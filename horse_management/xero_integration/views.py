"""
Views for Xero integration: OAuth flow, invoice push, settings.
"""

import logging
import secrets

from django.contrib import messages
from django.db.models import Q

from core.permissions import LEVEL_VIEW, feature_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from invoicing.models import Invoice

from .client import XeroAPIError, XeroClient, XeroTokenExpiredError, choose_tenant
from .models import XeroConnection, XeroContactMapping, XeroInvoiceSync
from .services import (
    XeroNotConnectedError,
    check_xero_invoice_status,
    push_invoice_to_xero,
)

logger = logging.getLogger(__name__)


@feature_required('xero')
def xero_settings(request):
    """Redirect to unified settings page."""
    return redirect('app_settings')


@feature_required('xero')
def xero_connect(request):
    """Initiate OAuth flow by redirecting to Xero."""
    state = secrets.token_urlsafe(32)
    conn = XeroConnection.get_connection()
    conn.oauth_state = state
    conn.save(update_fields=['oauth_state'])

    auth_url = XeroClient.get_authorization_url(state)
    return redirect(auth_url)


@feature_required('xero')
def xero_callback(request):
    """Handle OAuth callback from Xero."""
    error = request.GET.get('error')
    if error:
        messages.error(request, f'Xero authorization failed: {error}')
        return redirect('app_settings')

    code = request.GET.get('code')
    state = request.GET.get('state')

    if not code or not state:
        messages.error(request, 'Invalid callback from Xero (missing code or state).')
        return redirect('app_settings')

    # Validate state parameter
    conn = XeroConnection.get_connection()
    if not conn.oauth_state or conn.oauth_state != state:
        messages.error(request, 'Invalid state parameter. Please try connecting again.')
        return redirect('app_settings')

    # Clear state immediately
    conn.oauth_state = ''
    conn.save(update_fields=['oauth_state'])

    try:
        # Exchange code for tokens
        token_data = XeroClient.exchange_code_for_tokens(code)

        now = timezone.now()
        conn.access_token = token_data['access_token']
        conn.refresh_token = token_data['refresh_token']
        conn.token_expires_at = now + timezone.timedelta(
            seconds=token_data.get('expires_in', 1800)
        )

        # Fetch tenant connections
        tenants = XeroClient.get_tenant_connections(token_data['access_token'])
        if not tenants:
            messages.error(request, 'No Xero organisations found. Please ensure you have access to at least one organisation.')
            return redirect('app_settings')

        # The organisation chosen in this authorisation, not simply the
        # first in the list: the list also holds organisations connected
        # before and never removed in Xero.
        tenant = choose_tenant(token_data['access_token'], tenants)
        if tenant is None:
            messages.error(
                request,
                'More than one Xero organisation is connected to Yardway, and '
                'Xero did not say which one you chose. In Xero, open Settings '
                '→ Connected apps, remove Yardway from the organisations you '
                'do not want, then connect again.',
            )
            return redirect('app_settings')
        cleared = _clear_links_to_other_organisations(tenant['tenantId'])
        conn.xero_tenant_id = tenant['tenantId']
        conn.xero_tenant_name = tenant.get('tenantName', '')
        conn.connected_at = now
        conn.last_refreshed_at = now
        conn.is_active = True

        conn.save()
        messages.success(
            request,
            f'Connected to Xero organisation: {conn.xero_tenant_name}'
        )
        if cleared:
            messages.info(
                request,
                'This is a different Xero organisation from before. Links to '
                f'the old one were cleared: {cleared}. Push invoices again to '
                'send them to this organisation.',
            )

    except Exception:
        logger.exception('Xero OAuth callback failed')
        # The exception can carry Xero's response; the log has it, the
        # page says only what to do.
        messages.error(request, 'Could not connect to Xero. Please try again. If it fails again, check the server log.')

    return redirect('app_settings')


def _clear_links_to_other_organisations(tenant_id):
    """Drop contact links and push records that belong to another organisation.

    A contact ID or invoice ID from organisation A means nothing in B: a
    push with A's contact ID fails, and the nightly sweep polled A's
    invoices for ever. Push records are kept as "not pushed", with a note
    of where the invoice went, so the invoice can be pushed to B.
    Returns a short description of what was cleared, or ''.
    """
    from django.db.models import F

    other = ~Q(xero_tenant_id=tenant_id) & ~Q(xero_tenant_id='')
    contacts = XeroContactMapping.objects.filter(other).delete()[0]
    invoices = 0
    syncs = XeroInvoiceSync.objects.filter(other).exclude(
        sync_status=XeroInvoiceSync.SyncStatus.NOT_PUSHED,
    )
    for sync in syncs:
        note = (
            'Was in another Xero organisation'
            + (f' as {sync.xero_invoice_number}' if sync.xero_invoice_number else '')
            + ' before Xero was connected to a different organisation.'
        )
        XeroInvoiceSync.objects.filter(pk=sync.pk).update(
            sync_status=XeroInvoiceSync.SyncStatus.NOT_PUSHED,
            error_message=note,
            push_round=F('push_round') + 1,
        )
        invoices += 1
    parts = []
    if contacts:
        parts.append(f"{contacts} contact{'s' if contacts != 1 else ''}")
    if invoices:
        parts.append(f"{invoices} invoice{'s' if invoices != 1 else ''}")
    return ' and '.join(parts)


@feature_required('xero')
@require_POST
def xero_disconnect(request):
    """Disconnect from Xero, and remove Yardway's access in Xero too."""
    conn = XeroConnection.get_connection()
    # Without the revoke, the organisation stayed in Xero's connections
    # list, and the next connect could pick it again.
    revoked = XeroClient.revoke_refresh_token(conn.refresh_token)
    conn.access_token = ''
    conn.refresh_token = ''
    conn.token_expires_at = None
    conn.xero_tenant_id = ''
    conn.xero_tenant_name = ''
    conn.connected_at = None
    conn.last_refreshed_at = None
    conn.is_active = False
    conn.save()

    messages.success(request, 'Disconnected from Xero.')
    if not revoked:
        messages.warning(
            request,
            'Xero did not confirm that Yardway\'s access was removed. In Xero, '
            'open Settings → Connected apps and remove Yardway there.',
        )
    return redirect('app_settings')


@feature_required('xero')
@require_POST
def xero_push_invoice(request, pk):
    """Push an invoice to Xero."""
    invoice = get_object_or_404(Invoice, pk=pk)

    if invoice.status == Invoice.Status.CANCELLED:
        messages.warning(request, 'Cannot push a cancelled invoice to Xero.')
        return redirect('invoice_detail', pk=pk)

    try:
        sync = push_invoice_to_xero(invoice)
        if sync.sync_status == XeroInvoiceSync.SyncStatus.PUSHED:
            messages.success(
                request,
                f'Invoice {invoice.invoice_number} pushed to Xero as draft.'
            )
        else:
            messages.info(request, 'Invoice was already in Xero.')

    except XeroNotConnectedError:
        messages.error(request, 'Xero is not connected. Please connect first.')
    except XeroTokenExpiredError:
        messages.error(request, 'Xero session expired. Please reconnect.')
    except XeroAPIError as e:
        messages.error(request, f'Xero API error: {e}')
    except Exception:
        logger.exception('Unexpected error pushing invoice %s to Xero', invoice.invoice_number)
        messages.error(request, 'Failed to push the invoice to Xero. Check the server log, then try again.')

    return redirect('invoice_detail', pk=pk)


@feature_required('xero', LEVEL_VIEW)
def xero_invoice_status(request, pk):
    """HTMX endpoint: check and return current Xero status for an invoice.

    Read-only (was login-only before the Role Suite), so view level is
    enough — connect/disconnect/push stay at full.
    """
    sync = get_object_or_404(XeroInvoiceSync, invoice_id=pk)

    # Throttle: only check Xero if last check was > 5 minutes ago
    should_check = (
        sync.sync_status == XeroInvoiceSync.SyncStatus.PUSHED
        and sync.xero_invoice_id
        and (
            not sync.last_status_check_at
            or (timezone.now() - sync.last_status_check_at).total_seconds() > 300
        )
    )

    if should_check:
        try:
            sync = check_xero_invoice_status(sync)
        except Exception:
            # Show the stale status rather than erroring the page, but leave
            # a trace — a dead connection was previously invisible here.
            logger.warning(
                'Xero status check failed for sync %s', sync.pk, exc_info=True,
            )

    return render(request, 'xero_integration/partials/invoice_xero_status.html', {
        'sync': sync,
    })

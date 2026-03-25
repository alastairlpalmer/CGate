"""
Views for Xero integration: OAuth flow, invoice push, settings.
"""

import logging
import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Invoice

from .client import XeroAPIError, XeroClient, XeroTokenExpiredError
from .models import XeroConnection, XeroInvoiceSync
from .services import (
    XeroNotConnectedError,
    check_xero_invoice_status,
    push_invoice_to_xero,
)

logger = logging.getLogger(__name__)


@login_required
def xero_settings(request):
    """Xero integration settings page."""
    connection = XeroConnection.get_connection()
    return render(request, 'xero_integration/settings.html', {
        'connection': connection,
    })


@login_required
def xero_connect(request):
    """Initiate OAuth flow by redirecting to Xero."""
    state = secrets.token_urlsafe(32)
    conn = XeroConnection.get_connection()
    conn.oauth_state = state
    conn.save(update_fields=['oauth_state'])

    auth_url = XeroClient.get_authorization_url(state)
    return redirect(auth_url)


@login_required
def xero_callback(request):
    """Handle OAuth callback from Xero."""
    error = request.GET.get('error')
    if error:
        messages.error(request, f'Xero authorization failed: {error}')
        return redirect('xero_settings')

    code = request.GET.get('code')
    state = request.GET.get('state')

    if not code or not state:
        messages.error(request, 'Invalid callback from Xero (missing code or state).')
        return redirect('xero_settings')

    # Validate state parameter
    conn = XeroConnection.get_connection()
    if not conn.oauth_state or conn.oauth_state != state:
        messages.error(request, 'Invalid state parameter. Please try connecting again.')
        return redirect('xero_settings')

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
            return redirect('xero_settings')

        # Use first tenant (single-tenant app)
        tenant = tenants[0]
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

    except Exception as e:
        logger.exception('Xero OAuth callback failed')
        messages.error(request, f'Failed to connect to Xero: {e}')

    return redirect('xero_settings')


@login_required
@require_POST
def xero_disconnect(request):
    """Disconnect from Xero."""
    conn = XeroConnection.get_connection()
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
    return redirect('xero_settings')


@login_required
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
    except Exception as e:
        logger.exception('Unexpected error pushing invoice %s to Xero', invoice.invoice_number)
        messages.error(request, f'Failed to push invoice: {e}')

    return redirect('invoice_detail', pk=pk)


@login_required
def xero_invoice_status(request, pk):
    """HTMX endpoint: check and return current Xero status for an invoice."""
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
            # Silently fail — just show stale status
            pass

    return render(request, 'xero_integration/partials/invoice_xero_status.html', {
        'sync': sync,
    })

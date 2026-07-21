"""
Business logic for Xero integration.

Handles contact matching/creation and invoice push operations.
"""

import logging
from decimal import Decimal

from django.utils import timezone

from invoicing.utils import _parse_address_lines

from .client import XeroClient, XeroAPIError, XeroTokenExpiredError
from .models import XeroConnection, XeroContactMapping, XeroInvoiceSync

logger = logging.getLogger(__name__)


class XeroNotConnectedError(Exception):
    """Raised when Xero is not connected."""


class XeroContactService:
    """Manages the mapping between LivMan Owners and Xero Contacts."""

    @staticmethod
    def ensure_contact_exists(owner):
        """Get or create a Xero Contact for this Owner.

        Returns the xero_contact_id (str).
        """
        # 1. Check local mapping first
        try:
            mapping = XeroContactMapping.objects.get(owner=owner)
            return mapping.xero_contact_id
        except XeroContactMapping.DoesNotExist:
            pass

        client = XeroClient()

        # 2. Search Xero by name
        existing = client.find_contact_by_name(owner.name)
        if existing:
            XeroContactMapping.objects.create(
                owner=owner,
                xero_contact_id=existing['ContactID'],
                xero_contact_name=existing['Name'],
            )
            return existing['ContactID']

        # 3. Create new contact in Xero
        address_lines = _parse_address_lines(owner.address)
        contact_data = {
            'Name': owner.name,
            'EmailAddress': owner.email or '',
            'Phones': [],
            'Addresses': [{
                'AddressType': 'POBOX',
                'AddressLine1': address_lines[0],
                'AddressLine2': address_lines[1],
                'AddressLine3': address_lines[2],
                'AddressLine4': address_lines[3],
            }],
        }
        if owner.phone:
            contact_data['Phones'].append({
                'PhoneType': 'DEFAULT',
                'PhoneNumber': owner.phone,
            })

        created = client.create_contact(contact_data)
        XeroContactMapping.objects.create(
            owner=owner,
            xero_contact_id=created['ContactID'],
            xero_contact_name=created['Name'],
        )
        logger.info('Created Xero contact for %s: %s', owner.name, created['ContactID'])
        return created['ContactID']


def build_xero_invoice_payload(invoice, xero_contact_id):
    """Build Xero API invoice JSON from a LivMan invoice.

    Mirrors the field mapping from invoicing/utils.py:invoice_to_xero_rows
    but in Xero JSON API format instead of CSV.
    """
    # Tax type follows the invoice's snapshotted VAT rate so Xero's computed
    # total (net lines + VAT) always matches the invoice the owner saw.
    tax_type = 'OUTPUT2' if invoice.vat_rate > 0 else 'NONE'

    # The owner's account_code is a customer reference, not a GL code — it
    # goes in Reference below. Revenue always posts to the sales account,
    # matching the CSV export's *AccountCode.
    account_code = '200'

    line_items = []
    for item in invoice.line_items.select_related('horse', 'charge').order_by(
        'line_type', 'description'
    ):
        # Xero derives each line's amount as Quantity x UnitAmount. For livery
        # lines, quantity/unit_price are days/full-daily-rate, whose product is
        # the FULL charge — not this owner's (possibly fractional) share. The
        # correct amount owed is the already-split line_total, so push it as a
        # single unit to guarantee the Xero figure matches the invoice.
        line_total = item.line_total
        if line_total is None:
            line_total = (item.quantity * item.unit_price).quantize(Decimal('0.01'))
        line_items.append({
            'Description': item.description,
            'Quantity': '1',
            'UnitAmount': str(line_total),
            'AccountCode': account_code,
            'TaxType': tax_type,
        })

    return {
        'Type': 'ACCREC',
        'Contact': {'ContactID': xero_contact_id},
        'InvoiceNumber': invoice.invoice_number,
        'Reference': getattr(invoice.owner, 'account_code', '') or '',
        'Date': invoice.created_at.strftime('%Y-%m-%d'),
        'DueDate': invoice.due_date.strftime('%Y-%m-%d'),
        'LineItems': line_items,
        'CurrencyCode': 'GBP',
        'Status': 'DRAFT',
        'LineAmountTypes': 'Exclusive',
    }


def push_invoice_to_xero(invoice):
    """Push a single invoice to Xero.

    Concurrency-safe: the invoice row is locked for the duration of the
    push, so a double-click (or a manual push racing the nightly task)
    serialises — the loser re-reads the sync record and returns it instead
    of POSTing a duplicate. The POST itself carries a stable Idempotency-Key
    per invoice, so a retry after a network timeout returns Xero's original
    result instead of failing on a duplicate invoice number.

    Returns the XeroInvoiceSync record.
    Raises XeroNotConnectedError, XeroTokenExpiredError, or XeroAPIError.
    """
    from django.db import transaction

    from invoicing.models import Invoice

    conn = XeroConnection.get_connection()
    if not conn.is_connected:
        raise XeroNotConnectedError('Xero is not connected. Please connect first.')

    error = None
    existing_sync = None
    try:
        with transaction.atomic():
            # Serialise concurrent pushes of the same invoice.
            invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)

            existing_sync = XeroInvoiceSync.objects.filter(invoice=invoice).first()
            if existing_sync and existing_sync.sync_status == XeroInvoiceSync.SyncStatus.PUSHED:
                return existing_sync

            # 1. Ensure contact exists
            contact_id = XeroContactService.ensure_contact_exists(invoice.owner)

            # 2. Build payload
            payload = build_xero_invoice_payload(invoice, contact_id)

            # 3. Push to Xero (idempotent per invoice)
            client = XeroClient()
            xero_invoice = client.create_invoice(
                payload, idempotency_key=f'yardway-invoice-{invoice.pk}',
            )

            # 4. Record sync state
            now = timezone.now()
            if existing_sync:
                existing_sync.xero_invoice_id = xero_invoice['InvoiceID']
                existing_sync.xero_invoice_number = xero_invoice.get('InvoiceNumber', '')
                existing_sync.sync_status = XeroInvoiceSync.SyncStatus.PUSHED
                existing_sync.last_pushed_at = now
                existing_sync.error_message = ''
                existing_sync.save()
                return existing_sync

            return XeroInvoiceSync.objects.create(
                invoice=invoice,
                xero_invoice_id=xero_invoice['InvoiceID'],
                xero_invoice_number=xero_invoice.get('InvoiceNumber', ''),
                sync_status=XeroInvoiceSync.SyncStatus.PUSHED,
                last_pushed_at=now,
            )
    except (XeroAPIError, XeroTokenExpiredError) as e:
        error = e

    # Record the error outside the atomic block — an exception raised inside
    # would roll the sync record back with everything else.
    if existing_sync:
        existing_sync.sync_status = XeroInvoiceSync.SyncStatus.ERROR
        existing_sync.error_message = str(error)
        existing_sync.save()
    else:
        XeroInvoiceSync.objects.get_or_create(
            invoice=invoice,
            defaults={
                'sync_status': XeroInvoiceSync.SyncStatus.ERROR,
                'error_message': str(error),
            },
        )
    raise error


def check_xero_invoice_status(sync):
    """Check Xero for updated invoice status.

    Updates sync record and local invoice if Xero reports PAID.
    Returns the updated sync record.
    """
    from invoicing.models import Invoice

    client = XeroClient()
    xero_invoice = client.get_invoice(sync.xero_invoice_id)

    xero_status = xero_invoice.get('Status', '')
    now = timezone.now()
    sync.last_status_check_at = now

    if xero_status == 'PAID':
        sync.sync_status = XeroInvoiceSync.SyncStatus.PAID_IN_XERO
        # Update local invoice if not already paid
        invoice = sync.invoice
        if invoice.status in (Invoice.Status.SENT, Invoice.Status.OVERDUE, Invoice.Status.DRAFT):
            invoice.mark_as_paid(method='xero', reference='Reported paid by Xero')
    elif xero_status == 'AUTHORISED':
        sync.sync_status = XeroInvoiceSync.SyncStatus.PUSHED
    elif xero_status == 'VOIDED':
        sync.sync_status = XeroInvoiceSync.SyncStatus.ERROR
        sync.error_message = 'Invoice voided in Xero'

    sync.save()
    return sync

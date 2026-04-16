"""
Business logic for Xero integration.

Handles contact matching/creation and invoice push operations.
"""

import logging

from django.utils import timezone

from core.models import BusinessSettings
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
    biz_settings = BusinessSettings.get_settings()
    vat_reg = getattr(biz_settings, 'vat_registration', 'N/A') or 'N/A'
    vat_rate = getattr(biz_settings, 'vat_rate', 0) or 0

    # No VAT if not registered or rate is zero
    if vat_reg.upper() in ('N/A', '', 'NONE') or not vat_rate:
        tax_type = 'NONE'
    else:
        tax_type = 'OUTPUT2'  # 20% VAT on Income

    account_code = getattr(invoice.owner, 'account_code', '') or '200'

    line_items = []
    for item in invoice.line_items.select_related('horse', 'charge').order_by(
        'line_type', 'description'
    ):
        line_items.append({
            'Description': item.description,
            'Quantity': str(item.quantity),
            'UnitAmount': str(item.unit_price),
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
        'Status': biz_settings.xero_invoice_status,
        'LineAmountTypes': 'Exclusive',
    }


def push_invoice_to_xero(invoice):
    """Push a single invoice to Xero.

    Returns the XeroInvoiceSync record.
    Raises XeroNotConnectedError, XeroTokenExpiredError, or XeroAPIError.
    """
    conn = XeroConnection.get_connection()
    if not conn.is_connected:
        raise XeroNotConnectedError('Xero is not connected. Please connect first.')

    # Check if already pushed
    try:
        existing_sync = XeroInvoiceSync.objects.get(invoice=invoice)
        if existing_sync.sync_status == XeroInvoiceSync.SyncStatus.PUSHED:
            return existing_sync
    except XeroInvoiceSync.DoesNotExist:
        existing_sync = None

    try:
        # 1. Ensure contact exists
        contact_id = XeroContactService.ensure_contact_exists(invoice.owner)

        # 2. Build payload
        payload = build_xero_invoice_payload(invoice, contact_id)

        # 3. Push to Xero
        client = XeroClient()
        xero_invoice = client.create_invoice(payload)

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
        # Record the error
        if existing_sync:
            existing_sync.sync_status = XeroInvoiceSync.SyncStatus.ERROR
            existing_sync.error_message = str(e)
            existing_sync.save()
        else:
            XeroInvoiceSync.objects.create(
                invoice=invoice,
                sync_status=XeroInvoiceSync.SyncStatus.ERROR,
                error_message=str(e),
            )
        raise


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
            invoice.mark_as_paid()
    elif xero_status == 'AUTHORISED':
        sync.sync_status = XeroInvoiceSync.SyncStatus.PUSHED
    elif xero_status == 'VOIDED':
        sync.sync_status = XeroInvoiceSync.SyncStatus.ERROR
        sync.error_message = 'Invoice voided in Xero'

    sync.save()
    return sync

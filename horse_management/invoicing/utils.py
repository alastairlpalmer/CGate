"""
Utility functions for invoice formatting and grouping.
"""

import csv
import io
from collections import OrderedDict
from decimal import Decimal


def _format_date_win(d, include_year=False):
    """Windows-compatible date formatting (no %-d support)."""
    day = d.day
    month_abbr = d.strftime('%b')
    if include_year:
        return f"{day} {month_abbr} {d.year}"
    return f"{day} {month_abbr}"


def format_date_short(d):
    """Format date as '6 Nov' (no leading zero, no year)."""
    if not hasattr(d, 'strftime'):
        return str(d)
    return _format_date_win(d, include_year=False)


def format_date_short_year(d):
    """Format date as '3 Jan 2026' (no leading zero, with year)."""
    if not hasattr(d, 'strftime'):
        return str(d)
    return _format_date_win(d, include_year=True)


def group_line_items_by_horse(line_items):
    """Group InvoiceLineItem queryset by horse.

    Returns list of dicts:
        {
            'horse': Horse instance (or None),
            'horse_name': str,
            'items': [item, ...] (livery first, then extras sorted by date),
            'subtotal': Decimal,
        }
    """
    groups = OrderedDict()

    for item in line_items:
        horse_id = item.horse_id
        if horse_id not in groups:
            groups[horse_id] = {
                'horse': item.horse,
                'horse_name': item.horse.name if item.horse else 'Other Charges',
                'items': [],
                'subtotal': Decimal('0.00'),
            }
        groups[horse_id]['items'].append(item)
        groups[horse_id]['subtotal'] += item.line_total

    # Sort items within each group: livery first, then extras by charge date
    for group in groups.values():
        livery = [i for i in group['items'] if i.line_type == 'livery']
        extras = [i for i in group['items'] if i.line_type != 'livery']
        # Sort extras by charge date if available
        extras.sort(key=lambda i: i.charge.date if i.charge else i.pk)
        group['items'] = livery + extras

    return list(groups.values())


def group_preview_charges_by_horse(all_charges):
    """Group preview charge dicts by horse for preview template.

    Returns list of dicts:
        {
            'horse': Horse instance,
            'horse_name': str,
            'charges': [charge_dict, ...],
            'subtotal': Decimal,
        }
    """
    groups = OrderedDict()

    for charge in all_charges:
        horse = charge.get('horse')
        horse_id = horse.pk if horse else None
        if horse_id not in groups:
            groups[horse_id] = {
                'horse': horse,
                'horse_name': horse.name if horse else 'Other Charges',
                'charges': [],
                'subtotal': Decimal('0.00'),
            }
        groups[horse_id]['charges'].append(charge)
        groups[horse_id]['subtotal'] += charge['amount']

    return list(groups.values())


XERO_CSV_HEADERS = [
    '*ContactName', 'EmailAddress',
    'POAddressLine1', 'POAddressLine2', 'POAddressLine3', 'POAddressLine4',
    'POCity', 'PORegion', 'POPostalCode', 'POCountry',
    '*InvoiceNumber', 'Reference',
    '*InvoiceDate', '*DueDate', 'Total',
    'InventoryItemCode', '*Description', '*Quantity', '*UnitAmount',
    'Discount', '*AccountCode', '*TaxType', 'TaxAmount',
    'TrackingName1', 'TrackingOption1', 'TrackingName2', 'TrackingOption2',
    'Currency', 'BrandingTheme',
]


def _parse_address_lines(address_text):
    """Split an address into up to 4 lines."""
    if not address_text:
        return ['', '', '', '']
    lines = [l.strip() for l in address_text.strip().split('\n') if l.strip()]
    while len(lines) < 4:
        lines.append('')
    return lines[:4]


def invoice_to_xero_rows(invoice, account_code='200'):
    """Convert an invoice to Xero-compatible CSV rows.

    Returns a list of dicts, one per line item. Contact/invoice metadata
    appears on the first row only.
    """
    # Tax type follows the invoice's snapshotted VAT rate, so what Xero adds
    # on import always matches what the PDF/detail page showed the owner.
    # Line totals are net; Xero applies the VAT itself.
    tax_type = '20% (VAT on Income)' if invoice.vat_rate > 0 else 'No VAT'

    address_lines = _parse_address_lines(invoice.owner.address)

    line_items = list(
        invoice.line_items.select_related('horse', 'charge')
        .order_by('line_type', 'description')
    )

    # Xero rounds VAT per line on import; the app computes it once on the
    # subtotal, so multi-line invoices with odd pennies can disagree with the
    # stated Total by 1p (and Xero flags the row). Export explicit per-line
    # TaxAmounts that sum exactly to the invoice's VAT.
    tax_amounts = {}
    if invoice.vat_rate > 0 and line_items:
        rate = invoice.vat_rate / Decimal('100')
        allocated = Decimal('0.00')
        for item in line_items[:-1]:
            net = item.line_total
            if net is None:
                net = (item.quantity * item.unit_price).quantize(Decimal('0.01'))
            tax = (net * rate).quantize(Decimal('0.01'))
            tax_amounts[item.pk] = tax
            allocated += tax
        tax_amounts[line_items[-1].pk] = invoice.vat_amount - allocated

    rows = []

    for idx, item in enumerate(line_items):
        row = {h: '' for h in XERO_CSV_HEADERS}

        if idx == 0:
            row['*ContactName'] = invoice.owner.name
            row['EmailAddress'] = invoice.owner.email or ''
            row['POAddressLine1'] = address_lines[0]
            row['POAddressLine2'] = address_lines[1]
            row['POAddressLine3'] = address_lines[2]
            row['POAddressLine4'] = address_lines[3]
            row['*InvoiceNumber'] = invoice.invoice_number
            row['Reference'] = getattr(invoice.owner, 'account_code', '') or ''
            row['*InvoiceDate'] = invoice.created_at.strftime('%d/%m/%Y')
            row['*DueDate'] = invoice.due_date.strftime('%d/%m/%Y')
            row['Total'] = str(invoice.total)

        row['*Description'] = item.description
        # Xero derives each line's amount as Quantity x UnitAmount. For livery
        # lines, quantity/unit_price are days/full-daily-rate, whose product is
        # the FULL charge — not this owner's (possibly fractional) share. The
        # correct amount owed is the already-split line_total, so export it as a
        # single unit to guarantee the exported figure matches the invoice.
        line_total = item.line_total
        if line_total is None:
            line_total = (item.quantity * item.unit_price).quantize(Decimal('0.01'))
        row['*Quantity'] = '1'
        row['*UnitAmount'] = str(line_total)
        row['*AccountCode'] = account_code
        row['*TaxType'] = tax_type
        if item.pk in tax_amounts:
            row['TaxAmount'] = str(tax_amounts[item.pk])
        row['Currency'] = 'GBP'

        rows.append(row)

    return rows


def write_xero_csv(invoices, output):
    """Write Xero-compatible CSV for one or more invoices to a file-like object."""
    writer = csv.DictWriter(output, fieldnames=XERO_CSV_HEADERS)
    writer.writeheader()

    if not hasattr(invoices, '__iter__'):
        invoices = [invoices]

    for invoice in invoices:
        rows = invoice_to_xero_rows(invoice)
        for row in rows:
            writer.writerow(row)

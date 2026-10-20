"""
PDF generation for invoices.
"""

import io
import logging
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from django.template.loader import render_to_string
from django.utils import timezone

from core.models import BusinessSettings

from .utils import group_line_items_by_horse


FONT_DIR = Path(__file__).resolve().parent / 'fonts'
FONT = 'DejaVuSans'
FONT_BOLD = 'DejaVuSans-Bold'
_REPLACEMENT = '?'


@lru_cache(maxsize=1)
def _font_glyphs():
    """Register DejaVu Sans once; return the set of characters it can draw.

    Helvetica (ReportLab's default) has no Polish, Czech, Greek, Cyrillic,
    Arabic or Hebrew letters and drew them as black boxes on the invoice.
    DejaVu Sans has all of these (invoicing/fonts/README.md). Arabic and
    Hebrew print letter by letter, left to right: ReportLab does not join
    or reorder right-to-left text.
    """
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular = TTFont(FONT, str(FONT_DIR / 'DejaVuSans.ttf'))
    pdfmetrics.registerFont(regular)
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONT_DIR / 'DejaVuSans-Bold.ttf')))
    # <b> and <i> inside a Paragraph pick the face from this family. There
    # is no italic face, so italic uses the upright one.
    addMapping(FONT, 0, 0, FONT)
    addMapping(FONT, 1, 0, FONT_BOLD)
    addMapping(FONT, 0, 1, FONT)
    addMapping(FONT, 1, 1, FONT_BOLD)
    return frozenset(regular.face.charToGlyph)


def _drawable(text):
    """Swap characters the font cannot draw (Chinese, some emoji) for '?'.

    Otherwise ReportLab draws an empty box, and nobody can tell what was
    there or that something was there at all.
    """
    glyphs = _font_glyphs()
    return ''.join(ch if ord(ch) in glyphs or ch in '\n\r\t' else _REPLACEMENT for ch in text)


def _styles():
    """The ReportLab sample styles, moved onto DejaVu Sans."""
    from reportlab.lib.styles import getSampleStyleSheet

    _font_glyphs()
    sheet = getSampleStyleSheet()
    for style in sheet.byName.values():
        name = getattr(style, 'fontName', '')
        if name.startswith('Helvetica'):
            style.fontName = FONT_BOLD if 'Bold' in name else FONT
    return sheet


def _esc(value):
    """Escape free text for a ReportLab Paragraph.

    Paragraph parses its string as XML-ish markup, so a stray '<' or '&' in
    an owner's address, a charge description or the invoice notes raised
    ValueError — which the email path swallowed, sending the invoice email
    with no PDF attached.
    """
    return _xml_escape(_drawable(str(value if value is not None else '')))


def _esc_lines(value):
    """Escape multi-line text and turn newlines into <br/>."""
    return '<br/>'.join(_esc(line) for line in str(value or '').split('\n'))

logger = logging.getLogger(__name__)


def generate_invoice_pdf(invoice):
    """Generate a PDF for an invoice using WeasyPrint."""
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:
        logger.info("WeasyPrint unavailable (%s), using ReportLab fallback.", e)
        return generate_invoice_pdf_reportlab(invoice)

    settings = BusinessSettings.get_settings()

    line_items = invoice.line_items.select_related(
        'horse', 'placement', 'charge'
    ).order_by('line_type', 'description')
    horse_groups = group_line_items_by_horse(line_items)

    html_content = render_to_string('invoicing/invoice_pdf.html', {
        'invoice': invoice,
        'settings': settings,
        'line_items': line_items,
        'horse_groups': horse_groups,
    })

    pdf_file = io.BytesIO()
    HTML(string=html_content).write_pdf(pdf_file)
    pdf_file.seek(0)

    return pdf_file


def generate_invoice_pdf_reportlab(invoice):
    """Generate a PDF using ReportLab as fallback."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    settings = BusinessSettings.get_settings()
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20*mm,
        leftMargin=20*mm,
        topMargin=15*mm,
        bottomMargin=15*mm
    )

    styles = _styles()
    title_style = ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Heading1'],
        fontSize=22,
        spaceAfter=6
    )
    heading_style = ParagraphStyle(
        'InvoiceHeading',
        parent=styles['Heading2'],
        fontSize=14,
        spaceAfter=6
    )
    normal_style = styles['Normal']
    small_style = ParagraphStyle(
        'Small',
        parent=normal_style,
        fontSize=9,
        textColor=colors.Color(0.4, 0.4, 0.4),
    )
    item_style = ParagraphStyle(
        'Item',
        parent=normal_style,
        fontSize=9,
    )
    indent_style = ParagraphStyle(
        'IndentItem',
        parent=normal_style,
        fontSize=8,
        leftIndent=15,
        textColor=colors.Color(0.35, 0.35, 0.35),
    )

    elements = []

    # Header
    elements.append(Paragraph(_esc(settings.business_name), title_style))
    header_parts = []
    if settings.address:
        header_parts.append(_esc_lines(settings.address))
    if settings.phone:
        header_parts.append(f"Tel: {_esc(settings.phone)}")
    if settings.email:
        header_parts.append(_esc(settings.email))
    if settings.website:
        header_parts.append(_esc(settings.website))
    if header_parts:
        elements.append(Paragraph('<br/>'.join(header_parts), small_style))

    elements.append(Spacer(1, 10*mm))

    # Invoice title
    elements.append(Paragraph("<b>INVOICE</b>", heading_style))

    # Two-column layout: Bill To + Invoice box
    bill_to_parts = [f"<b>{_esc(invoice.owner.name)}</b>"]
    if invoice.owner.address:
        bill_to_parts.append(_esc_lines(invoice.owner.address))
    bill_to_text = '<br/>'.join(bill_to_parts)

    invoice_info_lines = [
        f"Invoice No: {invoice.invoice_number}",
        f"Date: {invoice.created_at.strftime('%d/%m/%Y')}",
    ]
    if invoice.customer_reference:
        invoice_info_lines.append(f"Your ref: {_esc(invoice.customer_reference)}")
    if hasattr(invoice.owner, 'account_code') and invoice.owner.account_code:
        invoice_info_lines.append(f"Account: {invoice.owner.account_code}")
    # Only a real VAT number: "VAT Reg: N/A" on the invoice of a yard that
    # is not registered reads like a missing detail.
    vat_reg = (getattr(settings, 'vat_registration', '') or '').strip()
    if vat_reg and vat_reg.upper() not in ('N/A', 'NA', 'NONE', '-'):
        invoice_info_lines.append(f"VAT Reg: {vat_reg}")
    invoice_info_lines.append(
        f"Period: {invoice.period_start.strftime('%d/%m/%Y')} - {invoice.period_end.strftime('%d/%m/%Y')}"
    )
    invoice_info_lines.append(f"Due Date: {invoice.due_date.strftime('%d/%m/%Y')}")
    invoice_info_text = '<br/>'.join(invoice_info_lines)

    meta_table = Table(
        [[Paragraph(bill_to_text, normal_style), Paragraph(invoice_info_text, small_style)]],
        colWidths=[95*mm, 75*mm]
    )
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (1, 0), (1, 0), 0.5, colors.grey),
        ('TOPPADDING', (1, 0), (1, 0), 6),
        ('BOTTOMPADDING', (1, 0), (1, 0), 6),
        ('LEFTPADDING', (1, 0), (1, 0), 8),
        ('RIGHTPADDING', (1, 0), (1, 0), 8),
    ]))
    elements.append(meta_table)

    elements.append(Spacer(1, 8*mm))

    # Build items table with horse grouping
    line_items = invoice.line_items.select_related(
        'horse', 'placement', 'charge'
    ).order_by('line_type', 'description')
    horse_groups = group_line_items_by_horse(line_items)

    # Table header
    table_data = [
        [
            Paragraph('<b>Item</b>', item_style),
            Paragraph('<b>Amount</b>', item_style),
        ]
    ]
    row_styles = []

    for group in horse_groups:
        # Only label the group with a share % when every line in it carries
        # the same fractional share. A 50%-share livery line plus a direct
        # (100%-billed) vet charge used to render the whole group as
        # "(50% share)", implying the vet line was half of a larger bill —
        # mixed groups rely on the per-line share notes instead.
        shares = {item.share_percentage for item in group['items']}
        share_label = ""
        if len(shares) == 1:
            share_pct = shares.pop()
            if share_pct < 100:
                share_label = f" ({share_pct:g}% share)"
        # Horse header row
        table_data.append([
            Paragraph(f"<b>{_esc(group['horse_name'])}{share_label}</b>", item_style),
            '',
        ])
        row_idx = len(table_data) - 1
        row_styles.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.Color(0.95, 0.95, 0.95)))
        row_styles.append(('SPAN', (0, row_idx), (-1, row_idx)))

        for item in group['items']:
            if item.line_type == 'livery':
                desc = Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;{_esc(item.description)}", item_style)
            else:
                date_prefix = ''
                if item.charge:
                    date_prefix = f"{item.charge.date.strftime('%d/%m/%Y')}: "
                desc = Paragraph(
                    f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{date_prefix}{_esc(item.description)}",
                    indent_style
                )

            table_data.append([
                desc,
                Paragraph(f"\u00a3{item.line_total:.2f}", item_style),
            ])

    table = Table(table_data, colWidths=[140*mm, 30*mm])

    base_styles = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
        ('FONTNAME', (0, 0), (-1, 0), FONT_BOLD),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.grey),
        ('TOPPADDING', (0, 1), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 3),
    ]
    base_styles.extend(row_styles)
    table.setStyle(TableStyle(base_styles))
    elements.append(table)

    elements.append(Spacer(1, 8*mm))

    # Totals \u2014 when part-paid, show the running balance so the PDF asks for
    # what's actually owed rather than the full face value.
    amount_paid = invoice.amount_paid
    vat_label = 'VAT:' if invoice.vat_rate == 0 else f"VAT ({invoice.vat_rate:g}%):"
    totals_data = [
        ['Net Total:', f"\u00a3{invoice.subtotal:.2f}"],
        [vat_label, f"\u00a3{invoice.vat_amount:.2f}"],
    ]
    if amount_paid > 0:
        credited = invoice.amount_credited
        totals_data.append(['Invoice Total:', f"\u00a3{invoice.total:.2f}"])
        if amount_paid - credited > 0:
            totals_data.append(['Paid to Date:', f"-\u00a3{amount_paid - credited:.2f}"])
        if credited > 0:
            totals_data.append(['Credit Notes:', f"-\u00a3{credited:.2f}"])
        totals_data.append(['Balance Due:', f"\u00a3{invoice.balance_due:.2f}"])
    else:
        totals_data.append(['Amount Due:', f"\u00a3{invoice.total:.2f}"])
    totals_table = Table(totals_data, colWidths=[30*mm, 30*mm])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, -1), FONT),
        ('FONTNAME', (0, -1), (-1, -1), FONT_BOLD),
        ('FONTSIZE', (0, -1), (-1, -1), 12),
        ('LINEABOVE', (0, -1), (-1, -1), 1.5, colors.black),
        ('TOPPADDING', (0, -1), (-1, -1), 6),
    ]))
    # Right-align totals table
    wrapper = Table([[totals_table]], colWidths=[170*mm])
    wrapper.setStyle(TableStyle([('ALIGN', (0, 0), (0, 0), 'RIGHT')]))
    elements.append(wrapper)

    elements.append(Spacer(1, 10*mm))

    # Payment details
    if settings.bank_details:
        elements.append(Paragraph("<b>Payment Details:</b>", normal_style))
        elements.append(Paragraph(_esc_lines(settings.bank_details), small_style))
        if settings.card_payment_url:
            elements.append(Paragraph(f"Or pay by card: {_esc(settings.card_payment_url)}", small_style))

    # Notes
    if invoice.notes:
        elements.append(Spacer(1, 6*mm))
        elements.append(Paragraph("<b>Notes:</b>", normal_style))
        elements.append(Paragraph(_esc_lines(invoice.notes), normal_style))

    doc.build(elements)
    buffer.seek(0)

    return buffer


def generate_owner_statement_pdf(owner, statement):
    """Generate a statement-of-account PDF for an owner (ReportLab)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    settings = BusinessSettings.get_settings()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=20*mm, leftMargin=20*mm, topMargin=15*mm, bottomMargin=15*mm,
    )

    styles = _styles()
    title_style = ParagraphStyle(
        'StatementTitle', parent=styles['Heading1'], fontSize=20, spaceAfter=4,
    )
    normal_style = styles['Normal']
    small_style = ParagraphStyle(
        'Small', parent=normal_style, fontSize=9,
        textColor=colors.Color(0.4, 0.4, 0.4),
    )

    elements = [
        Paragraph('Statement of Account', title_style),
        Paragraph(_esc(settings.business_name), normal_style),
        Spacer(1, 4*mm),
        Paragraph(f"<b>{_esc(owner.name)}</b>", normal_style),
    ]
    if owner.address:
        for line in owner.address.strip().split('\n'):
            elements.append(Paragraph(_esc(line.strip()), small_style))
    elements.append(Paragraph(
        f"Issued {timezone.localdate():%d %B %Y}", small_style
    ))
    elements.append(Spacer(1, 8*mm))

    table_data = [['Invoice', 'Issued', 'Period', 'Total', 'Paid', 'Balance']]
    for row in statement['rows']:
        invoice = row['invoice']
        table_data.append([
            invoice.invoice_number,
            f"{invoice.created_at:%d/%m/%Y}",
            f"{invoice.period_start:%d/%m/%y} – {invoice.period_end:%d/%m/%y}",
            f"£{invoice.total:.2f}",
            f"£{row['paid']:.2f}",
            f"£{row['balance']:.2f}",
        ])
    totals = statement['totals']
    table_data.append([
        '', '', 'Totals:',
        f"£{totals['invoiced']:.2f}",
        f"£{totals['paid']:.2f}",
        f"£{totals['balance']:.2f}",
    ])

    table = Table(
        table_data,
        colWidths=[28*mm, 24*mm, 46*mm, 24*mm, 24*mm, 24*mm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT),
        ('FONTNAME', (0, 0), (-1, 0), FONT_BOLD),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
        ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, -1), (-1, -1), FONT_BOLD),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
        ('TOPPADDING', (0, 1), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 3),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 8*mm))
    elements.append(Paragraph(
        f"<b>Balance outstanding: £{totals['balance']:.2f}</b>", normal_style
    ))

    if settings.bank_details:
        elements.append(Spacer(1, 6*mm))
        elements.append(Paragraph('<b>Payment Details:</b>', normal_style))
        # _esc_lines, not a bare newline swap: Paragraph parses its string as
        # markup, so an unclosed tag in the bank details raised ValueError —
        # a 500 on this page, and a statement email sent with no PDF while
        # still reporting success. The invoice PDF above escapes the same
        # field; this one was missed.
        elements.append(Paragraph(_esc_lines(settings.bank_details), small_style))

    doc.build(elements)
    buffer.seek(0)
    return buffer


def generate_credit_note_pdf(credit):
    """A one-page credit note (ReportLab): number, the invoice it credits,
    the amount and the reason. ``credit`` is a Payment of method
    CREDIT_NOTE."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    settings = BusinessSettings.get_settings()
    invoice = credit.invoice
    owner = invoice.owner
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm,
                            topMargin=15*mm, bottomMargin=15*mm)
    styles = _styles()
    title_style = ParagraphStyle('CNTitle', parent=styles['Heading1'], fontSize=20, spaceAfter=4)
    normal = styles['Normal']

    elements = [Paragraph(_esc(settings.business_name), title_style)]
    if settings.address:
        elements.append(Paragraph(_esc_lines(settings.address), normal))
    elements.append(Spacer(1, 8*mm))
    elements.append(Paragraph('<b>CREDIT NOTE</b>', styles['Heading2']))
    if credit.is_voided:
        elements.append(Paragraph('<b>VOID</b>: this credit note was cancelled.', normal))
    bill_to = f"<b>{_esc(owner.name)}</b>"
    if owner.address:
        bill_to += '<br/>' + _esc_lines(owner.address)
    info = [
        f"Credit note No: {_esc(credit.credit_note_number)}",
        f"Date: {credit.date.strftime('%d/%m/%Y')}",
        f"Against invoice: {_esc(invoice.invoice_number)}",
    ]
    if invoice.customer_reference:
        info.append(f"Your ref: {_esc(invoice.customer_reference)}")
    meta = Table([[Paragraph(bill_to, normal), Paragraph('<br/>'.join(info), normal)]], colWidths=[85*mm, 85*mm])
    meta.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    elements += [meta, Spacer(1, 8*mm)]
    lines = Table(
        [['Reason', 'Amount'], [Paragraph(_esc(credit.notes or ''), normal), f"£{credit.amount:.2f}"]],
        colWidths=[130*mm, 40*mm],
    )
    lines.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, (0.6, 0.6, 0.6)),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTNAME', (0, 0), (-1, 0), FONT_BOLD),
    ]))
    elements += [lines, Spacer(1, 6*mm)]
    elements.append(Paragraph(
        f"This credit note reduces the amount due on invoice {_esc(invoice.invoice_number)} "
        f"by £{credit.amount:.2f}.", normal,
    ))
    doc.build(elements)
    buffer.seek(0)
    return buffer

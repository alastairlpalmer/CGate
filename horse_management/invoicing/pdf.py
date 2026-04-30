"""
PDF generation for invoices.
"""

import io
from decimal import Decimal

from core.models import BusinessSettings

from .utils import group_line_items_by_horse


def generate_invoice_pdf(invoice):
    """Generate a PDF for an invoice using ReportLab."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
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

    styles = getSampleStyleSheet()
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
    bold_style = ParagraphStyle(
        'Bold',
        parent=normal_style,
        fontName='Helvetica-Bold',
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
    elements.append(Paragraph(settings.business_name, title_style))
    header_parts = []
    if settings.address:
        header_parts.append(settings.address.replace('\n', '<br/>'))
    if settings.phone:
        header_parts.append(f"Tel: {settings.phone}")
    if settings.email:
        header_parts.append(settings.email)
    if settings.website:
        header_parts.append(settings.website)
    if header_parts:
        elements.append(Paragraph('<br/>'.join(header_parts), small_style))

    elements.append(Spacer(1, 10*mm))

    # Invoice title
    elements.append(Paragraph("<b>INVOICE</b>", heading_style))

    # Two-column layout: Bill To + Invoice box
    bill_to_parts = [f"<b>{invoice.owner.name}</b>"]
    if invoice.owner.address:
        bill_to_parts.append(invoice.owner.address.replace('\n', '<br/>'))
    bill_to_text = '<br/>'.join(bill_to_parts)

    invoice_info_lines = [
        f"Invoice No: {invoice.invoice_number}",
        f"Date: {invoice.created_at.strftime('%d/%m/%Y')}",
    ]
    if hasattr(invoice.owner, 'account_code') and invoice.owner.account_code:
        invoice_info_lines.append(f"Account: {invoice.owner.account_code}")
    vat_reg = getattr(settings, 'vat_registration', 'N/A') or 'N/A'
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
        # Determine ownership % from first line item's share_percentage
        share_pct = group['items'][0].share_percentage if group['items'] else Decimal('100')
        share_label = f" ({share_pct:g}% share)" if share_pct < 100 else ""
        # Horse header row
        table_data.append([
            Paragraph(f"<b>{group['horse_name']}{share_label}</b>", item_style),
            '',
        ])
        row_idx = len(table_data) - 1
        row_styles.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.Color(0.95, 0.95, 0.95)))
        row_styles.append(('SPAN', (0, row_idx), (-1, row_idx)))

        for item in group['items']:
            if item.line_type == 'livery':
                desc = Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;{item.description}", item_style)
            else:
                date_prefix = ''
                if item.charge:
                    date_prefix = f"{item.charge.date.strftime('%d/%m/%Y')}: "
                desc = Paragraph(
                    f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{date_prefix}{item.description}",
                    indent_style
                )

            table_data.append([
                desc,
                Paragraph(f"\u00a3{item.line_total:.2f}", item_style),
            ])

    table = Table(table_data, colWidths=[140*mm, 30*mm])

    base_styles = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
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

    # Totals
    totals_data = [
        ['Net Total:', f"\u00a3{invoice.subtotal:.2f}"],
        ['VAT:', '\u00a30.00'],
        ['Amount Due:', f"\u00a3{invoice.total:.2f}"],
    ]
    totals_table = Table(totals_data, colWidths=[30*mm, 30*mm])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
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
        elements.append(Paragraph(settings.bank_details.replace('\n', '<br/>'), small_style))
        if settings.card_payment_url:
            elements.append(Paragraph(f"Or pay by card: {settings.card_payment_url}", small_style))

    # Notes
    if invoice.notes:
        elements.append(Spacer(1, 6*mm))
        elements.append(Paragraph("<b>Notes:</b>", normal_style))
        elements.append(Paragraph(invoice.notes, normal_style))

    doc.build(elements)
    buffer.seek(0)

    return buffer

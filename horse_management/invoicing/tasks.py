"""
Celery tasks for automated invoicing.
"""

import logging
from datetime import date, timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def generate_monthly_draft_invoices():
    """Generate draft invoices for the month that has just ended.

    Scheduled for the 1st of each month via Celery Beat. Uses the same
    duplicate-safe generation as the "Generate Monthly" button, so a manual
    run before or after this task cannot double-bill — owners who already
    have an invoice for the period are skipped. Only drafts are created;
    sending remains a manual, reviewed step.

    Can be switched off in Settings (auto_generate_invoices).
    """
    from core.models import BusinessSettings
    from invoicing.services import InvoiceService

    settings_obj = BusinessSettings.get_settings()
    if not settings_obj.auto_generate_invoices:
        logger.info("Monthly invoice auto-generation is disabled in settings.")
        return "disabled"

    today = timezone.localdate()
    last_month_end = today.replace(day=1) - timedelta(days=1)

    invoices, skipped = InvoiceService.generate_monthly_invoices(
        last_month_end.year, last_month_end.month
    )
    summary = (
        f"Generated {len(invoices)} draft invoice(s) for "
        f"{last_month_end:%B %Y}; skipped {len(skipped)} already invoiced."
    )
    logger.info(summary)
    return summary


# A queued send that never ran (worker restarted mid-task, lost message)
# would otherwise hold its claim forever and the invoice could never be
# re-sent from the list. After this long the claim is treated as stale.
SEND_CLAIM_TIMEOUT = timedelta(minutes=15)


def claim_invoice_for_sending(invoice_pk):
    """Atomically mark a draft invoice as queued for sending.

    Returns True when this caller won the claim. Another caller (a
    double-click, a second tab) sees False and must not enqueue again.
    """
    from django.db.models import Q

    from invoicing.models import Invoice

    now = timezone.now()
    stale = now - SEND_CLAIM_TIMEOUT
    updated = Invoice.objects.filter(
        pk=invoice_pk,
        status=Invoice.Status.DRAFT,
    ).filter(
        Q(send_queued_at__isnull=True) | Q(send_queued_at__lt=stale)
    ).update(send_queued_at=now, send_error='')
    return updated == 1


@shared_task
def send_invoice_email_task(invoice_pk):
    """Email one claimed invoice and mark it sent.

    Runs after claim_invoice_for_sending. Outcomes: 'sent', 'failed'
    (claim released, reason stored in send_error, invoice stays draft so
    it remains in the work queue), or 'skipped' (no claim / not a draft
    any more / owner has no email).
    """
    from django.db import transaction

    from invoicing.models import Invoice
    from notifications.emails import send_invoice_email

    with transaction.atomic():
        invoice = (
            Invoice.objects.select_for_update()
            .select_related('owner')
            .filter(pk=invoice_pk)
            .first()
        )
        if invoice is None or invoice.send_queued_at is None:
            return 'skipped'
        if invoice.status != Invoice.Status.DRAFT:
            invoice.send_queued_at = None
            invoice.save(update_fields=['send_queued_at'])
            return 'skipped'
        if not invoice.owner.email:
            invoice.send_queued_at = None
            invoice.send_error = 'Owner has no email address.'
            invoice.save(update_fields=['send_queued_at', 'send_error'])
            return 'skipped'

    # The SMTP round-trip happens outside the row lock.
    try:
        sent = send_invoice_email(invoice)
    except Exception:
        logger.exception("Queued send raised for invoice %s", invoice.invoice_number)
        sent = False

    if sent:
        invoice.mark_as_sent()
        return 'sent'

    Invoice.objects.filter(pk=invoice.pk).update(
        send_queued_at=None,
        send_error=(
            'Sending failed — the PDF could not be built or the email '
            'server refused the message. Check the server log and try again.'
        ),
    )
    logger.error("Queued send failed for invoice %s", invoice.invoice_number)
    return 'failed'

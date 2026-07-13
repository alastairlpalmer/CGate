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

    today = timezone.now().date()
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

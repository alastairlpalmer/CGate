"""
Celery tasks for the Xero integration.
"""

import logging
import time

from celery import shared_task

logger = logging.getLogger(__name__)

# Xero allows 60 calls/min per tenant. Pace the sweep just under it and
# honour Retry-After on 429 instead of burning the rest of the minute's
# budget on failures.
SWEEP_CALL_INTERVAL_SECONDS = 1.1
MAX_RATE_LIMIT_RETRIES = 3


@shared_task
def sync_xero_invoice_statuses():
    """Poll Xero for payment status on every pushed, still-open invoice.

    Runs nightly via Celery Beat. Invoices Xero reports as PAID are marked
    paid locally with a 'Paid in Xero' payment recorded against them (see
    check_xero_invoice_status), which also stops overdue reminder emails
    chasing owners who have already paid.

    A per-invoice failure (rate limit, transient API error) is logged and
    skipped so one bad invoice doesn't block the rest of the sweep.
    """
    from invoicing.models import Invoice

    from .client import XeroAPIError, XeroTokenExpiredError
    from .models import XeroConnection, XeroInvoiceSync
    from .services import check_xero_invoice_status

    conn = XeroConnection.get_connection()
    if not conn.is_connected:
        logger.info("Xero not connected — skipping invoice status sync.")
        return "not_connected"

    syncs = XeroInvoiceSync.objects.filter(
        sync_status=XeroInvoiceSync.SyncStatus.PUSHED,
        invoice__status__in=[
            Invoice.Status.DRAFT,
            Invoice.Status.SENT,
            Invoice.Status.OVERDUE,
        ],
    ).select_related('invoice')

    checked = paid = errors = 0
    for sync in syncs:
        rate_limit_retries = 0
        while True:
            try:
                updated = check_xero_invoice_status(sync)
            except XeroTokenExpiredError:
                # The connection needs re-authorising — no point continuing.
                logger.warning(
                    "Xero token expired during status sync; reconnect required."
                )
                return f"token_expired after {checked} checked"
            except XeroAPIError as exc:
                if (
                    exc.status_code == 429
                    and rate_limit_retries < MAX_RATE_LIMIT_RETRIES
                ):
                    # Rate limited: wait it out rather than erroring through
                    # the rest of the sweep — unchecked invoices mean owners
                    # who paid in Xero still get overdue reminders today.
                    rate_limit_retries += 1
                    logger.info(
                        "Xero rate limit hit; sleeping 60s "
                        "(retry %s/%s for %s)",
                        rate_limit_retries, MAX_RATE_LIMIT_RETRIES,
                        sync.invoice.invoice_number,
                    )
                    time.sleep(60)
                    continue
                errors += 1
                logger.warning(
                    "Xero status check failed for %s: %s",
                    sync.invoice.invoice_number, exc,
                )
                break
            checked += 1
            if updated.sync_status == XeroInvoiceSync.SyncStatus.PAID_IN_XERO:
                paid += 1
            break
        # Stay under Xero's 60 calls/min while sweeping large yards.
        time.sleep(SWEEP_CALL_INTERVAL_SECONDS)

    summary = f"Checked {checked} invoice(s): {paid} newly paid, {errors} error(s)."
    logger.info(summary)
    return summary

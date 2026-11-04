"""
Celery tasks for the Xero integration.
"""

import logging
import time

from celery import shared_task

logger = logging.getLogger(__name__)

# Xero allows 60 calls/min per tenant. Pace the sweep just under it and
# honour Retry-After on 429 (capped at MAX_RATE_LIMIT_WAIT_SECONDS) instead
# of burning the rest of the minute's budget on failures.
SWEEP_CALL_INTERVAL_SECONDS = 1.1
# Sweep-wide, not per-invoice: three 60s waits per invoice let eight
# rate-limited invoices eat the whole 25-minute soft time limit, after
# which the task was killed mid-sweep with no summary.
MAX_RATE_LIMIT_RETRIES = 3
MAX_RATE_LIMIT_WAIT_SECONDS = 60


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
    from celery.exceptions import SoftTimeLimitExceeded
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
        # Only invoices in the organisation connected now.
        xero_tenant_id__in=['', conn.xero_tenant_id],
        invoice__status__in=[
            Invoice.Status.DRAFT,
            Invoice.Status.SENT,
            Invoice.Status.OVERDUE,
        ],
    ).select_related('invoice')

    checked = paid = errors = 0
    rate_limit_retries = 0
    try:
        for sync in syncs:
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
                        # Rate limited: wait it out rather than erroring
                        # through the rest of the sweep — unchecked invoices
                        # mean owners who paid in Xero still get overdue
                        # reminders today.
                        rate_limit_retries += 1
                        wait = min(
                            exc.retry_after or MAX_RATE_LIMIT_WAIT_SECONDS,
                            MAX_RATE_LIMIT_WAIT_SECONDS,
                        )
                        logger.info(
                            "Xero rate limit hit; sleeping %ss "
                            "(retry %s/%s, at %s)",
                            wait, rate_limit_retries, MAX_RATE_LIMIT_RETRIES,
                            sync.invoice.invoice_number,
                        )
                        time.sleep(wait)
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
    except SoftTimeLimitExceeded:
        # Report what was done rather than dying with no summary; the
        # remaining invoices are picked up by tomorrow's sweep.
        logger.warning(
            "Xero status sync hit the task time limit after %s checked.",
            checked,
        )
        return (
            f"time_limit: checked {checked} invoice(s): {paid} newly paid, "
            f"{errors} error(s); remainder deferred to next run."
        )

    summary = f"Checked {checked} invoice(s): {paid} newly paid, {errors} error(s)."
    logger.info(summary)
    return summary


@shared_task
def push_invoices_to_xero(invoice_ids):
    """Push many invoices to Xero, within Xero's rate limit.

    The bulk "Push to Xero" used to do this inside the web request: with no
    pause between calls, Xero's limit (60 calls a minute) failed every
    invoice after about the 20th, and a large selection ran past the web
    server's 60 s timeout. Here every call waits SWEEP_CALL_INTERVAL_SECONDS
    after the one before, and a 429 waits as long as Xero asks, then tries
    the same invoice again.

    Each invoice's own Xero status shows its result. Returns a summary.
    """
    from celery.exceptions import SoftTimeLimitExceeded
    from invoicing.models import Invoice

    from .client import XeroAPIError, XeroTokenExpiredError, paced_calls
    from .services import XeroNotConnectedError, push_invoice_to_xero

    pushed = failed = 0
    rate_limit_retries = 0
    invoices = Invoice.objects.filter(pk__in=invoice_ids).select_related('owner')
    try:
        with paced_calls(SWEEP_CALL_INTERVAL_SECONDS):
            for invoice in invoices.order_by('pk'):
                if invoice.status == Invoice.Status.CANCELLED:
                    continue
                while True:
                    try:
                        push_invoice_to_xero(invoice)
                    except (XeroNotConnectedError, XeroTokenExpiredError) as exc:
                        # Every remaining push would fail the same way.
                        logger.warning("Xero bulk push stopped: %s", exc)
                        return (
                            f"stopped: {exc}. Pushed {pushed}, "
                            f"{failed} failed before that."
                        )
                    except XeroAPIError as exc:
                        if (
                            exc.status_code == 429
                            and rate_limit_retries < MAX_RATE_LIMIT_RETRIES
                        ):
                            rate_limit_retries += 1
                            time.sleep(min(
                                exc.retry_after or MAX_RATE_LIMIT_WAIT_SECONDS,
                                MAX_RATE_LIMIT_WAIT_SECONDS,
                            ))
                            continue
                        failed += 1
                        logger.warning(
                            "Xero push failed for %s: %s",
                            invoice.invoice_number, exc,
                        )
                        break
                    pushed += 1
                    break
    except SoftTimeLimitExceeded:
        logger.warning("Xero bulk push hit the task time limit after %s.", pushed)
        return (
            f"time_limit: pushed {pushed}, {failed} failed; push the rest again."
        )

    summary = f"Pushed {pushed} invoice(s) to Xero, {failed} failed."
    logger.info(summary)
    return summary

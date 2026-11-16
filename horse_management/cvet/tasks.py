"""Background tasks for C-vet."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def bill_monthly():
    """Make the monthly bills, for each practice that bills once a month.

    Scheduled for the 1st of each month (settings.CELERY_BEAT_SCHEDULE).
    It covers every finished visit before the 1st, so a run on a later
    day, or a second run, bills nothing twice (cvet.billing).
    """
    from . import billing

    bills = billing.bill_month_end()
    summary = f'{len(bills)} monthly bill(s) made'
    logger.info(summary)
    return summary

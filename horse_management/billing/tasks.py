"""Scheduled jobs for billing (horse_management/settings.py, CELERY_BEAT_SCHEDULE)."""

from celery import shared_task


@shared_task
def create_recurring_yard_costs():
    """Make the copies of recurring yard costs that are due (billing/recurring.py)."""
    from .recurring import create_repeats
    return create_repeats()

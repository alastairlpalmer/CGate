"""Overdue invoice reminders: when they go, and "Send reminder now"
(Dev board, 23 Nov 2026: "Configure send/paid/chase email logic").

The yard sets the days in Settings → Billing (BusinessSettings.
overdue_reminder_days); blank uses the server's OVERDUE_REMINDER_DAYS.
The last day's email is the final notice. The daily job
(notifications.tasks.send_overdue_invoice_reminders) and the button on the
invoice page both count reminders on the invoice, so a reminder sent by
hand moves the next one on.
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

MAX_STEPS = 5
MAX_DAY = 365


def parse_days(text):
    """The days in "1, 14, 30" as [1, 14, 30], or [] for a blank box.
    Raises ValueError with a sentence for the form."""
    parts = [p.strip() for p in (text or '').replace(';', ',').split(',') if p.strip()]
    if not parts:
        return []
    try:
        days = [int(p) for p in parts]
    except ValueError:
        raise ValueError('Type whole numbers of days, with commas between them, for example 1, 14, 30.')
    if len(days) > MAX_STEPS:
        raise ValueError(f'Use {MAX_STEPS} reminders or fewer.')
    if any(d < 1 or d > MAX_DAY for d in days):
        raise ValueError(f'Each day must be from 1 to {MAX_DAY}.')
    if days != sorted(set(days)):
        raise ValueError('Put the days in order, each one later than the one before.')
    return days


def reminder_days():
    """The days after the due date on which a reminder goes."""
    from core.models import BusinessSettings
    try:
        days = parse_days(BusinessSettings.get_settings().overdue_reminder_days)
    except ValueError:
        days = []
    return days or list(settings.OVERDUE_REMINDER_DAYS)


def can_remind(invoice):
    """True when "Send reminder now" may be offered for this invoice."""
    from .models import Invoice
    return (
        invoice.status in (Invoice.Status.SENT, Invoice.Status.OVERDUE)
        and invoice.is_overdue
        and invoice.balance_due > 0
        and bool(invoice.owner.email)
    )


def send_now(invoice):
    """Send the next reminder for an overdue invoice at once. Answers the
    stage sent (1 = first), or 0 when the email could not be sent. After
    the last step it sends the final notice again."""
    from notifications.emails import send_invoice_overdue_reminder

    from .models import Invoice

    steps = len(reminder_days())
    stage = min(invoice.overdue_reminder_count + 1, steps)
    if not send_invoice_overdue_reminder(invoice, stage=stage, final=stage == steps):
        return 0
    with transaction.atomic():
        row = Invoice.objects.select_for_update().get(pk=invoice.pk)
        row.overdue_reminder_count = max(row.overdue_reminder_count, stage)
        row.last_overdue_reminder_at = timezone.now()
        row.save(update_fields=['overdue_reminder_count', 'last_overdue_reminder_at'])
    invoice.overdue_reminder_count = row.overdue_reminder_count
    invoice.last_overdue_reminder_at = row.last_overdue_reminder_at
    return stage

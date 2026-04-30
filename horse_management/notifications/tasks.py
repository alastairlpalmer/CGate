"""
Celery tasks for automated notifications.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from invoicing.models import Invoice
from health.models import BreedingRecord, FarrierVisit, Vaccination

from .emails import (
    send_ehv_reminder,
    send_farrier_reminder,
    send_invoice_overdue_reminder,
    send_vaccination_reminder,
)

logger = logging.getLogger(__name__)


@shared_task
def send_vaccination_reminders():
    """
    Send reminders for vaccinations due soon.
    Run daily via Celery Beat.
    """
    today = timezone.now().date()
    reminders_sent = 0

    # Get vaccinations due within their reminder period that haven't been notified
    vaccinations = Vaccination.objects.filter(
        reminder_sent=False,
        next_due_date__isnull=False,
        horse__is_active=True,
    ).select_related('horse', 'vaccination_type')

    for vaccination in vaccinations:
        try:
            reminder_days = vaccination.vaccination_type.reminder_days_before
            reminder_date = vaccination.next_due_date - timedelta(days=reminder_days)

            if today >= reminder_date:
                # Atomic claim: only one worker wins the race to send.
                claimed = Vaccination.objects.filter(
                    pk=vaccination.pk, reminder_sent=False
                ).update(reminder_sent=True)
                if not claimed:
                    continue
                if send_vaccination_reminder(vaccination):
                    reminders_sent += 1
                else:
                    Vaccination.objects.filter(pk=vaccination.pk).update(
                        reminder_sent=False
                    )
        except Exception:
            logger.exception("Error processing vaccination reminder for pk=%s", vaccination.pk)

    return f"Sent {reminders_sent} vaccination reminders"


@shared_task
def send_farrier_reminders():
    """
    Send reminders for farrier visits due within 2 weeks.
    Run daily via Celery Beat.
    """
    today = timezone.now().date()
    two_weeks = today + timedelta(days=14)
    reminders_sent = 0

    # Get horses with farrier visits due soon
    # Only get the most recent visit per horse
    from django.db.models import Max

    horses_needing_farrier = FarrierVisit.objects.filter(
        next_due_date__lte=two_weeks,
        next_due_date__gte=today,
        horse__is_active=True,
        reminder_sent=False,
    ).values('horse').annotate(
        latest_date=Max('date')
    )

    for entry in horses_needing_farrier:
        try:
            visit = FarrierVisit.objects.filter(
                horse_id=entry['horse'],
                date=entry['latest_date'],
                reminder_sent=False,
            ).first()

            if not visit:
                continue

            claimed = FarrierVisit.objects.filter(
                pk=visit.pk, reminder_sent=False
            ).update(reminder_sent=True)
            if not claimed:
                continue
            if send_farrier_reminder(visit):
                reminders_sent += 1
            else:
                FarrierVisit.objects.filter(pk=visit.pk).update(reminder_sent=False)
        except Exception:
            logger.exception("Error processing farrier reminder for horse_id=%s", entry['horse'])

    return f"Sent {reminders_sent} farrier reminders"


REMINDER_REPEAT_DAYS = 7


@shared_task
def send_overdue_invoice_reminders():
    """
    Send reminders for overdue invoices.

    Re-sends at most once every REMINDER_REPEAT_DAYS so beat running daily
    does not spam owners. Status promotion to OVERDUE is left to
    check_invoice_status; this task is purely about emails.

    Run daily via Celery Beat.
    """
    now = timezone.now()
    today = now.date()
    cutoff = now - timedelta(days=REMINDER_REPEAT_DAYS)
    reminders_sent = 0

    eligible = Q(last_overdue_reminder_at__isnull=True) | Q(
        last_overdue_reminder_at__lt=cutoff
    )
    overdue_invoices = Invoice.objects.filter(
        status__in=[Invoice.Status.SENT, Invoice.Status.OVERDUE],
        due_date__lt=today,
    ).filter(eligible).select_related('owner')

    for invoice in overdue_invoices:
        try:
            previous = invoice.last_overdue_reminder_at
            # Atomic claim: only send if last_overdue_reminder_at is still
            # null or older than cutoff. Update returns 0 if another worker
            # already claimed it.
            claimed = Invoice.objects.filter(pk=invoice.pk).filter(
                eligible
            ).update(last_overdue_reminder_at=now)
            if not claimed:
                continue
            if send_invoice_overdue_reminder(invoice):
                reminders_sent += 1
            else:
                # Roll back so next run can retry.
                Invoice.objects.filter(pk=invoice.pk).update(
                    last_overdue_reminder_at=previous
                )
        except Exception:
            logger.exception("Error processing overdue invoice reminder for pk=%s", invoice.pk)

    return f"Sent {reminders_sent} overdue invoice reminders"


@shared_task
def send_ehv_reminders():
    """
    Send EHV vaccination reminders for pregnant mares.
    Checks months 5, 7, 9 from covering date.
    Sends reminder 14 days before each due date.
    Run daily via Celery Beat.
    """
    today = timezone.now().date()
    reminders_sent = 0

    # Get active breeding records that are confirmed in-foal
    active_records = BreedingRecord.objects.filter(
        status='confirmed',
        mare__is_active=True,
    ).select_related('mare')

    for record in active_records:
        try:
            ehv_dates = record.ehv_vaccination_dates

            for month, due_date in ehv_dates.items():
                # Send reminder 14 days before due date
                reminder_date = due_date - timedelta(days=14)
                if not (reminder_date <= today <= due_date + timedelta(days=7)):
                    continue

                # Atomic claim: lock the row, re-read sent months, only
                # append if not already there. Prevents concurrent runs
                # from duplicating the month or corrupting the CSV.
                with transaction.atomic():
                    locked = BreedingRecord.objects.select_for_update().get(pk=record.pk)
                    sent = locked.sent_ehv_months
                    if month in sent:
                        continue
                    sent.add(month)
                    locked.ehv_reminders_sent = ','.join(str(m) for m in sorted(sent))
                    locked.save(update_fields=['ehv_reminders_sent'])

                if send_ehv_reminder(record, month):
                    reminders_sent += 1
                else:
                    # Roll back the claim so it can be retried later.
                    with transaction.atomic():
                        locked = BreedingRecord.objects.select_for_update().get(pk=record.pk)
                        sent = locked.sent_ehv_months
                        sent.discard(month)
                        locked.ehv_reminders_sent = ','.join(str(m) for m in sorted(sent))
                        locked.save(update_fields=['ehv_reminders_sent'])
        except Exception:
            logger.exception("Error processing EHV reminder for record pk=%s", record.pk)

    return f"Sent {reminders_sent} EHV reminders"


@shared_task
def check_invoice_status():
    """
    Check and update invoice statuses.
    Run daily via Celery Beat.
    """
    today = timezone.now().date()
    updated = 0

    # Mark sent invoices as overdue if past due date
    overdue = Invoice.objects.filter(
        status=Invoice.Status.SENT,
        due_date__lt=today,
    ).update(status=Invoice.Status.OVERDUE)

    return f"Updated {overdue} invoices to overdue status"

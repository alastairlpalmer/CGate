"""
Celery tasks for automated notifications.
"""

import logging
from datetime import timedelta

from celery import shared_task
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
                success = send_vaccination_reminder(vaccination)
                if success:
                    vaccination.reminder_sent = True
                    vaccination.save(update_fields=['reminder_sent'])
                    reminders_sent += 1
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

            if visit:
                success = send_farrier_reminder(visit)
                if success:
                    visit.reminder_sent = True
                    visit.save(update_fields=['reminder_sent'])
                    reminders_sent += 1
        except Exception:
            logger.exception("Error processing farrier reminder for horse_id=%s", entry['horse'])

    return f"Sent {reminders_sent} farrier reminders"


@shared_task
def send_overdue_invoice_reminders():
    """
    Send reminders for overdue invoices.
    Run daily via Celery Beat.
    """
    today = timezone.now().date()
    reminders_sent = 0

    # Get invoices that are overdue and not paid
    overdue_invoices = Invoice.objects.filter(
        status=Invoice.Status.SENT,
        due_date__lt=today,
    ).select_related('owner')

    for invoice in overdue_invoices:
        try:
            # Send reminder first, then update status
            success = send_invoice_overdue_reminder(invoice)
            invoice.status = Invoice.Status.OVERDUE
            invoice.save(update_fields=['status'])
            if success:
                reminders_sent += 1
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
            sent_months = record.sent_ehv_months

            for month, due_date in ehv_dates.items():
                if month in sent_months:
                    continue

                # Send reminder 14 days before due date
                reminder_date = due_date - timedelta(days=14)
                if today >= reminder_date and today <= due_date + timedelta(days=7):
                    success = send_ehv_reminder(record, month)
                    if success:
                        # Mark this month as sent
                        if record.ehv_reminders_sent:
                            record.ehv_reminders_sent += f',{month}'
                        else:
                            record.ehv_reminders_sent = str(month)
                        record.save(update_fields=['ehv_reminders_sent'])
                        reminders_sent += 1
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

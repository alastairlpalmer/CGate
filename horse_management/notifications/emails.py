"""
Email sending functions for notifications.
"""

import logging

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from core.models import BusinessSettings

from .mail import SKIPPED, can_email, health_unsubscribe_url, message

logger = logging.getLogger(__name__)


def send_invoice_email(invoice):
    """Send invoice email with PDF attachment."""
    from invoicing.pdf import generate_invoice_pdf

    if not can_email(invoice.owner):
        return False

    business = BusinessSettings.get_settings()

    subject = f"Invoice {invoice.invoice_number} from {business.business_name}"

    context = {
        'invoice': invoice,
        'business': business,
    }

    html_content = render_to_string('notifications/email/invoice.html', context)

    email = message(subject, html_content, [invoice.owner.email])

    # The PDF *is* the invoice. Sending without it used to report success,
    # mark the invoice SENT and drop it from the draft queue, while the
    # owner got an email with nothing attached and nobody was told.
    try:
        pdf_file = generate_invoice_pdf(invoice)
    except Exception:
        logger.exception("Failed to generate PDF for %s", invoice.invoice_number)
        return False
    email.attach(
        f"{invoice.invoice_number}.pdf",
        pdf_file.read(),
        'application/pdf'
    )

    try:
        email.send()
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def send_vaccination_digest(owner, vaccinations):
    """Send ONE email covering all of an owner's due vaccinations.

    An owner with several horses due used to get one email per record —
    a backfill or a busy spring morning meant a stack of near-identical
    emails. The digest lists them all in a single message.
    """
    if not owner or not owner.email or not vaccinations:
        return False
    if not owner.email_health_reminders or not can_email(owner):
        # Turned off by the owner, or the address bounces.
        return SKIPPED
    unsubscribe = health_unsubscribe_url(owner)

    business = BusinessSettings.get_settings()

    if len(vaccinations) == 1:
        v = vaccinations[0]
        subject = f"Vaccination Due: {v.horse.name} - {v.vaccination_type.name}"
    else:
        subject = f"Vaccinations Due: {len(vaccinations)} reminders"

    html_content = render_to_string(
        'notifications/email/vaccination_digest.html',
        {
            'vaccinations': vaccinations,
            'owner': owner,
            'unsubscribe_url': unsubscribe,
            'business': business,
        },
    )

    email = message(subject, html_content, [owner.email], unsubscribe_url=unsubscribe)

    try:
        email.send()
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def send_farrier_digest(owner, visits):
    """Send ONE email covering all of an owner's due farrier visits."""
    if not owner or not owner.email or not visits:
        return False
    if not owner.email_health_reminders or not can_email(owner):
        # Turned off by the owner, or the address bounces.
        return SKIPPED
    unsubscribe = health_unsubscribe_url(owner)

    business = BusinessSettings.get_settings()

    if len(visits) == 1:
        subject = f"Farrier Due: {visits[0].horse.name}"
    else:
        subject = f"Farrier Due: {len(visits)} horses"

    html_content = render_to_string(
        'notifications/email/farrier_digest.html',
        {
            'visits': visits,
            'owner': owner,
            'unsubscribe_url': unsubscribe,
            'business': business,
        },
    )

    email = message(subject, html_content, [owner.email], unsubscribe_url=unsubscribe)

    try:
        email.send()
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def send_ehv_reminder(breeding_record, month_number):
    """Send EHV vaccination reminder for a pregnant mare."""
    mare = breeding_record.mare
    owner = mare.current_owner

    if not owner or not owner.email:
        return False
    if not owner.email_health_reminders or not can_email(owner):
        # Turned off by the owner, or the address bounces.
        return SKIPPED
    unsubscribe = health_unsubscribe_url(owner)

    business = BusinessSettings.get_settings()

    ehv_dates = breeding_record.ehv_vaccination_dates
    due_date = ehv_dates.get(month_number)

    subject = f"EHV Vaccination Due: {mare.name} - Month {month_number}"

    context = {
        'breeding_record': breeding_record,
        'mare': mare,
        'owner': owner,
            'unsubscribe_url': unsubscribe,
        'business': business,
        'month_number': month_number,
        'due_date': due_date,
    }

    html_content = render_to_string('notifications/email/ehv_reminder.html', context)

    email = message(subject, html_content, [owner.email], unsubscribe_url=unsubscribe)

    try:
        email.send()
        return True
    except Exception as e:
        logger.error(f"Failed to send EHV reminder email: {e}")
        return False


def send_invoice_overdue_reminder(invoice, stage=1, final=False):
    """Send overdue invoice reminder email.

    ``stage`` is which reminder this is (1 = first); ``final`` words it as
    the last one before the yard follows up in person.
    """
    if not can_email(invoice.owner):
        return False

    business = BusinessSettings.get_settings()

    if final:
        subject = f"Final notice: Invoice {invoice.invoice_number}"
    elif stage > 1:
        subject = f"Second reminder: Invoice {invoice.invoice_number}"
    else:
        subject = f"Payment Reminder: Invoice {invoice.invoice_number}"

    context = {
        'invoice': invoice,
        'business': business,
        'stage': stage,
        'final': final,
    }

    html_content = render_to_string('notifications/email/invoice_overdue.html', context)

    email = message(subject, html_content, [invoice.owner.email])

    try:
        email.send()
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def send_owner_statement(owner):
    """Send an owner their statement of account with PDF attachment."""
    from invoicing.pdf import generate_owner_statement_pdf
    from invoicing.services import StatementService

    if not can_email(owner):
        return False

    business = BusinessSettings.get_settings()
    statement = StatementService.build_owner_statement(owner)

    subject = f"Statement of account from {business.business_name}"
    html_content = render_to_string('notifications/email/statement.html', {
        'owner': owner,
        'statement': statement,
        'business': business,
    })

    email = message(subject, html_content, [owner.email])

    try:
        pdf_file = generate_owner_statement_pdf(owner, statement)
        email.attach(
            f"statement-{timezone.localdate():%Y-%m-%d}.pdf",
            pdf_file.read(),
            'application/pdf',
        )
    except Exception:
        logger.exception("Statement PDF generation failed; sending without attachment.")

    try:
        email.send()
        return True
    except Exception:
        logger.exception("Failed to send statement email to %s", owner.email)
        return False


def send_document_expiry_summary(to_email, documents, today):
    """Send the yard one email listing documents expiring soon (or expired)."""
    business = BusinessSettings.get_settings()

    subject = (
        f"{len(documents)} document{'s' if len(documents) != 1 else ''} "
        "expiring soon"
    )
    html_content = render_to_string('notifications/email/document_expiry.html', {
        'documents': documents,
        'business': business,
        'today': today,
    })

    email = message(subject, html_content, [to_email])
    try:
        email.send()
        return True
    except Exception:
        logger.exception("Failed to send document expiry summary to %s", to_email)
        return False


def send_breeding_digest(owner, entries):
    """One email per owner listing this week's breeding reminders: a scan
    due, or a foal due in 30 or 7 days. ``entries`` is a list of dicts with
    ``record``, ``kind`` ('scan14', 'heartbeat', 'foal30', 'foal7') and
    ``due``."""
    if not owner or not owner.email or not entries:
        return False
    if not owner.email_health_reminders or not can_email(owner):
        # Turned off by the owner, or the address bounces.
        return SKIPPED
    unsubscribe = health_unsubscribe_url(owner)

    business = BusinessSettings.get_settings()
    if len(entries) == 1:
        e = entries[0]
        subject = f"Breeding reminder: {e['record'].mare.name} - {e['label']}"
    else:
        subject = f"Breeding reminders: {len(entries)} items"

    html_content = render_to_string(
        'notifications/email/breeding_digest.html',
        {'entries': entries, 'owner': owner,
            'unsubscribe_url': unsubscribe, 'business': business},
    )
    email = message(subject, html_content, [owner.email], unsubscribe_url=unsubscribe)
    try:
        email.send()
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False

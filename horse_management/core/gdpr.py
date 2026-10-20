"""Personal data about horse owners: export it, erase it, and stop keeping
it longer than the yard needs to (UK GDPR articles 15, 17 and 5(1)(e)).

Three operations:

* ``export_owner`` — everything held about one owner, as a ZIP of CSV files
  plus their uploaded documents (a subject access request).
* ``erase_contact_details`` — a request to be forgotten, for an archived
  owner. Removes email, phone, notes, account code and every document,
  from the live row **and** from the record history. Name and postal
  address stay, because they are on invoices that the yard must keep for
  ``OWNER_RETENTION_YEARS`` (HMRC: six years) and UK GDPR article
  17(3)(b) allows keeping what a legal duty requires.
* ``anonymise_owner`` — replaces the name and address too, and removes the
  Xero contact link. Done by ``apply_retention`` once the newest invoice
  is older than the retention period, or by hand when there are no
  invoices to keep.

Not reached by this code, and written down in docs/DATA_RETENTION.md:
off-site backups (they age out under their own retention), Xero (a
separate system), and emails already sent.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .csv_export import csv_bytes

logger = logging.getLogger(__name__)

# Fields on Owner that hold personal data, by what may be kept.
CONTACT_FIELDS = ('email', 'phone', 'notes', 'account_code')
INVOICE_FIELDS = ('name', 'address')


def anonymous_name(owner) -> str:
    return f'Former owner #{owner.pk}'


def owner_label(owner) -> str:
    """How the audit trail names an owner: never by their name."""
    return f'Owner #{owner.pk}'


# ── Export ───────────────────────────────────────────────────────────────────

def export_owner(owner) -> bytes:
    """A ZIP of everything held about ``owner``."""
    from billing.models import ExtraCharge
    from invoicing.models import Payment

    from .models import Document, OwnershipShare, Placement

    files = {}
    files['owner.csv'] = csv_bytes(
        ['Name', 'Email', 'Phone', 'Address', 'Account code', 'Notes', 'Archived', 'Created'],
        [[owner.name, owner.email, owner.phone, owner.address, owner.account_code,
          owner.notes, owner.is_archived, owner.created_at]],
    )
    files['owner_history.csv'] = csv_bytes(
        ['Changed at', 'Change', 'Name', 'Email', 'Phone', 'Address', 'Notes'],
        [[h.history_date, {'+': 'Created', '~': 'Changed', '-': 'Deleted'}.get(h.history_type, ''),
          h.name, h.email, h.phone, h.address, h.notes]
         for h in owner.history.order_by('history_date')],
    )
    files['horses_owned.csv'] = csv_bytes(
        ['Horse', 'Share %'],
        [[s.horse.name, s.share_percentage]
         for s in OwnershipShare.objects.filter(owner=owner).select_related('horse')],
    )
    files['stays.csv'] = csv_bytes(
        ['Horse', 'Location', 'From', 'To', 'Rate'],
        [[p.horse.name, str(p.location), p.start_date, p.end_date, str(p.rate_type or '')]
         for p in Placement.objects.filter(owner=owner).select_related('horse', 'location', 'rate_type')
         .order_by('start_date')],
    )
    invoices = list(owner.invoices.order_by('period_start'))
    files['invoices.csv'] = csv_bytes(
        ['Number', 'From', 'To', 'Net', 'VAT', 'Total', 'Status', 'Due'],
        [[i.invoice_number, i.period_start, i.period_end, i.subtotal, i.vat_amount,
          i.total, i.get_status_display(), i.due_date] for i in invoices],
    )
    files['payments.csv'] = csv_bytes(
        ['Invoice', 'Date', 'Amount', 'Method', 'Reference', 'Voided'],
        [[p.invoice.invoice_number, p.date, p.amount, p.get_method_display(), p.reference,
          p.is_voided]
         for p in Payment.all_objects.filter(invoice__owner=owner).select_related('invoice')
         .order_by('date')],
    )
    files['charges.csv'] = csv_bytes(
        ['Date', 'Horse', 'Type', 'Description', 'Amount', 'Invoiced'],
        [[c.date, c.horse.name, c.get_charge_type_display(), c.description, c.amount, c.invoiced]
         for c in ExtraCharge.objects.filter(owner=owner).select_related('horse').order_by('date')],
    )
    documents = list(Document.objects.filter(owner=owner))
    files['documents.csv'] = csv_bytes(
        ['Title', 'Type', 'Expires', 'Added', 'File in this ZIP'],
        [[d.title, d.get_doc_type_display(), d.expiry_date, d.uploaded_at,
          f'documents/{d.pk}-{_basename(d.file.name)}' if d.file else '']
         for d in documents],
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('README.txt', _readme(owner))
        for name, data in files.items():
            archive.writestr(name, data)
        for d in documents:
            if not d.file:
                continue
            try:
                with d.file.open('rb') as fh:
                    archive.writestr(f'documents/{d.pk}-{_basename(d.file.name)}', fh.read())
            except (FileNotFoundError, OSError):
                logger.warning('Export: file for document %s is missing from storage.', d.pk)
    return buffer.getvalue()


def _basename(name: str) -> str:
    return name.rsplit('/', 1)[-1]


def _readme(owner) -> str:
    return (
        f'Personal data held about {owner.name}, exported {timezone.localdate():%d %B %Y}.\n\n'
        'owner.csv          Contact details as they are now.\n'
        'owner_history.csv  Earlier versions of those details.\n'
        'horses_owned.csv   Horses and ownership shares.\n'
        'stays.csv          Where each horse was kept, and at what rate.\n'
        'invoices.csv       Invoices.\n'
        'payments.csv       Payments recorded against those invoices.\n'
        'charges.csv        Extra charges (vet, farrier and similar).\n'
        'documents.csv      Uploaded documents. The files are in documents/.\n'
    )


# ── Erase ────────────────────────────────────────────────────────────────────

class ErasureRefused(Exception):
    pass


def erase_blockers(owner) -> list[str]:
    if not owner.is_archived:
        return ["Archive this owner first. An owner with horses on the yard still needs their contact details."]
    return []


@transaction.atomic
def erase_contact_details(owner, user=None) -> None:
    """Remove what the yard does not need to keep: see the module docstring."""
    from .models import AuditEvent

    blockers = erase_blockers(owner)
    if blockers:
        raise ErasureRefused(' '.join(blockers))
    _clear(owner, CONTACT_FIELDS)
    _delete_documents(owner)
    _record(owner, AuditEvent.Action.OWNER_ERASED, user, 'Contact details and documents erased')


@transaction.atomic
def anonymise_owner(owner, user=None, reason='') -> None:
    """Remove the name and address as well, and the Xero contact link."""
    from .models import AuditEvent

    blockers = erase_blockers(owner)
    if blockers:
        raise ErasureRefused(' '.join(blockers))
    _clear(owner, CONTACT_FIELDS + INVOICE_FIELDS)
    _delete_documents(owner)
    from xero_integration.models import XeroContactMapping
    XeroContactMapping.objects.filter(owner=owner).delete()
    _record(owner, AuditEvent.Action.OWNER_ANONYMISED, user, reason or 'Name, address and contact details removed')


def _clear(owner, fields) -> None:
    blank = {f: '' for f in fields}
    if 'name' in fields:
        blank['name'] = anonymous_name(owner)
    for field, value in blank.items():
        setattr(owner, field, value)
    # Not a tracked change: the history row it would add is exactly the
    # kind of copy being removed.
    owner.skip_history_when_saving = True
    owner.save(update_fields=list(blank))
    del owner.skip_history_when_saving
    # Earlier versions of the row hold the same details.
    owner.history.update(**blank)


def _delete_documents(owner) -> None:
    from .models import Document

    for document in Document.all_objects.filter(owner=owner):
        if document.file:
            try:
                document.file.delete(save=False)
            except Exception:
                logger.exception('Erase: could not delete the file of document %s', document.pk)
        document.delete()


def _record(owner, action, user, detail) -> None:
    from . import audit

    audit.record(action, actor=user, detail=f'{owner_label(owner)}: {detail}')


# ── Retention ────────────────────────────────────────────────────────────────

def retention_cutoff() -> date:
    years = settings.OWNER_RETENTION_YEARS
    today = timezone.localdate()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 February
        return today.replace(year=today.year - years, day=28)


def owners_past_retention():
    """Archived owners whose newest invoice (or archive date, with no
    invoices) is older than the retention period, not yet anonymised."""
    from django.db.models import Max, Q

    from .models import Owner

    cutoff = retention_cutoff()
    return (
        Owner.objects.archived()
        .annotate(last_period=Max('invoices__period_end'))
        .filter(
            Q(last_period__lt=cutoff)
            | Q(last_period__isnull=True, archived_at__date__lt=cutoff),
        )
        .exclude(name__startswith='Former owner #')
    )


def apply_retention() -> dict:
    """Anonymise owners past retention, and drop old account events."""
    from .models import AuditEvent

    anonymised = 0
    for owner in owners_past_retention():
        anonymise_owner(owner, reason=f'Retention: {settings.OWNER_RETENTION_YEARS} years after the last invoice')
        anonymised += 1
    event_cutoff = timezone.now() - timedelta(days=settings.AUDIT_EVENT_RETENTION_DAYS)
    events, _ = AuditEvent.objects.filter(created_at__lt=event_cutoff).delete()
    result = {'owners_anonymised': anonymised, 'audit_events_deleted': events}
    logger.info('Retention: %s', result)
    return result

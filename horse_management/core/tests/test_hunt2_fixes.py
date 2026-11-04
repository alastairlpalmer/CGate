"""Findings from the second bug hunt (owners, dashboard, emails, settings)."""

from datetime import date, timedelta
from decimal import Decimal

from django.core import mail
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core import gdpr
from core.dashboard import activity, attention
from core.models import BusinessSettings, Document, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access


def _pdf(name='a.pdf'):
    return ContentFile(b'%PDF-1.4', name=name)


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin()
        self.owner = Owner.objects.create(name='Jo', email='jo@example.com')
        self.rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))

    def place(self, horse, site='Colgate', name='Top'):
        location = Location.objects.get_or_create(site=site, name=name)[0]
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location, rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
        )
        return location


class OwnerDeleteTests(Base):

    def test_an_owner_with_only_a_deleted_charge_is_archived_not_a_500(self):
        horse = Horse.objects.create(name='Ghost')
        charge = ExtraCharge.objects.create(
            horse=horse, owner=self.owner, charge_type='other', date=self.today,
            amount=Decimal('10.00'), description='Wrong owner',
        )
        charge.soft_delete()
        self.client.force_login(self.admin)
        response = self.client.post(reverse('owner_delete', args=[self.owner.pk]))
        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_archived)

    def test_deleting_an_owner_removes_their_history_and_files(self):
        self.owner.phone = '07700 900111'
        self.owner.save()
        self.client.force_login(self.admin)
        owner_pk = self.owner.pk
        self.client.post(reverse('owner_delete', args=[owner_pk]))
        self.assertFalse(Owner.objects.filter(pk=owner_pk).exists())
        self.assertFalse(Owner.history.filter(id=owner_pk).exists())


class GdprTests(Base):

    def test_anonymise_clears_the_bounce_reason(self):
        Owner.objects.filter(pk=self.owner.pk).update(
            is_archived=True, archived_at=timezone.now(),
            email_bounced_at=timezone.now(),
            email_bounce_reason='550 <jo@example.com>: Recipient address rejected',
        )
        self.owner.refresh_from_db()
        gdpr.anonymise_owner(self.owner)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email_bounce_reason, '')
        self.assertIsNone(self.owner.email_bounced_at)
        self.assertFalse(
            Owner.history.filter(id=self.owner.pk).exclude(email_bounce_reason='').exists()
        )

    def test_the_export_includes_a_document_in_recently_deleted(self):
        import io
        import zipfile

        document = Document.objects.create(owner=self.owner, doc_type='other', title='Loan', file=_pdf())
        self.addCleanup(document.file.delete, save=False)
        document.soft_delete()
        archive = zipfile.ZipFile(io.BytesIO(gdpr.export_owner(self.owner)))
        self.assertIn('Loan', archive.read('documents.csv').decode())


class QuickFindTests(Base):

    def test_active_owners_come_before_archived_ones(self):
        for i in range(6):
            Owner.objects.create(name=f'Aaron Smith {i}', is_archived=True)
        Owner.objects.create(name='Zara Smith')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('quick_find'), {'q': 'smith'})
        self.assertContains(response, 'Zara Smith')
        self.assertContains(response, 'Archived')


class DocumentExpiryTests(Base):

    def setUp(self):
        super().setUp()
        settings_row = BusinessSettings.get_settings()
        settings_row.email = 'office@yard.example'
        settings_row.save()

    def test_departed_horses_and_archived_owners_are_left_out(self):
        gone = Horse.objects.create(name='Gone', is_active=False)
        Document.objects.create(
            horse=gone, doc_type='insurance', title='Old cover', file=_pdf(),
            expiry_date=self.today + timedelta(days=10),
        )
        archived = Owner.objects.create(name='Former', is_archived=True)
        Document.objects.create(
            owner=archived, doc_type='other', title='Loan', file=_pdf(),
            expiry_date=self.today + timedelta(days=5),
        )
        from notifications.tasks import send_document_expiry_reminders
        send_document_expiry_reminders()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            [i for i in attention.collect(self.admin, today=self.today) if i.kind == 'document'], [],
        )

    def test_a_renewed_document_clears_the_expired_one(self):
        horse = Horse.objects.create(name='Ghost')
        self.place(horse)
        Document.objects.create(
            horse=horse, doc_type='insurance', title='Insurance 2025', file=_pdf(),
            expiry_date=self.today - timedelta(days=20),
        )
        Document.objects.create(
            horse=horse, doc_type='insurance', title='Insurance 2026', file=_pdf(),
            expiry_date=self.today + timedelta(days=345),
        )
        items = [i for i in attention.collect(self.admin, today=self.today) if i.kind == 'document']
        self.assertEqual(items, [])

    def test_owner_documents_are_not_shown_without_owners_access(self):
        Document.objects.create(
            owner=self.owner, doc_type='other', title='Loan', file=_pdf(),
            expiry_date=self.today + timedelta(days=5),
        )
        groom = make_user_with_access('groom', horses='view', dashboard='view')
        items = [i for i in attention.collect(groom, today=self.today) if i.kind == 'document']
        self.assertEqual(items, [])


class EhvRearmTests(Base):

    def test_moving_the_covering_date_sends_the_ehv_reminders_again(self):
        from health.models import BreedingRecord
        mare = Horse.objects.create(name='Mare')
        record = BreedingRecord.objects.create(
            mare=mare, stallion_name='Stud', date_covered=self.today - timedelta(days=150),
            status=BreedingRecord.Status.CONFIRMED, ehv_reminders_sent='5',
        )
        record = BreedingRecord.objects.get(pk=record.pk)
        record.date_covered = self.today - timedelta(days=120)
        record.save()
        record.refresh_from_db()
        self.assertEqual(record.sent_ehv_months, set())


class ZeroBalanceReminderTests(Base):

    def test_no_overdue_reminder_for_nothing_owed(self):
        from invoicing.models import Invoice
        from notifications.tasks import send_overdue_invoice_reminders
        Invoice.objects.create(
            owner=self.owner, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            due_date=self.today - timedelta(days=60), total=Decimal('0.00'),
            status=Invoice.Status.OVERDUE,
        )
        send_overdue_invoice_reminders()
        self.assertEqual(len(mail.outbox), 0)


class ActivityFeedTests(Base):

    def test_a_quiet_site_still_shows_its_events(self):
        from health.models import FarrierVisit
        for i in range(15):
            horse = Horse.objects.create(name=f'Busy {i}')
            self.place(horse, site='Busy', name='B')
            FarrierVisit.objects.create(horse=horse, date=self.today - timedelta(days=1))
        quiet = Horse.objects.create(name='Quiet one')
        self.place(quiet, site='Quiet', name='Q')
        FarrierVisit.objects.create(horse=quiet, date=self.today - timedelta(days=2))
        days = activity.recent(self.admin, site='Quiet', today=self.today)
        kinds = {event.kind for day in days for event in day['events']}
        self.assertIn('farrier', kinds)

    def test_a_credit_note_is_not_a_payment_received(self):
        from invoicing.models import Invoice, Payment
        invoice = Invoice.objects.create(
            owner=self.owner, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            due_date=self.today, total=Decimal('100.00'), status=Invoice.Status.SENT,
        )
        Payment.objects.create(
            invoice=invoice, date=self.today, amount=Decimal('40.00'),
            method=Payment.Method.CREDIT_NOTE,
        )
        days = activity.recent(self.admin, today=self.today)
        texts = [event.what for day in days for event in day['events']]
        self.assertTrue(any(t.startswith('Credit note') for t in texts), texts)
        self.assertFalse(any(t.startswith('Payment received') for t in texts), texts)


class AuditLogTests(Base):

    def test_an_impossible_date_is_not_a_500(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('audit_log'), {'before': '2026-02-30T10:00:00'})
        self.assertEqual(response.status_code, 200)

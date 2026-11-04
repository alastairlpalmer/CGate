"""Background jobs, import and horse placement defects from the bug review.

- An invoice email was sent again when recording the send failed.
- The backup reported success with no media while the database pointed
  at uploaded files.
- load_csv_data: --force renamed the business, duplicate names stopped
  the import, and a changed owner made a second open stay.
- A move or arrival dated in the future put the horse in the field now.
- A past stay on an archived field could not be edited.
- Two arrivals of one horse at once could make Log Arrival a 500.
"""

import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from celery.exceptions import Retry
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import IntegrityError, OperationalError
from django.test import TestCase
from django.utils import timezone

from core.models import BusinessSettings, Document, Horse, Location, Owner, Placement, RateType
from core.services import PlacementService


class InvoiceSendRecordTests(TestCase):

    def test_a_failed_save_after_sending_does_not_send_again(self):
        from invoicing.models import Invoice
        from invoicing.tasks import claim_invoice_for_sending, send_invoice_email_task

        owner = Owner.objects.create(name='Jo', email='jo@example.com')
        invoice = Invoice.objects.create(
            owner=owner, period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
            due_date=date(2026, 7, 31), total=Decimal('100.00'),
        )
        claim_invoice_for_sending(invoice.pk)
        sends = []
        retry_kwargs = {}
        real_mark = Invoice.mark_as_sent

        def flaky_mark(self):
            if not retry_kwargs:
                raise OperationalError('connection lost')
            return real_mark(self)

        def fake_retry(**kwargs):
            retry_kwargs.update(kwargs)
            return Retry()

        with mock.patch('notifications.emails.send_invoice_email',
                        side_effect=lambda inv: sends.append(inv.pk) or True), \
                mock.patch.object(Invoice, 'mark_as_sent', flaky_mark), \
                mock.patch.object(send_invoice_email_task, 'retry', side_effect=fake_retry):
            with self.assertRaises(Retry):
                send_invoice_email_task.run(invoice.pk)
            self.assertEqual(retry_kwargs['kwargs'], {'already_sent': True})
            # The worker runs the retry message.
            send_invoice_email_task.run(*retry_kwargs['args'], **retry_kwargs['kwargs'])

        self.assertEqual(len(sends), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.SENT)


class BackupMediaTests(TestCase):

    def test_files_the_database_points_at_but_cannot_be_read_fail_the_backup(self):
        from core.backup import runner

        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        owner = Owner.objects.create(name='Jo')
        document = Document.objects.create(
            owner=owner, doc_type='other', title='Passport',
            file=ContentFile(b'%PDF', name='passport.pdf'),
        )
        self.addCleanup(document.file.delete, save=False)
        empty = FileSystemStorage(location=media.name)
        with mock.patch.object(runner, 'default_storage', empty):
            with self.assertRaises(runner.BackupError):
                runner.archive_media(media.name)


class LoadCsvDataTests(TestCase):

    def test_force_keeps_the_business_name(self):
        from core.management.commands.load_csv_data import Command

        settings_row = BusinessSettings.objects.create(pk=1, business_name='Yardway Stables')
        Command(stdout=StringIO())._do_import([], {})
        settings_row.refresh_from_db()
        self.assertEqual(settings_row.business_name, 'Yardway Stables')


class FutureDateTests(TestCase):

    def setUp(self):
        self.owner = Owner.objects.create(name='Jo')
        self.rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        self.field_a = Location.objects.create(site='Colgate', name='A')
        self.field_b = Location.objects.create(site='Colgate', name='B')
        self.horse = Horse.objects.create(name='Ghost')
        self.today = timezone.localdate()
        Placement.objects.create(
            horse=self.horse, owner=self.owner, location=self.field_a, rate_type=self.rate,
            start_date=self.today - timedelta(days=30),
        )

    def test_a_move_cannot_be_dated_in_the_future(self):
        with self.assertRaises(ValidationError):
            PlacementService.move_horse(
                self.horse, new_location=self.field_b,
                move_date=self.today + timedelta(days=10),
            )
        self.assertEqual(self.horse.placements.get(end_date__isnull=True).location, self.field_a)

    def test_an_arrival_cannot_be_dated_in_the_future(self):
        newcomer = Horse.objects.create(name='New')
        created, errors = PlacementService.bulk_arrive(
            [newcomer], owner=self.owner, location=self.field_b, rate_type=self.rate,
            arrival_date=self.today + timedelta(days=3),
        )
        self.assertEqual(created, 0)
        self.assertIn('future', errors[0])

    def test_two_arrivals_at_once_are_reported_not_a_crash(self):
        newcomer = Horse.objects.create(name='New')
        with mock.patch.object(PlacementService, '_place', side_effect=IntegrityError('one open stay')):
            created, errors = PlacementService.bulk_arrive(
                [newcomer], owner=self.owner, location=self.field_b, rate_type=self.rate,
                arrival_date=self.today,
            )
        self.assertEqual(created, 0)
        self.assertIn('already arrived', errors[0])


class ArchivedLocationStayTests(TestCase):

    def test_a_past_stay_on_an_archived_field_keeps_its_field(self):
        from core.forms import PlacementForm

        owner = Owner.objects.create(name='Jo')
        rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        old_field = Location.objects.create(site='Colgate', name='Old')
        other_archived = Location.objects.create(site='Colgate', name='Gone')
        horse = Horse.objects.create(name='Ghost')
        today = timezone.localdate()
        stay = Placement.objects.create(
            horse=horse, owner=owner, location=old_field, rate_type=rate,
            start_date=today - timedelta(days=60), end_date=today - timedelta(days=30),
        )
        Location.objects.filter(pk__in=[old_field.pk, other_archived.pk]).update(is_archived=True)

        form = PlacementForm(instance=stay)
        self.assertIn(old_field, form.fields['location'].queryset)
        self.assertNotIn(other_archived, form.fields['location'].queryset)
        new_form = PlacementForm()
        self.assertNotIn(old_field, new_form.fields['location'].queryset)

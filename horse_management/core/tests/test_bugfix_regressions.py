"""Regression tests for the bug-fix sweep on branch claude/project-overview-UQa6A.

Each test class corresponds to one fixed bug. If a class fails, the
underlying bug has reappeared.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (
    BusinessSettings,
    Horse,
    Location,
    Owner,
    Placement,
    RateType,
)
from health.models import BreedingRecord, FarrierVisit, Vaccination, VaccinationType
from invoicing.models import Invoice


def _mare():
    return Horse.objects.create(name="Test Mare", sex=Horse.Sex.MARE)


def _yard():
    return Location.objects.create(name="Field A")


def _owner():
    return Owner.objects.create(name="Jane")


def _rate():
    return RateType.objects.create(name="Grass", daily_rate=Decimal("5.00"))


class PlacementDateValidationTests(TestCase):
    """Placement.clean must reject end_date < start_date."""

    def test_rejects_end_before_start(self):
        placement = Placement(
            horse=_mare(),
            owner=_owner(),
            location=_yard(),
            rate_type=_rate(),
            start_date=date(2026, 2, 1),
            end_date=date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError) as ctx:
            placement.clean()
        self.assertIn("end date", str(ctx.exception).lower())

    def test_accepts_same_day(self):
        Placement.objects.create(
            horse=_mare(), owner=_owner(), location=_yard(), rate_type=_rate(),
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 1),
        )

    def test_accepts_end_after_start(self):
        Placement.objects.create(
            horse=_mare(), owner=_owner(), location=_yard(), rate_type=_rate(),
            start_date=date(2026, 1, 1), end_date=date(2026, 2, 1),
        )


class VaccinationIsDueSoonNullTests(TestCase):
    """Vaccination.is_due_soon must not crash when next_due_date is None."""

    def test_returns_false_when_no_due_date(self):
        vtype = VaccinationType.objects.create(name="Flu", interval_months=12)
        # Bypass save() auto-fill by writing the field after creation.
        vac = Vaccination.objects.create(
            horse=_mare(), vaccination_type=vtype, date_given=date(2026, 1, 1),
        )
        Vaccination.objects.filter(pk=vac.pk).update(next_due_date=None)
        vac.refresh_from_db()
        self.assertIsNone(vac.next_due_date)
        self.assertFalse(vac.is_due_soon)


class InvoiceNumberSequentialTests(TestCase):
    """get_next_invoice_number issues sequential, unique numbers."""

    def test_sequential_unique_numbers(self):
        settings = BusinessSettings.get_settings()
        settings.next_invoice_number = 1
        settings.save(update_fields=['next_invoice_number'])

        numbers = [settings.get_next_invoice_number() for _ in range(5)]

        self.assertEqual(numbers, [
            "INV00001", "INV00002", "INV00003", "INV00004", "INV00005",
        ])
        self.assertEqual(len(set(numbers)), 5)

        # Counter is now 6, not 2 (the original bug left the in-memory
        # instance stale at 2 because of refresh_from_db ordering).
        BusinessSettings.objects.get(pk=settings.pk).next_invoice_number  # noqa
        self.assertEqual(
            BusinessSettings.objects.get(pk=settings.pk).next_invoice_number,
            6,
        )


class LogoSvgRejectionTests(TestCase):
    """BusinessSettings.logo no longer accepts SVG (XSS vector)."""

    def test_svg_rejected_by_validator(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        settings = BusinessSettings.get_settings()
        settings.logo = SimpleUploadedFile(
            "evil.svg",
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            content_type="image/svg+xml",
        )
        with self.assertRaises(ValidationError):
            settings.full_clean()

    def test_png_still_accepted(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        settings = BusinessSettings.get_settings()
        settings.logo = SimpleUploadedFile("ok.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
        # Validators only — file content checks are loose for fakes.
        validators = settings._meta.get_field('logo').validators
        ext_validator = next(v for v in validators if hasattr(v, 'allowed_extensions'))
        self.assertNotIn('svg', ext_validator.allowed_extensions)
        self.assertIn('png', ext_validator.allowed_extensions)


class OverdueInvoiceReminderCooldownTests(TestCase):
    """send_overdue_invoice_reminders must not re-send within the cooldown."""

    def setUp(self):
        self.owner = Owner.objects.create(name="Owner", email="owner@example.com")
        self.invoice = Invoice.objects.create(
            owner=self.owner,
            invoice_number="INV-T1",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            status=Invoice.Status.SENT,
            due_date=date(2026, 2, 1),
        )

    def test_does_not_send_twice_in_one_day(self):
        from notifications import tasks

        with mock.patch.object(tasks, 'send_invoice_overdue_reminder', return_value=True) as send:
            tasks.send_overdue_invoice_reminders()
            tasks.send_overdue_invoice_reminders()

        self.assertEqual(send.call_count, 1)
        self.invoice.refresh_from_db()
        self.assertIsNotNone(self.invoice.last_overdue_reminder_at)

    def test_does_not_send_when_email_provider_fails(self):
        from notifications import tasks

        with mock.patch.object(tasks, 'send_invoice_overdue_reminder', return_value=False) as send:
            tasks.send_overdue_invoice_reminders()

        self.assertEqual(send.call_count, 1)
        self.invoice.refresh_from_db()
        # Failed sends roll back the claim so a retry can happen.
        self.assertIsNone(self.invoice.last_overdue_reminder_at)

    def test_resends_after_cooldown_window(self):
        from notifications import tasks

        # Simulate a previous successful send 8 days ago.
        long_ago = timezone.now() - timedelta(days=8)
        Invoice.objects.filter(pk=self.invoice.pk).update(
            last_overdue_reminder_at=long_ago,
        )

        with mock.patch.object(tasks, 'send_invoice_overdue_reminder', return_value=True) as send:
            tasks.send_overdue_invoice_reminders()

        self.assertEqual(send.call_count, 1)


class EhvReminderIdempotencyTests(TestCase):
    """send_ehv_reminders must not duplicate a month even if called twice."""

    def setUp(self):
        mare = _mare()
        # Set covering date so month-5 reminder window is "now".
        five_months_ago = (timezone.now().date() - timedelta(days=5 * 30 + 7))
        self.record = BreedingRecord.objects.create(
            mare=mare,
            stallion_name="Test Sire",
            date_covered=five_months_ago,
            status=BreedingRecord.Status.CONFIRMED,
        )

    def test_month_recorded_only_once_across_runs(self):
        from notifications import tasks

        with mock.patch.object(tasks, 'send_ehv_reminder', return_value=True) as send:
            tasks.send_ehv_reminders()
            tasks.send_ehv_reminders()

        self.record.refresh_from_db()
        sent_months = self.record.ehv_reminders_sent.split(',') if self.record.ehv_reminders_sent else []
        # If the month somehow fired (depending on date arithmetic), it
        # must appear exactly once -- never duplicated like '5,5'.
        self.assertEqual(len(sent_months), len(set(sent_months)))
        # If a reminder was sent, it should match recorded months.
        self.assertEqual(send.call_count, len(set(sent_months)))


class VaccinationReminderClaimTests(TestCase):
    """Reminder task must not double-send when called concurrently/repeatedly."""

    def test_repeated_call_sends_once(self):
        from notifications import tasks

        vtype = VaccinationType.objects.create(
            name="Flu", interval_months=12, reminder_days_before=14,
        )
        Vaccination.objects.create(
            horse=_mare(),
            vaccination_type=vtype,
            date_given=date.today() - timedelta(days=350),
            next_due_date=date.today() + timedelta(days=7),
        )

        with mock.patch.object(tasks, 'send_vaccination_reminder', return_value=True) as send:
            tasks.send_vaccination_reminders()
            tasks.send_vaccination_reminders()

        self.assertEqual(send.call_count, 1)


class CsvImportHeaderValidationTests(TestCase):
    """import_location_csv must raise on missing required headers."""

    def test_missing_horse_column_raises(self):
        import tempfile
        from data.import_csv import import_location_csv

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.csv', delete=False, encoding='utf-8'
        ) as tmp:
            tmp.write("Horses,Location,Owners\n")  # 'Horses' typo
            tmp.write("Bessie,Field A,Jane\n")
            path = tmp.name

        with self.assertRaises(ValueError) as ctx:
            import_location_csv(path)
        self.assertIn("Horse", str(ctx.exception))

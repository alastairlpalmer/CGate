"""Health reminder defects from the November 2026 bug review.

3. Vet follow-ups stayed "overdue" for ever.
5. Two farrier visits on the same day sent "Farrier Due" twice.
6. Replaced vaccinations showed a red "Overdue" badge.
7. A recorded EHV dose stayed on the dashboard (and was emailed).
8. An egg count of exactly 200 EPG was "high" but never flagged.
9. A foal due within a week got both foal reminders in one email.
"""

from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard import attention
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from health.models import (
    BreedingRecord, FarrierVisit, Vaccination, VaccinationType, VetVisit, WormEggCount,
)
from notifications.tasks import breeding_reminder_entries, send_farrier_reminders


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin()
        self.owner = Owner.objects.create(name='Jo', email='jo@example.com')
        self.location = Location.objects.create(site='Colgate', name='Top Field')
        self.rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))

    def horse(self, name='Ghost'):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=self.location, rate_type=self.rate,
            start_date=self.today - timedelta(days=400),
        )
        return horse

    def kinds(self, kind):
        return [i for i in attention.collect(self.admin, today=self.today) if i.kind == kind]


class VetFollowUpTests(Base):

    def test_a_follow_up_is_done_once_a_later_visit_is_recorded(self):
        horse = self.horse()
        VetVisit.objects.create(
            horse=horse, date=self.today - timedelta(days=200), reason='Lame',
            follow_up_date=self.today - timedelta(days=186),
        )
        self.assertEqual(len(self.kinds('vet')), 1)
        VetVisit.objects.create(horse=horse, date=self.today - timedelta(days=185), reason='Re-check')
        self.assertEqual(self.kinds('vet'), [])


class EggCountThresholdTests(Base):

    def test_exactly_200_epg_is_flagged(self):
        horse = self.horse()
        count = WormEggCount.objects.create(horse=horse, date=self.today, count=200)
        self.assertTrue(count.is_high)
        self.assertEqual(len(self.kinds('egg_count')), 1)


class FarrierReminderTests(Base):

    def test_two_visits_on_the_same_day_send_one_email(self):
        horse = self.horse()
        for _ in range(2):
            FarrierVisit.objects.create(
                horse=horse, date=self.today - timedelta(days=50),
                next_due_date=self.today + timedelta(days=5),
            )
        send_farrier_reminders()
        send_farrier_reminders()
        self.assertEqual(len(mail.outbox), 1)


class ReplacedVaccinationTests(Base):

    def test_a_replaced_record_is_not_shown_as_overdue(self):
        self.client.force_login(self.admin)
        horse = self.horse()
        flu = VaccinationType.objects.create(name='Flu')
        Vaccination.objects.create(
            horse=horse, vaccination_type=flu, date_given=self.today - timedelta(days=400),
            next_due_date=self.today - timedelta(days=35),
        )
        Vaccination.objects.create(
            horse=horse, vaccination_type=flu, date_given=self.today - timedelta(days=30),
            next_due_date=self.today + timedelta(days=335),
        )
        for url in (reverse('health_dashboard') + '?type=vaccinations', reverse('vaccination_list')):
            response = self.client.get(url)
            self.assertNotContains(response, 'badge-danger">Overdue', msg_prefix=url)
            self.assertContains(response, 'Replaced', msg_prefix=url)


class EhvTests(Base):

    def setUp(self):
        super().setUp()
        self.mare = self.horse('Mare')
        # Covered 5 months ago, so the month-5 dose is due about now.
        covered = self.today - timedelta(days=150)
        self.record = BreedingRecord.objects.create(
            mare=self.mare, stallion_name='Stud', date_covered=covered,
            status=BreedingRecord.Status.CONFIRMED,
        )
        self.ehv = VaccinationType.objects.create(name='EHV 1,4')

    def test_a_recorded_dose_leaves_the_dashboard(self):
        self.assertEqual(len(self.kinds('ehv')), 1)
        Vaccination.objects.create(horse=self.mare, vaccination_type=self.ehv, date_given=self.today)
        self.assertEqual(self.kinds('ehv'), [])

    def test_no_email_for_a_dose_already_given(self):
        from notifications.tasks import send_ehv_reminders
        Vaccination.objects.create(horse=self.mare, vaccination_type=self.ehv, date_given=self.today)
        send_ehv_reminders()
        self.assertEqual(len(mail.outbox), 0)


class FoalReminderTests(Base):

    def test_a_foal_due_within_a_week_gets_one_reminder(self):
        record = BreedingRecord(
            mare=self.horse('Mare'), stallion_name='Stud',
            date_covered=self.today - timedelta(days=335),
            date_foal_due=self.today + timedelta(days=5),
            status=BreedingRecord.Status.CONFIRMED,
        )
        labels = [e['label'] for e in breeding_reminder_entries(record, self.today)]
        self.assertEqual(labels, ['Foal due in 5 days'])

    def test_thirty_days_out_gets_the_first_reminder(self):
        record = BreedingRecord(
            mare=self.horse('Mare'), stallion_name='Stud',
            date_covered=self.today - timedelta(days=310),
            date_foal_due=self.today + timedelta(days=30),
            status=BreedingRecord.Status.CONFIRMED,
        )
        entries = breeding_reminder_entries(record, self.today)
        self.assertEqual([(e['key'], e['label']) for e in entries], [('foal30', 'Foal due in 30 days')])

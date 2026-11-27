"""Bulk health forms check dates like the single-horse forms (pre-launch
review: one typo went on every selected horse)."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from health.forms import BulkFarrierVisitForm, BulkVaccinationForm, BulkVetVisitForm


class BulkDateCheckTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()

    def test_vaccination_next_due_before_the_date_given_is_refused(self):
        form = BulkVaccinationForm({'date_given': self.today, 'next_due_date': self.today - timedelta(days=1)})
        form.is_valid()
        self.assertIn('next_due_date', form.errors)

    def test_vaccination_in_the_future_is_refused(self):
        form = BulkVaccinationForm({'date_given': self.today + timedelta(days=30)})
        form.is_valid()
        self.assertIn('date_given', form.errors)

    def test_farrier_next_due_must_follow_the_visit(self):
        form = BulkFarrierVisitForm({'date': self.today, 'next_due_date': self.today})
        form.is_valid()
        self.assertIn('next_due_date', form.errors)

    def test_vet_follow_up_must_follow_the_visit(self):
        form = BulkVetVisitForm({'date': self.today, 'follow_up_date': self.today - timedelta(days=2)})
        form.is_valid()
        self.assertIn('follow_up_date', form.errors)

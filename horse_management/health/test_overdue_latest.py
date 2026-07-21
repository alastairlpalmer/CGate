"""Regression tests for latest-record-only overdue logic."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Owner
from health.models import FarrierVisit, Vaccination, VaccinationType


class OverdueLatestOnlyTests(TestCase):
    """Dashboard/list overdue logic must consider only each horse's latest
    record per (horse, type) — superseded records keep a past next_due_date
    forever and showed horses as permanently overdue."""

    def setUp(self):
        from core.roles_testutils import make_admin
        self.client.force_login(make_admin(username='health-admin'))
        self.owner = Owner.objects.create(name='Sue')
        self.horse = Horse.objects.create(name='Dobbin', is_active=True)
        self.vt = VaccinationType.objects.create(
            name='Flu', interval_months=12, reminder_days_before=30,
        )

    def _pair(self):
        """Superseded overdue record + fresh current one."""
        today = timezone.now().date()
        Vaccination.objects.create(
            horse=self.horse, vaccination_type=self.vt,
            date_given=today - timedelta(days=400),
            next_due_date=today - timedelta(days=35),
        )
        Vaccination.objects.create(
            horse=self.horse, vaccination_type=self.vt,
            date_given=today - timedelta(days=1),
            next_due_date=today + timedelta(days=364),
        )

    def test_overview_ignores_superseded_vaccinations(self):
        self._pair()
        response = self.client.get(reverse('health_dashboard'))
        self.assertEqual(response.context['stat_overdue_vax'], 0)
        self.assertEqual(response.context['action_required'], [])

    def test_vaccination_list_overdue_filter_ignores_superseded(self):
        self._pair()
        response = self.client.get(
            reverse('vaccination_list'), {'status': 'overdue'}
        )
        self.assertEqual(len(response.context['vaccinations']), 0)

    def test_unsuperseded_overdue_still_shows(self):
        today = timezone.now().date()
        Vaccination.objects.create(
            horse=self.horse, vaccination_type=self.vt,
            date_given=today - timedelta(days=400),
            next_due_date=today - timedelta(days=35),
        )
        response = self.client.get(reverse('health_dashboard'))
        self.assertEqual(response.context['stat_overdue_vax'], 1)

    def test_farrier_overdue_ignores_superseded_visit(self):
        today = timezone.now().date()
        FarrierVisit.objects.create(
            horse=self.horse, date=today - timedelta(days=90),
            next_due_date=today - timedelta(days=48),
        )
        FarrierVisit.objects.create(
            horse=self.horse, date=today - timedelta(days=2),
            next_due_date=today + timedelta(days=40),
        )
        response = self.client.get(reverse('health_dashboard'))
        self.assertEqual(response.context['action_required'], [])

"""Query-count regression tests for the heaviest pages.

These don't assert exact counts (auth/session middleware adds a few); they cap
the total so reintroducing per-row queries or redundant aggregate round trips
fails the suite. Thresholds are set with headroom above the measured count.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from health.models import FarrierVisit, Vaccination, VaccinationType

User = get_user_model()


class QueryCountTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User(
            username='perfuser',
            last_login=timezone.now(),
            date_joined=timezone.now(),
            is_active=True,
            is_staff=True,
        )
        cls.user.set_password('x')
        cls.user.save()

        today = timezone.now().date()
        rate = RateType.objects.create(name='Grass', daily_rate=10)
        vax_type = VaccinationType.objects.create(name='Flu')

        # Enough rows that any per-row query pattern shows up in the counts
        for i in range(12):
            owner = Owner.objects.create(name=f'Owner {i}')
            location = Location.objects.create(name=f'Field {i}', site='Main')
            horse = Horse.objects.create(name=f'Horse {i}')
            OwnershipShare.objects.create(horse=horse, owner=owner, share_percentage=100)
            Placement.objects.create(
                horse=horse, owner=owner, location=location,
                rate_type=rate, start_date=today - timedelta(days=100),
            )
            Vaccination.objects.create(
                horse=horse, vaccination_type=vax_type,
                date_given=today - timedelta(days=300),
                next_due_date=today + timedelta(days=10 if i % 2 else -10),
            )
            FarrierVisit.objects.create(
                horse=horse, date=today - timedelta(days=40),
                next_due_date=today + timedelta(days=5 if i % 2 else -5),
            )

    def setUp(self):
        self.client.force_login(self.user)

    def assertMaxQueries(self, url, limit):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(ctx.captured_queries), limit,
            f"{url} ran {len(ctx.captured_queries)} queries (limit {limit}):\n"
            + "\n".join(q['sql'][:120] for q in ctx.captured_queries),
        )

    def test_health_overview_query_count(self):
        # 8 data queries + auth/session; the old .count() round trips would
        # push this past the limit.
        self.assertMaxQueries(reverse('health_dashboard'), 14)

    def test_health_tab_query_count(self):
        self.assertMaxQueries(reverse('health_dashboard') + '?type=vaccinations', 10)

    def test_horse_list_query_count(self):
        # Constant queries regardless of horse count (prefetches, no N+1)
        self.assertMaxQueries(reverse('horse_list'), 14)

    def test_dashboard_query_count(self):
        # The dashboard view catches all exceptions and re-renders an empty
        # fallback, which can mask real errors (it hid a broken UNION query
        # once) — so also assert nothing was logged and the chart rendered.
        with self.assertNoLogs('core.views.dashboard', level='ERROR'):
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(ctx.captured_queries), 22)
        self.assertTrue(
            response.context['chart_data']['monthly']['labels'],
            'dashboard rendered the empty fallback context',
        )

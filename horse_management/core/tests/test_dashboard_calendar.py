"""The dashboard's month calendar (core/dashboard/calendar.py).

docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP4: it replaced the Next 14 days
strip and took the Needs action card's place beside the map.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard import attention
from core.dashboard import calendar as cal
from core.models import DashboardPreference, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access


class GridTests(SimpleTestCase):

    def test_grid_starts_on_monday_and_ends_on_sunday(self):
        start, end = cal.grid_bounds(date(2026, 11, 1))
        self.assertEqual(start, date(2026, 10, 26))
        self.assertEqual(start.weekday(), 0)
        self.assertEqual(end, date(2026, 12, 6))
        self.assertEqual(end.weekday(), 6)

    def test_parse_month(self):
        today = date(2026, 11, 18)
        self.assertEqual(cal.parse_month('2026-12', today), date(2026, 12, 1))
        self.assertEqual(cal.parse_month('rubbish', today), date(2026, 11, 1))
        self.assertEqual(cal.parse_month(None, today), date(2026, 11, 1))
        # No further than a year either way.
        self.assertEqual(cal.parse_month('2030-01', today), date(2027, 11, 1))
        self.assertEqual(cal.parse_month('2020-01', today), date(2025, 11, 1))

    def test_parse_day_must_be_in_the_month(self):
        month = date(2026, 11, 1)
        self.assertEqual(cal.parse_day('2026-11-26', month), date(2026, 11, 26))
        self.assertIsNone(cal.parse_day('2026-12-01', month))
        self.assertIsNone(cal.parse_day('nope', month))

    def test_horizon_reaches_the_grid_end(self):
        today = date(2026, 11, 18)
        self.assertEqual(cal.horizon_days(date(2026, 11, 1), today), (date(2026, 12, 6) - today).days + 1)
        self.assertEqual(cal.horizon_days(date(2025, 11, 1), today), 1)

    def _item(self, due, kind='farrier', severity='due'):
        return attention.AttentionItem(kind=kind, severity=severity, title='Ida', detail='', url='/', due_date=due)

    def test_build_puts_items_on_their_days(self):
        today = date(2026, 11, 18)
        items = [
            self._item(date(2026, 11, 26)),
            self._item(date(2026, 11, 26), kind='vaccination'),
            self._item(date(2026, 11, 3), severity='overdue'),
        ]
        view = cal.build(items, today, date(2026, 11, 1))
        days = {d['date']: d for week in view['weeks'] for d in week}
        self.assertEqual(len(view['weeks']), 6)
        self.assertEqual(days[date(2026, 11, 26)]['count'], 2)
        self.assertEqual(days[date(2026, 11, 26)]['dots'], ['farrier', 'vaccination'])
        self.assertTrue(days[date(2026, 11, 3)]['overdue'])
        self.assertTrue(days[today]['is_today'])
        self.assertTrue(days[today]['is_selected'])
        self.assertFalse(days[date(2026, 10, 26)]['in_month'])
        self.assertEqual(view['total'], 3)
        self.assertEqual(view['overdue'], 1)
        self.assertEqual([e['kind'] for e in view['legend']], ['farrier', 'vaccination'])

    def test_another_month_selects_its_first_busy_day(self):
        today = date(2026, 11, 18)
        view = cal.build([self._item(date(2026, 12, 9))], today, date(2026, 12, 1))
        self.assertEqual(view['selected'], date(2026, 12, 9))
        self.assertEqual(len(view['selected_items']), 1)
        self.assertFalse(view['is_this_month'])

    def test_arrows_stop_a_year_out(self):
        today = date(2026, 11, 18)
        self.assertIsNone(cal.build([], today, date(2027, 11, 1))['next'])
        self.assertIsNone(cal.build([], today, date(2025, 11, 1))['previous'])
        self.assertEqual(cal.build([], today, date(2026, 11, 1))['next'], date(2026, 12, 1))


class CalendarViewTests(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.user = make_admin('calendarer')
        self.client.force_login(self.user)
        owner = Owner.objects.create(name='Roz Tobin')
        field = Location.objects.create(site='Colgate', name='Top Paddock')
        rate = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        self.horse = Horse.objects.create(name='Ida')
        Placement.objects.create(
            horse=self.horse, owner=owner, location=field, rate_type=rate,
            start_date=self.today - timedelta(days=30), expected_departure=self.today,
        )

    def test_dashboard_draws_this_month(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'id="dash-calendar"')
        # The title also holds "Next 7 days", which a phone shows in its place.
        self.assertContains(response, f'<span class="cal-in-month">{self.today:%B %Y}</span></h2>')
        self.assertContains(response, 'aria-current="date"')
        # Today is chosen, so its list shows the expected departure.
        self.assertContains(response, 'class="attn-title">Ida</a>')
        self.assertNotContains(response, 'id="needs-action"')

    def test_month_arrows_and_days_ask_for_the_card_again(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, f'hx-get="{reverse("dashboard_calendar")}?month=')
        self.assertContains(response, 'hx-target="#dash-calendar"')

    def test_partial_for_another_month(self):
        later = (self.today.replace(day=1) + timedelta(days=40)).replace(day=1)
        response = self.client.get(reverse('dashboard_calendar'), {'month': f'{later:%Y-%m}'})
        self.assertContains(response, f'<span class="cal-in-month">{later:%B %Y}</span></h2>')
        self.assertContains(response, '>Today</button>')
        self.assertNotContains(response, '<html')

    def test_partial_for_a_chosen_day(self):
        response = self.client.get(reverse('dashboard_calendar'), {
            'month': f'{self.today:%Y-%m}', 'day': self.today.isoformat(),
        })
        self.assertEqual(response.context['calendar']['selected'], self.today)
        self.assertContains(response, 'class="attn-title">Ida</a>')

    def test_switched_off_calendar_answers_nothing(self):
        pref = DashboardPreference.get_for(self.user)
        pref.layout = {'upcoming': {'visible': False, 'order': 0}}
        pref.save()
        self.assertEqual(self.client.get(reverse('dashboard_calendar')).content, b'')
        self.assertNotContains(self.client.get(reverse('dashboard')), 'id="dash-calendar"')

    def test_needs_the_dashboard(self):
        self.client.force_login(make_user_with_access('nodash', horses='view'))
        self.assertIn(self.client.get(reverse('dashboard_calendar')).status_code, (302, 403))

    def test_headline_opens_the_notification_centre(self):
        Placement.objects.filter(horse=self.horse).update(expected_departure=self.today)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn("$store.notif.show('action')", body)

"""General items on the dashboard calendar (core.models.CalendarEntry)."""

from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard import attention, calendar as calendar_data
from core.models import CalendarEntry, Location
from core.roles_testutils import make_admin, make_user_with_access

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class CalendarEntryTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin(username='cal-admin')
        self.client.force_login(self.admin)

    def entry(self, days=0, **kwargs):
        kwargs.setdefault('title', 'Hay delivery')
        return CalendarEntry.objects.create(date=self.today + timedelta(days=days), **kwargs)

    def items(self, user=None):
        return [i for i in attention.collect(user or self.admin, today=self.today) if i.kind == 'calendar']

    # ── The form ──

    def test_the_chosen_day_has_an_add_button(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'data-testid="calendar-add"')
        self.assertContains(response, f'?date={self.today:%Y-%m-%d}')

    def test_adding_fills_the_day_and_saves(self):
        day = self.today + timedelta(days=3)
        url = reverse('calendar_entry_add') + f'?date={day:%Y-%m-%d}'
        response = self.client.get(url, **POPUP)
        self.assertContains(response, f'value="{day:%Y-%m-%d}"')
        response = self.client.post(reverse('calendar_entry_add'), {
            'title': '  Yard meeting ', 'date': f'{day:%Y-%m-%d}', 'time': '18:30',
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers['HX-Trigger'], 'popup:saved')
        entry = CalendarEntry.objects.get()
        self.assertEqual((entry.title, entry.date, entry.time), ('Yard meeting', day, time(18, 30)))
        self.assertEqual(entry.created_by, self.admin)

    def test_a_title_is_needed(self):
        response = self.client.post(reverse('calendar_entry_add'), {
            'title': '   ', 'date': f'{self.today:%Y-%m-%d}',
        }, **POPUP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Say what is on this day.')
        self.assertFalse(CalendarEntry.objects.exists())

    def test_edit_and_delete(self):
        entry = self.entry(2)
        url = reverse('calendar_entry_edit', args=[entry.pk])
        response = self.client.get(url, **POPUP)
        self.assertContains(response, 'name="delete"')
        self.client.post(url, {'title': 'Straw delivery', 'date': f'{entry.date:%Y-%m-%d}'}, **POPUP)
        entry.refresh_from_db()
        self.assertEqual(entry.title, 'Straw delivery')
        response = self.client.post(url, {'delete': '1'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(CalendarEntry.objects.exists())

    def test_outside_the_sheet_it_goes_to_the_dashboard(self):
        response = self.client.get(reverse('calendar_entry_add'))
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_a_role_without_the_dashboard_cannot_add(self):
        user = make_user_with_access(username='no-dash', horses='view')
        self.client.force_login(user)
        self.client.post(reverse('calendar_entry_add'), {
            'title': 'X', 'date': f'{self.today:%Y-%m-%d}',
        }, **POPUP)
        self.assertFalse(CalendarEntry.objects.exists())

    # ── Where it shows ──

    def test_today_is_a_notification_and_a_past_day_is_never_overdue(self):
        self.entry(0, title='Farrier lunch')
        self.entry(-5, title='Show')
        self.entry(4, title='Hay delivery')
        items = {i.title: i for i in self.items()}
        self.assertTrue(attention.is_inbox(items['Farrier lunch'], self.today))
        self.assertEqual(items['Show'].severity, 'info')
        self.assertFalse(attention.is_inbox(items['Show'], self.today))
        self.assertFalse(attention.is_inbox(items['Hay delivery'], self.today))

    def test_the_calendar_names_a_lone_item_by_its_title(self):
        self.entry(1, title='Hay delivery')
        self.entry(2, title='A')
        self.entry(2, title='B')
        month = self.today.replace(day=1)
        cal = calendar_data.build(self.items(), self.today, month)
        days = {d['date']: d for w in cal['weeks'] for d in w}
        one = days.get(self.today + timedelta(days=1))
        two = days.get(self.today + timedelta(days=2))
        if one is not None:
            self.assertIn({'kind': 'calendar', 'text': 'Hay delivery'}, one['labels'])
        if two is not None:
            self.assertIn({'kind': 'calendar', 'text': 'Calendar item 2'}, two['labels'])

    def test_a_location_puts_it_on_that_site(self):
        field = Location.objects.create(name='Top', site='Colgate')
        self.entry(1, location=field, time=time(9, 0), notes='Two tonnes\nmore text')
        [item] = self.items()
        self.assertEqual((item.site, item.location), ('Colgate', 'Top'))
        self.assertEqual(item.detail, '09:00 · Two tonnes')
        elsewhere = attention.collect(self.admin, today=self.today, site='Somerford')
        self.assertEqual([i for i in elsewhere if i.kind == 'calendar'], [])


class RepeatTests(TestCase):
    """A repeating item is one row; the calendar shows each of its days."""

    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin(username='repeat-admin')
        self.client.force_login(self.admin)

    def make(self, first, repeat, until=None, **kwargs):
        return CalendarEntry.objects.create(
            title=kwargs.pop('title', 'Hay delivery'), date=first, repeat=repeat,
            repeat_until=until, **kwargs)

    def test_weekly_and_fortnightly(self):
        first = date(2026, 1, 5)  # a Monday
        weekly = self.make(first, 'weekly')
        self.assertEqual(
            weekly.occurrences(date(2026, 1, 1), date(2026, 1, 31)),
            [date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19), date(2026, 1, 26)])
        fortnightly = self.make(first, 'fortnightly')
        self.assertEqual(
            fortnightly.occurrences(date(2026, 1, 10), date(2026, 2, 28)),
            [date(2026, 1, 19), date(2026, 2, 2), date(2026, 2, 16)])

    def test_monthly_on_the_31st_uses_the_last_day_of_a_short_month(self):
        entry = self.make(date(2026, 1, 31), 'monthly')
        self.assertEqual(
            entry.occurrences(date(2026, 1, 1), date(2026, 4, 30)),
            [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])

    def test_yearly_and_daily_with_an_end(self):
        yearly = self.make(date(2024, 2, 29), 'yearly')
        self.assertEqual(
            yearly.occurrences(date(2025, 1, 1), date(2028, 12, 31)),
            [date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29)])
        daily = self.make(date(2026, 3, 1), 'daily', until=date(2026, 3, 3))
        self.assertEqual(
            daily.occurrences(date(2026, 2, 1), date(2026, 3, 31)),
            [date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3)])

    def test_a_far_start_jumps_without_missing_a_day(self):
        entry = self.make(date(2020, 1, 15), 'monthly')
        self.assertEqual(entry.occurrences(date(2026, 3, 1), date(2026, 4, 30)),
                         [date(2026, 3, 15), date(2026, 4, 15)])

    def test_each_day_is_its_own_item_and_today_is_a_notification(self):
        entry = self.make(self.today - timedelta(days=7), 'weekly')
        items = [i for i in attention.collect(self.admin, today=self.today) if i.kind == 'calendar']
        days = [i.due_date for i in items]
        self.assertIn(self.today, days)
        self.assertIn(self.today + timedelta(days=7), days)
        self.assertEqual(len(set(i.key for i in items)), len(items))
        today = next(i for i in items if i.due_date == self.today)
        self.assertTrue(attention.is_inbox(today, self.today))
        self.assertIn('Every week', today.detail)
        self.assertIn(f'?on={self.today:%Y-%m-%d}', today.url)
        self.assertEqual(entry.repeat_label, 'Every week')

    def test_the_form_saves_a_repeat_and_checks_the_end(self):
        response = self.client.post(reverse('calendar_entry_add'), {
            'title': 'Yard meeting', 'date': '2026-12-01', 'repeat': 'monthly', 'repeat_until': '2026-11-01',
        }, **POPUP)
        self.assertContains(response, 'The last day must be on or after the first day.')
        self.client.post(reverse('calendar_entry_add'), {
            'title': 'Yard meeting', 'date': '2026-12-01', 'repeat': 'monthly', 'repeat_until': '2027-06-01',
        }, **POPUP)
        entry = CalendarEntry.objects.get()
        self.assertEqual((entry.repeat, entry.repeat_until), ('monthly', date(2027, 6, 1)))
        # "Until" is cleared for an item that does not repeat.
        self.client.post(reverse('calendar_entry_edit', args=[entry.pk]), {
            'title': 'Yard meeting', 'date': '2026-12-01', 'repeat': '', 'repeat_until': '2027-06-01',
        }, **POPUP)
        entry.refresh_from_db()
        self.assertEqual((entry.repeat, entry.repeat_until), ('', None))

    def test_delete_this_day_only_or_all(self):
        entry = self.make(date(2026, 1, 5), 'weekly')
        url = reverse('calendar_entry_edit', args=[entry.pk]) + '?on=2026-01-12'
        response = self.client.get(url, **POPUP)
        self.assertContains(response, 'name="delete" value="day"')
        self.assertContains(response, 'A change here applies to every day of it.')
        response = self.client.post(url, {'delete': 'day'}, **POPUP)
        self.assertEqual(response.status_code, 204)
        entry.refresh_from_db()
        self.assertEqual(entry.skip_dates, ['2026-01-12'])
        self.assertEqual(entry.occurrences(date(2026, 1, 1), date(2026, 1, 20)),
                         [date(2026, 1, 5), date(2026, 1, 19)])
        self.client.post(url, {'delete': 'all'}, **POPUP)
        self.assertFalse(CalendarEntry.objects.exists())

    def test_a_single_item_has_one_delete(self):
        entry = self.make(self.today, '')
        response = self.client.get(reverse('calendar_entry_edit', args=[entry.pk]), **POPUP)
        self.assertNotContains(response, 'value="day"')
        self.assertContains(response, '>Delete</button>')

    def test_a_past_day_says_ago_not_overdue(self):
        past = self.today - timedelta(days=3)
        self.make(past, '', title='Show')
        response = self.client.get(
            reverse('dashboard_calendar') + f'?month={past:%Y-%m}&day={past:%Y-%m-%d}')
        self.assertContains(response, '3 days ago')
        self.assertNotContains(response, 'days overdue')

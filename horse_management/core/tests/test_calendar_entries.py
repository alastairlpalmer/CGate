"""General items on the dashboard calendar (core.models.CalendarEntry)."""

from datetime import time, timedelta

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

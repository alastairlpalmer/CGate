"""Reminder switches (core/reminders.py).

A yard chooses which kinds of reminder its dashboard shows. The farrier
starts switched off: the yard sees its horses' feet every day. A kind
that is off leaves the dashboard and the dots on the horse list, and
stays on its own page.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import reminders
from core.dashboard import attention
from core.models import BusinessSettings, Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from health.models import FarrierVisit, Vaccination, VaccinationType


class ReminderSwitchTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin(username='reminder-admin')
        owner = Owner.objects.create(name='Jo Bloggs')
        rate = RateType.objects.create(name='Grass', daily_rate=5)
        field = Location.objects.create(name='Top Field', site='Home')
        self.horse = Horse.objects.create(name='Bramble')
        Placement.objects.create(
            horse=self.horse, owner=owner, location=field, rate_type=rate,
            start_date=self.today - timedelta(days=100),
        )
        FarrierVisit.objects.create(
            horse=self.horse, date=self.today - timedelta(days=60),
            next_due_date=self.today - timedelta(days=4),
        )
        Vaccination.objects.create(
            horse=self.horse, vaccination_type=VaccinationType.objects.create(name='Flu'),
            date_given=self.today - timedelta(days=400),
            next_due_date=self.today - timedelta(days=10),
        )

    def kinds(self, **kwargs):
        return {item.kind for item in attention.collect(self.admin, today=self.today, **kwargs)}

    def test_farrier_is_off_and_vaccinations_on_for_a_new_yard(self):
        self.assertEqual(reminders.muted_keys(), {'farrier'})
        kinds = self.kinds()
        self.assertIn('vaccination', kinds)
        self.assertNotIn('farrier', kinds)

    def test_switching_the_farrier_on_brings_it_back(self):
        reminders.set_reminder('farrier', True)
        self.assertIn('farrier', self.kinds())

    def test_switching_vaccinations_off_removes_them(self):
        reminders.set_reminder('vaccination', False)
        self.assertNotIn('vaccination', self.kinds())

    def test_a_page_that_names_its_kinds_still_gets_them(self):
        # The Farrier page lists who is due whatever the switch says.
        self.assertIn('farrier', self.kinds(kinds={'farrier'}))
        self.assertEqual(len(attention.farrier_due(self.admin, today=self.today)), 1)

    def test_include_muted_returns_everything(self):
        self.assertIn('farrier', self.kinds(include_muted=True))

    def test_departures_switch_covers_both_collectors(self):
        reminders.set_reminder('departure', False)
        self.assertLessEqual({'departure', 'departure_expected'}, reminders.muted_collectors())

    def test_a_bad_stored_value_falls_back_to_the_default(self):
        business = BusinessSettings.get_settings()
        business.muted_reminders = 'farrier'
        self.assertEqual(reminders.muted_keys(business), {'farrier'})
        business.muted_reminders = ['farrier', 'not-a-kind']
        self.assertEqual(reminders.muted_keys(business), {'farrier'})

    def test_dashboard_leaves_the_farrier_out_until_switched_on(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'Record farrier visit')
        reminders.set_reminder('farrier', True)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Record farrier visit')

    def test_horse_list_puts_no_farrier_dot_while_off(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('horse_list'))
        self.assertNotContains(response, 'Farrier overdue')
        reminders.set_reminder('farrier', True)
        response = self.client.get(reverse('horse_list'))
        self.assertContains(response, 'Farrier overdue')

    def test_toggle_saves_and_settings_page_shows_the_switches(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('reminder_toggle'), {'key': 'farrier', 'on': 'true'})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(reminders.muted_keys(), set())
        response = self.client.post(reverse('reminder_toggle'), {'key': 'worming', 'on': 'false'})
        self.assertEqual(reminders.muted_keys(), {'worming'})
        page = self.client.get(reverse('app_settings'))
        self.assertContains(page, 'data-reminder-key="farrier"')
        self.assertContains(page, 'data-reminder-key="vaccination"')

    def test_toggle_refuses_bad_input(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('reminder_toggle'), {'key': 'nope', 'on': 'true'}).status_code, 400)
        self.assertEqual(self.client.post(reverse('reminder_toggle'), {'key': 'farrier', 'on': 'maybe'}).status_code, 400)
        self.assertEqual(self.client.get(reverse('reminder_toggle')).status_code, 405)

    def test_toggle_needs_settings_access(self):
        user = make_user_with_access('yard-hand', dashboard='full', health='full')
        self.client.force_login(user)
        response = self.client.post(reverse('reminder_toggle'), {'key': 'farrier', 'on': 'true'})
        self.assertNotEqual(response.status_code, 204)
        self.assertEqual(reminders.muted_keys(), {'farrier'})

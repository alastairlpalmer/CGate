"""Layout fixes: no empty app bar on a computer, Add in the sidebar, the
Filters button in each list's own row, and the size setting.
"""

import re

from django.test import TestCase
from django.urls import reverse

from core.models import Horse, UserProfile
from core.roles_testutils import make_admin, make_user_with_access

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


class AppBarTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin('layout'))

    def test_app_bar_is_for_phones_only(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        bar = re.search(r'<div class="app-bar ([^"]+)">', html).group(1)
        self.assertIn('lg:hidden', bar)

    def test_add_menu_is_in_the_sidebar(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        sidebar = html[html.index('<aside class="app-sidebar'):html.index('</aside>')]
        self.assertIn('class="side-add-btn"', sidebar)
        self.assertIn(reverse('horse_new_arrival'), sidebar)
        self.assertIn(reverse('owner_create'), sidebar)

    def test_no_add_button_without_full_access(self):
        self.client.force_login(make_user_with_access('looker', horses='view'))
        html = self.client.get(reverse('horse_list')).content.decode()
        self.assertNotIn('side-add-btn', html)

    def test_each_filtered_list_has_a_row_for_the_filters_button(self):
        for name in ('horse_list', 'invoice_list', 'charge_list', 'costs_list'):
            with self.subTest(page=name):
                html = self.client.get(reverse(name)).content.decode()
                self.assertEqual(html.count('data-filter-slot class='), 1)
                self.assertIn('x-teleport="[data-filter-slot]"', html)


class DisplaySizeTests(TestCase):

    def setUp(self):
        self.user = make_admin('sizer', email='sizer@example.com')
        self.client.force_login(self.user)

    def _size(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        return re.search(r'<html lang="en" data-ui-size="([a-z]+)">', html).group(1)

    def test_compact_is_the_default(self):
        self.assertEqual(self._size(), 'compact')

    def test_profile_choice_applies_at_once(self):
        self.client.post(reverse('profile'), {
            'action': 'details', 'first_name': 'S', 'last_name': 'Z', 'email': 'sizer@example.com',
            'phone': '', 'display_size': 'large',
        }, **POPUP)
        self.assertEqual(UserProfile.objects.get(user=self.user).display_size, 'large')
        self.assertEqual(self._size(), 'large')

    def test_choice_is_read_again_at_sign_in(self):
        UserProfile.objects.create(user=self.user, display_size='medium')
        self.client.logout()
        self.client.force_login(self.user)
        self.assertEqual(self._size(), 'medium')

    def test_an_unknown_value_falls_back_to_compact(self):
        session = self.client.session
        session['_display_size'] = 'gigantic'
        session.save()
        self.assertEqual(self._size(), 'compact')


class HorseSubtitleTests(TestCase):

    def test_no_stray_dot_when_nothing_is_known(self):
        self.client.force_login(make_admin('dots'))
        horse = Horse.objects.create(name='Plain')
        html = self.client.get(reverse('horse_detail', args=[horse.pk])).content.decode()
        self.assertNotIn('dot-list', html)
        self.assertNotRegex(html, r'>\s*&middot;\s*<')

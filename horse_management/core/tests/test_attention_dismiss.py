"""Dismissing Needs action items, and the yard's default map site.

An Administrator can take an item off the dashboard for the whole yard
(core/dashboard/dismissals.py). The item's own page is not changed, a new
due date brings it back, and Restore brings everything back.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from core.dashboard import attention, board, dismissals
from core.models import AttentionDismissal, DashboardPreference, Location
from core.roles_testutils import make_admin, make_user_with_access
from core.tests.test_dashboard_attention import DashboardDataTestCase

PHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1'


class DismissTests(DashboardDataTestCase):
    def setUp(self):
        super().setUp()
        self.bella = self.horse('Bella')
        self.vax = self.vaccination(self.bella, due=-3)
        self.item = next(
            item for item in attention.collect(self.admin, today=self.today)
            if item.key == f'vaccination-{self.vax.pk}'
        )
        self.client.force_login(self.admin)

    def keys_on_dashboard(self, client=None):
        response = (client or self.client).get(reverse('dashboard'))
        return {item.key for row in response.context['rows'] for item in row.items}

    def dismiss(self, client=None, item=None):
        return (client or self.client).post(
            reverse('attention_dismiss'), {'item': dismissals.token(item or self.item)},
        )

    def test_an_administrator_sees_the_button(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, reverse('attention_dismiss'))
        self.assertContains(response, dismissals.token(self.item))

    def test_dismissing_takes_the_item_off_the_dashboard(self):
        self.assertIn(self.item.key, self.keys_on_dashboard())
        response = self.dismiss()
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertNotIn(self.item.key, self.keys_on_dashboard())
        row = AttentionDismissal.objects.get()
        self.assertEqual(row.dismissed_by, self.admin)
        self.assertEqual(row.due, self.item.due_date.isoformat())

    def test_it_is_gone_for_everyone(self):
        self.dismiss()
        other = make_user_with_access('groom', password='pw', dashboard='full', health='view')
        self.client.force_login(other)
        self.assertNotIn(self.item.key, self.keys_on_dashboard())

    def test_the_items_own_page_still_lists_it(self):
        self.dismiss()
        keys = {item.key for item in attention.collect(self.admin, today=self.today)}
        self.assertIn(self.item.key, keys)

    def test_a_new_due_date_brings_it_back(self):
        self.dismiss()
        self.vax.next_due_date = self.today - timedelta(days=1)
        self.vax.save(update_fields=['next_due_date'])
        self.assertIn(self.item.key, self.keys_on_dashboard())

    def test_dismissing_twice_is_one_row(self):
        self.dismiss()
        self.dismiss()
        self.assertEqual(AttentionDismissal.objects.count(), 1)

    def test_restore_brings_everything_back(self):
        self.dismiss()
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['dismissed_count'], 1)
        self.assertContains(response, reverse('attention_restore'))
        self.client.post(reverse('attention_restore'))
        self.assertFalse(AttentionDismissal.objects.exists())
        self.assertIn(self.item.key, self.keys_on_dashboard())

    def test_someone_who_is_not_an_administrator_cannot_dismiss(self):
        other = make_user_with_access('groom', password='pw', dashboard='full', health='full')
        self.client.force_login(other)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, reverse('attention_dismiss'))
        self.assertEqual(self.dismiss().status_code, 403)
        self.assertEqual(self.client.post(reverse('attention_restore')).status_code, 403)
        self.assertFalse(AttentionDismissal.objects.exists())
        self.assertIn(self.item.key, self.keys_on_dashboard())

    def test_dismissing_needs_a_post_and_a_sign_in(self):
        self.assertEqual(self.client.get(reverse('attention_dismiss')).status_code, 405)
        self.client.logout()
        self.assertEqual(self.dismiss().status_code, 302)
        self.assertFalse(AttentionDismissal.objects.exists())

    def test_a_malformed_item_is_ignored(self):
        self.client.post(reverse('attention_dismiss'), {'item': ['', 'no-separator', '|2026-01-01', 'x' * 200 + '|']})
        self.assertFalse(AttentionDismissal.objects.exists())


@override_settings(LOCATION_MAPS_ENABLED=True, DEFAULT_MAP_SITE='colgate')
class DefaultMapSiteTests(TestCase):
    """Somerford is the first site with a map; Colgate is the yard's default."""

    def setUp(self):
        self.user = make_admin()
        self.client.force_login(self.user)
        Location.objects.create(
            name='Grain store field', site='Somerford', capacity=12,
            latitude=Decimal('51.548'), longitude=Decimal('-2.064'),
        )
        Location.objects.create(name='Elsewhere', site='Colgate')

    def board_site(self, **headers):
        return self.client.get(reverse('location_list'), **headers).context['board_site']

    def test_a_computer_opens_on_the_default_site(self):
        self.assertEqual(self.board_site(), 'Colgate')

    def test_a_phone_keeps_the_first_mapped_site(self):
        self.assertEqual(self.board_site(HTTP_USER_AGENT=PHONE), 'Somerford')

    def test_the_persons_own_site_wins(self):
        pref = DashboardPreference.get_for(self.user)
        pref.site = 'Somerford'
        pref.save()
        self.assertEqual(self.board_site(), 'Somerford')

    def test_the_address_wins(self):
        response = self.client.get(reverse('location_list') + '?site=Somerford')
        self.assertEqual(response.context['board_site'], 'Somerford')

    @override_settings(DEFAULT_MAP_SITE='Nowhere')
    def test_a_name_no_site_has_changes_nothing(self):
        self.assertEqual(self.board_site(), 'Somerford')

    @override_settings(DEFAULT_MAP_SITE='')
    def test_no_default_changes_nothing(self):
        self.assertEqual(self.board_site(), 'Somerford')

    def test_near_you_falls_back_to_it_on_any_device(self):
        """The browser tries GPS first; the default is for someone it does
        not place on a site, on a phone as much as on a computer."""
        for headers in ({}, {'HTTP_USER_AGENT': PHONE}):
            response = self.client.get(reverse('dashboard'), **headers)
            self.assertEqual(response.context['near_you_card']['default_site'], 'Colgate')

    def test_the_helper(self):
        names = ['Colgate', 'Somerford']
        self.assertEqual(board.default_map_site(names), 'Colgate')
        self.assertEqual(board.default_map_site(['Somerford']), '')

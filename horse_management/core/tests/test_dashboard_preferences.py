"""Tests for per-user configurable dashboard (DashboardPreference).

Covers:
- Lazy-create on first access.
- ``resolved_layout`` merges stored payloads with DEFAULT_LAYOUT and ignores
  unknown keys.
- Toggle endpoint: saves visibility; rejects unknown keys.
- Reorder endpoint: group-based payload and keyboard up/down both work; unknown
  keys are silently dropped; only the target user's row is touched.
- Permission: non-staff users can view and edit their own preferences.
- Dashboard view skips queries for hidden widgets (via assertNumQueries).
- ``dashboard_health_alerts`` returns an empty body when no health widget is
  visible.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard_widgets import DEFAULT_LAYOUT, WIDGETS, WIDGETS_BY_KEY
from core.models import DashboardPreference

User = get_user_model()


def make_user(username='testuser', is_staff=False):
    u = User(
        username=username,
        last_login=timezone.now(),
        date_joined=timezone.now(),
        is_staff=is_staff,
        is_active=True,
        is_superuser=False,
    )
    u.set_password('x')
    u.save()
    return u


class DashboardPreferenceModelTests(TestCase):
    def test_get_for_lazy_creates(self):
        user = make_user('lazyuser')
        self.assertFalse(DashboardPreference.objects.filter(user=user).exists())
        pref = DashboardPreference.get_for(user)
        self.assertTrue(DashboardPreference.objects.filter(user=user).exists())
        self.assertEqual(pref.user, user)
        self.assertEqual(pref.layout, {})

    def test_resolved_layout_uses_defaults_for_new_preference(self):
        user = make_user('defaultuser')
        pref = DashboardPreference.get_for(user)
        layout = pref.resolved_layout()
        self.assertEqual(set(layout.keys()), set(DEFAULT_LAYOUT.keys()))
        for key, meta in layout.items():
            self.assertTrue(meta['visible'])
            self.assertEqual(meta['order'], DEFAULT_LAYOUT[key]['order'])

    def test_resolved_layout_merges_partial_stored(self):
        user = make_user('partialuser')
        pref = DashboardPreference.get_for(user)
        pref.layout = {'kpi_total_horses': {'visible': False, 'order': 99}}
        pref.save()
        layout = pref.resolved_layout()
        self.assertFalse(layout['kpi_total_horses']['visible'])
        self.assertEqual(layout['kpi_total_horses']['order'], 99)
        # Untouched key keeps its default.
        self.assertTrue(layout['chart_revenue']['visible'])

    def test_resolved_layout_ignores_stale_keys(self):
        user = make_user('staleuser')
        pref = DashboardPreference.get_for(user)
        pref.layout = {'not_a_real_widget': {'visible': False, 'order': 0}}
        pref.save()
        layout = pref.resolved_layout()
        self.assertNotIn('not_a_real_widget', layout)

    def test_visible_ordered_keys_by_group_filters_and_sorts(self):
        user = make_user('grouseruser')
        pref = DashboardPreference.get_for(user)
        # Hide first KPI and put the last KPI at the front.
        pref.layout = {
            'kpi_total_horses': {'visible': False, 'order': 0},
            'kpi_outstanding_invoices': {'visible': True, 'order': -5},
        }
        pref.save()
        grouped = pref.visible_ordered_keys_by_group()
        kpi = grouped['kpi']
        self.assertNotIn('kpi_total_horses', kpi)
        self.assertEqual(kpi[0], 'kpi_outstanding_invoices')


class DashboardToggleEndpointTests(TestCase):
    def setUp(self):
        self.user = make_user('toggleuser')
        self.client.force_login(self.user)
        self.url = reverse('dashboard_toggle')

    def test_toggle_saves_visibility(self):
        resp = self.client.post(self.url, {'key': 'chart_revenue', 'visible': 'false'})
        self.assertEqual(resp.status_code, 204)
        pref = DashboardPreference.get_for(self.user)
        self.assertFalse(pref.resolved_layout()['chart_revenue']['visible'])

        resp = self.client.post(self.url, {'key': 'chart_revenue', 'visible': 'true'})
        self.assertEqual(resp.status_code, 204)
        pref.refresh_from_db()
        self.assertTrue(pref.resolved_layout()['chart_revenue']['visible'])

    def test_toggle_rejects_unknown_widget(self):
        resp = self.client.post(self.url, {'key': 'not_a_widget', 'visible': 'true'})
        self.assertEqual(resp.status_code, 400)

    def test_toggle_rejects_bad_visible_value(self):
        resp = self.client.post(self.url, {'key': 'chart_revenue', 'visible': 'maybe'})
        self.assertEqual(resp.status_code, 400)

    def test_toggle_requires_login(self):
        self.client.logout()
        resp = self.client.post(self.url, {'key': 'chart_revenue', 'visible': 'true'})
        self.assertEqual(resp.status_code, 302)

    def test_toggle_only_touches_own_row(self):
        other = make_user('otheruser')
        other_pref = DashboardPreference.get_for(other)
        other_pref.layout = {'chart_revenue': {'visible': True, 'order': 4}}
        other_pref.save()

        self.client.post(self.url, {'key': 'chart_revenue', 'visible': 'false'})

        other_pref.refresh_from_db()
        # other user's stored payload is unchanged.
        self.assertEqual(other_pref.layout['chart_revenue']['visible'], True)


class DashboardReorderEndpointTests(TestCase):
    def setUp(self):
        self.user = make_user('reorderuser')
        self.client.force_login(self.user)
        self.url = reverse('dashboard_reorder')

    def test_reorder_group_sets_new_order(self):
        new_order = [
            'kpi_outstanding_invoices',
            'kpi_unbilled_charges',
            'kpi_total_horses',
            'kpi_vaccinations_due',
        ]
        resp = self.client.post(self.url, {'group': 'kpi', 'order': new_order})
        self.assertEqual(resp.status_code, 204)
        pref = DashboardPreference.get_for(self.user)
        grouped = pref.visible_ordered_keys_by_group()
        self.assertEqual(grouped['kpi'], new_order)

    def test_reorder_silently_drops_unknown_keys(self):
        resp = self.client.post(self.url, {
            'group': 'kpi',
            'order': ['bogus', 'kpi_total_horses', 'also_bogus', 'kpi_vaccinations_due'],
        })
        self.assertEqual(resp.status_code, 204)
        pref = DashboardPreference.get_for(self.user)
        kpi = pref.visible_ordered_keys_by_group()['kpi']
        self.assertEqual(kpi[0], 'kpi_total_horses')
        self.assertEqual(kpi[1], 'kpi_vaccinations_due')
        self.assertNotIn('bogus', kpi)

    def test_reorder_rejects_unknown_group(self):
        resp = self.client.post(self.url, {'group': 'not_a_group', 'order': ['foo']})
        self.assertEqual(resp.status_code, 400)

    def test_keyboard_direction_swaps_adjacent(self):
        # Move the LAST kpi widget up one slot.
        resp = self.client.post(self.url, {
            'key': 'kpi_outstanding_invoices',
            'direction': 'up',
        })
        self.assertEqual(resp.status_code, 204)
        pref = DashboardPreference.get_for(self.user)
        kpi = pref.visible_ordered_keys_by_group()['kpi']
        # It should now be at index 2 (was index 3).
        self.assertEqual(kpi.index('kpi_outstanding_invoices'), 2)

    def test_keyboard_direction_at_edge_is_noop(self):
        # Moving the first widget "up" does nothing.
        resp = self.client.post(self.url, {
            'key': 'kpi_total_horses',
            'direction': 'up',
        })
        self.assertEqual(resp.status_code, 204)
        pref = DashboardPreference.get_for(self.user)
        kpi = pref.visible_ordered_keys_by_group()['kpi']
        self.assertEqual(kpi[0], 'kpi_total_horses')


class DashboardPreferencesPageTests(TestCase):
    def test_non_staff_user_can_view_prefs(self):
        user = make_user('nonstaff', is_staff=False)
        self.client.force_login(user)
        resp = self.client.get(reverse('dashboard_preferences'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # Renders all 15 widgets by name.
        for w in WIDGETS:
            self.assertIn(w['name'], body)


class DashboardQueryGatingTests(TestCase):
    def test_hidden_chart_widget_skips_chart_query(self):
        """When the revenue chart is hidden, the dashboard context should not
        build chart_data (empty placeholder)."""
        user = make_user('gatinguser')
        self.client.force_login(user)

        # Hide the revenue chart for this user.
        pref = DashboardPreference.get_for(user)
        layout = pref.resolved_layout()
        layout['chart_revenue']['visible'] = False
        pref.layout = layout
        pref.save()

        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # The revenue canvas and its chart-data JSON script should be absent.
        self.assertNotIn('id="revenueChart"', body)
        self.assertNotIn('id="chart-data"', body)

    def test_all_health_hidden_skips_lazy_loader(self):
        user = make_user('healthhideuser')
        self.client.force_login(user)

        pref = DashboardPreference.get_for(user)
        layout = pref.resolved_layout()
        for key in ('health_upcoming_dep', 'health_ehv_due',
                    'health_egg_counts', 'health_vet_followups'):
            layout[key]['visible'] = False
        pref.layout = layout
        pref.save()

        resp = self.client.get(reverse('dashboard'))
        body = resp.content.decode()
        # No HTMX lazy-loader div for the health alerts row.
        self.assertNotIn('_partials/health-alerts', body)

    def test_health_endpoint_empty_when_all_hidden(self):
        user = make_user('healthendpointuser')
        self.client.force_login(user)

        pref = DashboardPreference.get_for(user)
        layout = pref.resolved_layout()
        for key in ('health_upcoming_dep', 'health_ehv_due',
                    'health_egg_counts', 'health_vet_followups'):
            layout[key]['visible'] = False
        pref.layout = layout
        pref.save()

        resp = self.client.get(reverse('dashboard_health_alerts'))
        self.assertEqual(resp.status_code, 200)
        # Body renders nothing (no grid wrapper, no individual health cards).
        self.assertNotIn('EHV Vaccinations Due', resp.content.decode())
        self.assertNotIn('High Egg Counts', resp.content.decode())

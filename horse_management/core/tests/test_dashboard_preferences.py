"""Tests for per-user configurable dashboard (DashboardPreference).

Covers:
- Lazy-create on first access.
- ``resolved_layout`` merges stored payloads with DEFAULT_LAYOUT and ignores
  unknown keys.
- Toggle endpoint: saves visibility; rejects unknown keys; requires CSRF;
  only touches the caller's row.
- Dashboard view skips queries for hidden widgets.
- ``dashboard_health_alerts`` returns an empty body when no health widget is
  visible.
- Settings page permissions: non-staff can view their dashboard toggles;
  staff still see the business-config sections.
- Regression: multi-line Django comments don't leak into rendered HTML.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard_widgets import DEFAULT_LAYOUT, WIDGETS
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


class DashboardToggleCSRFTests(TestCase):
    """Regression: the toggle UI uses ``htmx.ajax`` (not an hx-post form),
    so the CSRF token must travel in a header. CSRF_COOKIE_HTTPONLY is True,
    so cookie-based auto-injection does not work."""

    def test_toggle_rejects_post_without_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        user = make_user('csrfuser')
        client.force_login(user)
        resp = client.post(
            reverse('dashboard_toggle'),
            {'key': 'chart_revenue', 'visible': 'false'},
        )
        self.assertEqual(resp.status_code, 403)

    def test_toggle_accepts_post_with_csrf_header(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        user = make_user('csrfheaderuser')
        client.force_login(user)
        get_resp = client.get(reverse('app_settings'))
        token = get_resp.cookies['csrftoken'].value
        resp = client.post(
            reverse('dashboard_toggle'),
            {'key': 'chart_revenue', 'visible': 'false'},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resp.status_code, 204)


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
        self.assertEqual(other_pref.layout['chart_revenue']['visible'], True)


class SettingsPagePermissionsTests(TestCase):
    """Everyone can configure their own dashboard from /settings/; non-staff
    don't see the business-config cards."""

    def test_non_staff_sees_dashboard_widgets_but_not_business_sections(self):
        user = make_user('nonstaff', is_staff=False)
        self.client.force_login(user)
        resp = self.client.get(reverse('app_settings'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # Dashboard section header is visible.
        self.assertIn('>Dashboard<', body)
        # All 15 widget names render on the page.
        for w in WIDGETS:
            self.assertIn(w['name'], body)
        # Business-only cards are hidden.
        self.assertNotIn('Business Details', body)
        self.assertNotIn('Rate Types', body)
        self.assertNotIn('Integrations', body)

    def test_staff_sees_both_business_and_dashboard_sections(self):
        user = make_user('staffuser', is_staff=True)
        self.client.force_login(user)
        resp = self.client.get(reverse('app_settings'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Business Details', body)
        self.assertIn('>Dashboard<', body)

    def test_standalone_prefs_page_is_gone(self):
        """The old /settings/dashboard/ URL was removed; no named route exists
        and the path 404s."""
        user = make_user('urlcheck', is_staff=False)
        self.client.force_login(user)
        resp = self.client.get('/settings/dashboard/')
        self.assertEqual(resp.status_code, 404)


class TemplateRegressionTests(TestCase):
    """Django-comment blocks that span multiple lines render as literal text.
    Make sure no `{#` / `#}` sequence leaks into any response body we serve."""

    def _assert_no_comment_leak(self, body):
        self.assertNotIn('{#', body)
        self.assertNotIn('#}', body)

    def test_dashboard_has_no_raw_comment_markers(self):
        user = make_user('commentcheck')
        self.client.force_login(user)
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self._assert_no_comment_leak(resp.content.decode())

    def test_settings_page_has_no_raw_comment_markers(self):
        user = make_user('commentcheck2')
        self.client.force_login(user)
        resp = self.client.get(reverse('app_settings'))
        self.assertEqual(resp.status_code, 200)
        self._assert_no_comment_leak(resp.content.decode())

    def test_health_alerts_partial_has_no_raw_comment_markers(self):
        user = make_user('commentcheck3')
        self.client.force_login(user)
        resp = self.client.get(reverse('dashboard_health_alerts'))
        self.assertEqual(resp.status_code, 200)
        self._assert_no_comment_leak(resp.content.decode())


class DashboardQueryGatingTests(TestCase):
    def test_hidden_chart_widget_skips_chart_query(self):
        user = make_user('gatinguser')
        self.client.force_login(user)

        pref = DashboardPreference.get_for(user)
        layout = pref.resolved_layout()
        layout['chart_revenue']['visible'] = False
        pref.layout = layout
        pref.save()

        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
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
        self.assertNotIn('EHV Vaccinations Due', resp.content.decode())
        self.assertNotIn('High Egg Counts', resp.content.decode())

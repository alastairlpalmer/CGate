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

import re

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
        from core.dashboard_widgets import DEFAULT_HIDDEN

        user = make_user('defaultuser')
        pref = DashboardPreference.get_for(user)
        layout = pref.resolved_layout()
        self.assertEqual(set(layout.keys()), set(DEFAULT_LAYOUT.keys()))
        for key, meta in layout.items():
            self.assertEqual(meta['visible'], key not in DEFAULT_HIDDEN)
            self.assertEqual(meta['order'], DEFAULT_LAYOUT[key]['order'])

    def test_pending_departures_hidden_by_default(self):
        user = make_user('pendinghidden')
        pref = DashboardPreference.get_for(user)
        self.assertFalse(pref.resolved_layout()['pending_departures']['visible'])

    def test_resolved_layout_merges_partial_stored(self):
        user = make_user('partialuser')
        pref = DashboardPreference.get_for(user)
        pref.layout = {'kpi_total_horses': {'visible': False, 'order': 99}}
        pref.save()
        layout = pref.resolved_layout()
        self.assertFalse(layout['kpi_total_horses']['visible'])
        self.assertEqual(layout['kpi_total_horses']['order'], 99)
        # Untouched key keeps its default.
        self.assertTrue(layout['recent_activity']['visible'])

    def test_explicit_pending_departures_pref_survives_default_change(self):
        """Users who explicitly enabled Pending Departures keep it even though
        the registry default flipped to hidden."""
        user = make_user('pendingoptin')
        pref = DashboardPreference.get_for(user)
        pref.layout = {'pending_departures': {'visible': True, 'order': 4}}
        pref.save()
        self.assertTrue(pref.resolved_layout()['pending_departures']['visible'])

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
            {'key': 'recent_activity', 'visible': 'false'},
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
            {'key': 'recent_activity', 'visible': 'false'},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resp.status_code, 204)


class DashboardToggleEndpointTests(TestCase):
    def setUp(self):
        self.user = make_user('toggleuser')
        self.client.force_login(self.user)
        self.url = reverse('dashboard_toggle')

    def test_toggle_saves_visibility(self):
        resp = self.client.post(self.url, {'key': 'recent_activity', 'visible': 'false'})
        self.assertEqual(resp.status_code, 204)
        pref = DashboardPreference.get_for(self.user)
        self.assertFalse(pref.resolved_layout()['recent_activity']['visible'])

        resp = self.client.post(self.url, {'key': 'recent_activity', 'visible': 'true'})
        self.assertEqual(resp.status_code, 204)
        pref.refresh_from_db()
        self.assertTrue(pref.resolved_layout()['recent_activity']['visible'])

    def test_toggle_rejects_unknown_widget(self):
        resp = self.client.post(self.url, {'key': 'not_a_widget', 'visible': 'true'})
        self.assertEqual(resp.status_code, 400)

    def test_toggle_rejects_bad_visible_value(self):
        resp = self.client.post(self.url, {'key': 'recent_activity', 'visible': 'maybe'})
        self.assertEqual(resp.status_code, 400)

    def test_toggle_requires_login(self):
        self.client.logout()
        resp = self.client.post(self.url, {'key': 'recent_activity', 'visible': 'true'})
        self.assertEqual(resp.status_code, 302)

    def test_toggle_only_touches_own_row(self):
        other = make_user('otheruser')
        other_pref = DashboardPreference.get_for(other)
        other_pref.layout = {'recent_activity': {'visible': True, 'order': 4}}
        other_pref.save()

        self.client.post(self.url, {'key': 'recent_activity', 'visible': 'false'})

        other_pref.refresh_from_db()
        self.assertEqual(other_pref.layout['recent_activity']['visible'], True)


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
    def test_hidden_list_widget_not_rendered(self):
        user = make_user('gatinguser')
        self.client.force_login(user)

        pref = DashboardPreference.get_for(user)
        layout = pref.resolved_layout()
        layout['recent_activity']['visible'] = False
        pref.layout = layout
        pref.save()

        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('Recent Activity', resp.content.decode())

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


class FinancesChartRenderingTests(TestCase):
    """Regression: the revenue-chart cost UNION crashed on SQLite because the
    billing models' Meta.ordering leaked an ORDER BY into the compound
    subqueries. The view's catch-all then served an empty fallback, so no
    charts rendered at all. The query (and the regression) now lives in the
    Finances view."""

    def test_finances_renders_chart_canvases_and_data(self):
        user = make_user('chartuser')
        self.client.force_login(user)

        resp = self.client.get(reverse('finances'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('id="revenueChart"', body)
        self.assertIn('id="capacityChart"', body)
        self.assertIn('id="chart-data"', body)
        self.assertIn('id="capacity-data"', body)

    def test_finances_requires_login(self):
        resp = self.client.get(reverse('finances'))
        self.assertEqual(resp.status_code, 302)

    def test_finances_renders_for_non_staff(self):
        user = make_user('viewerfin', is_staff=False)
        self.client.force_login(user)
        resp = self.client.get(reverse('finances'))
        self.assertEqual(resp.status_code, 200)

    def test_finances_nav_link_on_dashboard(self):
        user = make_user('navuser')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('href="/finances/"', body)


class DashboardRedesignTests(TestCase):
    """The dashboard is operational-only: no charts, greeting header,
    quick-find, hide-when-empty widgets, all-caught-up banner."""

    def test_dashboard_has_no_charts(self):
        user = make_user('nochartuser')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('id="revenueChart"', body)
        self.assertNotIn('id="capacityChart"', body)
        self.assertNotIn('id="chart-data"', body)

    def test_dashboard_header_and_quick_find(self):
        user = make_user('headeruser')
        user.first_name = 'Sam'
        user.save()
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Sam', body)
        self.assertRegex(body, r'Good (morning|afternoon|evening)')
        self.assertIn('id="quick-find-results"', body)
        self.assertNotIn('Your dashboard is empty', body)

    def test_all_caught_up_banner_when_lists_empty(self):
        user = make_user('caughtupuser')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('All caught up', body)
        # Empty widgets emit nothing at all.
        self.assertNotIn('Vaccinations Due</h2>', body)
        self.assertNotIn('Farrier Due', body)

    def test_all_hidden_shows_customize_card_not_banner(self):
        user = make_user('allhiddenuser')
        self.client.force_login(user)
        pref = DashboardPreference.get_for(user)
        layout = pref.resolved_layout()
        for key in layout:
            layout[key]['visible'] = False
        pref.layout = layout
        pref.save()

        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Your dashboard is empty', body)
        self.assertNotIn('All caught up', body)

    def test_stale_chart_pref_keys_still_render(self):
        """Stored layouts predating the chart removal contain chart_* keys;
        the dashboard must ignore them."""
        user = make_user('stalechartuser')
        pref = DashboardPreference.get_for(user)
        pref.layout = {'chart_revenue': {'visible': True, 'order': 4}}
        pref.save()
        self.client.force_login(user)
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('id="revenueChart"', resp.content.decode())

    def test_toggle_rejects_removed_chart_key(self):
        user = make_user('removedkeyuser')
        self.client.force_login(user)
        resp = self.client.post(
            reverse('dashboard_toggle'),
            {'key': 'chart_revenue', 'visible': 'true'},
        )
        self.assertEqual(resp.status_code, 400)

    def test_overdue_items_stay_on_dashboard(self):
        """Regression: the due lists filtered next_due_date >= today, so an
        item silently vanished from the dashboard the day it became overdue.
        Overdue is the most urgent state — it must render, with the header
        attention summary counting it (matching the Health page semantics)."""
        from datetime import timedelta

        from django.utils import timezone

        from core.models import Horse
        from health.models import Vaccination, VaccinationType

        today = timezone.now().date()
        horse = Horse.objects.create(name='Latebloomer')
        vt = VaccinationType.objects.create(name='Flu')
        Vaccination.objects.create(
            horse=horse, vaccination_type=vt,
            date_given=today - timedelta(days=300),
            next_due_date=today - timedelta(days=3),
        )

        user = make_user('overdueuser')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Latebloomer', body)
        self.assertIn('days overdue', body)
        self.assertIn('1 item needs attention', body)


class QuickFindTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from datetime import date

        from core.models import Horse, Location, Owner, Placement, RateType

        cls.owner = Owner.objects.create(
            name='Sarah Mitchell', email='sarah@example.com', phone='07700111222'
        )
        cls.location = Location.objects.create(name='Rough Grounds', site='California Farm')
        cls.rate = RateType.objects.create(name='Full livery', daily_rate=30)
        cls.alihunter = Horse.objects.create(name='ALIHUNTER')
        cls.departed = Horse.objects.create(name='ALIGONE', is_active=False)
        Placement.objects.create(
            horse=cls.alihunter, owner=cls.owner, location=cls.location,
            rate_type=cls.rate, start_date=date(2026, 1, 1),
        )

    def setUp(self):
        self.user = make_user('quickfinder')
        self.client.force_login(self.user)

    def _find(self, q):
        resp = self.client.get(reverse('quick_find'), {'q': q})
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse('quick_find'), {'q': 'ali'})
        self.assertEqual(resp.status_code, 302)

    def test_short_query_returns_empty(self):
        resp = self.client.get(reverse('quick_find'), {'q': 'a'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'')

    def test_exact_match_finds_horse(self):
        body = self._find('ALIHUNTER')
        self.assertIn('ALIHUNTER', body)
        self.assertIn(f'/horses/{self.alihunter.pk}/', body)

    def test_typo_finds_horse(self):
        self.assertIn('ALIHUNTER', self._find('alihnter'))

    def test_finds_owner_and_location(self):
        body = self._find('mitchel')
        self.assertIn('Sarah Mitchell', body)
        self.assertIn(f'/owners/{self.owner.pk}/', body)

        body = self._find('rough gronds')
        self.assertIn('Rough Grounds', body)
        self.assertIn(f'/locations/{self.location.pk}/', body)

    def test_inactive_horses_excluded(self):
        self.assertNotIn('ALIGONE', self._find('aligone'))

    def test_no_match_message(self):
        self.assertIn('No matches', self._find('zzzqqq'))

    def test_dashboard_input_disinherits_hx_select(self):
        """The body's hx-boost defaults include hx-select="#main-content", which
        htmx inherits. The quick-find partial contains no #main-content, so
        without hx-select="unset" on the input every response swaps in empty
        content and the dropdown never appears (endpoint tests can't catch this).
        """
        body = self.client.get(reverse('dashboard')).content.decode()
        input_tag = re.search(r'<input[^>]*name="q"[^>]*>', body)
        self.assertIsNotNone(input_tag, 'quick-find input not found on dashboard')
        self.assertIn('hx-select="unset"', input_tag.group(0))

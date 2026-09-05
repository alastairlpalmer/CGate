"""Tests for the home dashboard page and per-user zone preferences.

Covers:
- ``DashboardPreference``: lazy-create, merging stored layouts over the
  registry defaults, ignoring keys from older dashboards.
- Toggle endpoint: saves visibility; rejects unknown keys; requires CSRF;
  only touches the caller's row.
- The page: the title is the yard's state, the inbox holds overdue and
  today's items, due-soon items go to the 14-day strip, shared visits
  group into one row, the site switch narrows and is remembered.
- Hidden zones render nothing and their partial endpoints return empty.
- Settings page permissions: every user can switch their own zones; only
  roles with Business-settings access see the business cards.
- Regression: multi-line Django comments don't leak into rendered HTML.
- The health bulk form's pop-up mode, which the inbox's "Record for N"
  action opens with the horses preselected.
"""

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard_widgets import DEFAULT_HIDDEN, DEFAULT_LAYOUT, WIDGETS, widget_available
from core.models import DashboardPreference, Horse, Location, Owner, Placement, RateType
from core.permissions import access_map
from core.roles_testutils import make_admin, make_user_with_access
from health.models import FarrierVisit, Vaccination, VaccinationType

User = get_user_model()


def make_user(username='testuser'):
    """A non-admin user whose role can see the dashboard and most zones'
    feature areas, but none of the admin areas (settings, users) and not
    breeding or feed."""
    return make_user_with_access(
        username,
        dashboard='full',
        horses='view',
        owners='view',
        locations='view',
        health='full',
        finances='full',
        invoices='view',
        charges='view',
    )


class DashboardPreferenceModelTests(TestCase):
    def test_get_for_lazy_creates(self):
        user = make_user('lazyuser')
        self.assertFalse(DashboardPreference.objects.filter(user=user).exists())
        pref = DashboardPreference.get_for(user)
        self.assertTrue(DashboardPreference.objects.filter(user=user).exists())
        self.assertEqual(pref.user, user)
        self.assertEqual(pref.layout, {})
        self.assertEqual(pref.site, '')

    def test_every_zone_is_on_by_default(self):
        self.assertEqual(DEFAULT_HIDDEN, set())
        user = make_user('defaultuser')
        layout = DashboardPreference.get_for(user).resolved_layout()
        self.assertEqual(set(layout), {w['key'] for w in WIDGETS})
        for key, meta in layout.items():
            self.assertTrue(meta['visible'], key)
            self.assertEqual(meta['order'], DEFAULT_LAYOUT[key]['order'])

    def test_resolved_layout_merges_partial_stored(self):
        user = make_user('partialuser')
        pref = DashboardPreference.get_for(user)
        pref.layout = {'yard_board': {'visible': False, 'order': 99}}
        pref.save()
        layout = pref.resolved_layout()
        self.assertFalse(layout['yard_board']['visible'])
        self.assertEqual(layout['yard_board']['order'], 99)
        # Untouched key keeps its default.
        self.assertTrue(layout['attention']['visible'])

    def test_layout_from_the_old_dashboard_is_ignored(self):
        """Stored layouts predating the redesign carry the old widget keys;
        they must neither render nor break the page."""
        user = make_user('oldlayout')
        pref = DashboardPreference.get_for(user)
        pref.layout = {
            'kpi_total_horses': {'visible': False, 'order': 0},
            'recent_activity': {'visible': True, 'order': 5},
            'chart_revenue': {'visible': True, 'order': 4},
        }
        pref.save()
        layout = pref.resolved_layout()
        self.assertNotIn('kpi_total_horses', layout)
        self.assertNotIn('recent_activity', layout)
        self.assertTrue(layout['attention']['visible'])
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_visible_keys_drops_zones_for_hidden_features(self):
        user = make_user('gated')  # breeding and feed hidden, locations view
        keys = DashboardPreference.get_for(user).visible_keys()
        self.assertIn('attention', keys)
        self.assertIn('yard_board', keys)
        self.assertIn('money', keys)
        self.assertNotIn('in_foal', keys)
        self.assertEqual(access_map(user)['breeding'], 'hidden')


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
            {'key': 'activity', 'visible': 'false'},
        )
        self.assertEqual(resp.status_code, 403)

    def test_toggle_accepts_post_with_csrf_header(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        user = make_user('csrfheaderuser')
        client.force_login(user)
        page = client.get(reverse('app_settings'))
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        resp = client.post(
            reverse('dashboard_toggle'),
            {'key': 'activity', 'visible': 'false'},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resp.status_code, 204)


class DashboardToggleEndpointTests(TestCase):
    def setUp(self):
        self.user = make_user('toggleuser')
        self.client.force_login(self.user)
        self.url = reverse('dashboard_toggle')

    def test_toggle_saves_visibility(self):
        resp = self.client.post(self.url, {'key': 'activity', 'visible': 'false'})
        self.assertEqual(resp.status_code, 204)
        pref = DashboardPreference.get_for(self.user)
        self.assertFalse(pref.resolved_layout()['activity']['visible'])

        resp = self.client.post(self.url, {'key': 'activity', 'visible': 'true'})
        self.assertEqual(resp.status_code, 204)
        pref.refresh_from_db()
        self.assertTrue(pref.resolved_layout()['activity']['visible'])

    def test_toggle_rejects_unknown_widget(self):
        resp = self.client.post(self.url, {'key': 'not_a_widget', 'visible': 'true'})
        self.assertEqual(resp.status_code, 400)

    def test_toggle_rejects_removed_keys(self):
        for key in ('chart_revenue', 'kpi_total_horses', 'pending_departures'):
            with self.subTest(key=key):
                resp = self.client.post(self.url, {'key': key, 'visible': 'true'})
                self.assertEqual(resp.status_code, 400)

    def test_toggle_rejects_bad_visible_value(self):
        resp = self.client.post(self.url, {'key': 'activity', 'visible': 'maybe'})
        self.assertEqual(resp.status_code, 400)

    def test_toggle_requires_login(self):
        self.client.logout()
        resp = self.client.post(self.url, {'key': 'activity', 'visible': 'true'})
        self.assertEqual(resp.status_code, 302)

    def test_toggle_only_touches_own_row(self):
        other = make_user('otheruser')
        other_pref = DashboardPreference.get_for(other)
        other_pref.layout = {'activity': {'visible': True, 'order': 4}}
        other_pref.save()

        self.client.post(self.url, {'key': 'activity', 'visible': 'false'})

        other_pref.refresh_from_db()
        self.assertEqual(other_pref.layout['activity']['visible'], True)


class SettingsPagePermissionsTests(TestCase):
    """Everyone can configure their own dashboard from /settings/; non-staff
    don't see the business-config cards."""

    def test_non_staff_sees_dashboard_zones_but_not_business_sections(self):
        user = make_user('nonstaff')
        self.client.force_login(user)
        resp = self.client.get(reverse('app_settings'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('>Dashboard<', body)
        # Zones for areas the role can see are switchable; the rest are not
        # offered (they could never render).
        levels = access_map(user)
        for w in WIDGETS:
            if levels[w['feature']] == 'hidden' or not widget_available(w):
                self.assertNotIn(f'data-widget-key="{w["key"]}"', body)
            else:
                self.assertIn(w['name'], body)
                self.assertIn(f'data-widget-key="{w["key"]}"', body)
        # Business-only cards are hidden.
        self.assertNotIn('Business Details', body)
        self.assertNotIn('Rate Types', body)
        self.assertNotIn('Integrations', body)

    def test_staff_sees_both_business_and_dashboard_sections(self):
        user = make_admin('staffuser')
        self.client.force_login(user)
        resp = self.client.get(reverse('app_settings'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Business Details', body)
        self.assertIn('>Dashboard<', body)
        for w in WIDGETS:
            if widget_available(w):
                self.assertIn(w['name'], body)

    def test_standalone_prefs_page_is_gone(self):
        """The old /settings/dashboard/ URL was removed; no named route exists
        and the path 404s."""
        user = make_user('urlcheck')
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

    def test_partials_have_no_raw_comment_markers(self):
        user = make_user('commentcheck3')
        self.client.force_login(user)
        for name in ('dashboard_money', 'dashboard_activity'):
            with self.subTest(partial=name):
                resp = self.client.get(reverse(name))
                self.assertEqual(resp.status_code, 200)
                self._assert_no_comment_leak(resp.content.decode())


class DashboardZoneGatingTests(TestCase):
    def _hide(self, user, key):
        pref = DashboardPreference.get_for(user)
        layout = pref.resolved_layout()
        layout[key]['visible'] = False
        pref.layout = layout
        pref.save()

    def test_hidden_zone_not_rendered(self):
        user = make_user('gatinguser')
        self.client.force_login(user)
        self._hide(user, 'activity')
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('What changed', body)
        self.assertNotIn(reverse('dashboard_activity'), body)
        # Money still loads lazily.
        self.assertIn(reverse('dashboard_money'), body)

    def test_hidden_zone_endpoint_returns_empty(self):
        user = make_user('emptyendpoint')
        self.client.force_login(user)
        self._hide(user, 'money')
        resp = self.client.get(reverse('dashboard_money'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'')

    def test_partials_require_the_dashboard_feature(self):
        user = make_user_with_access('nodash', invoices='view')
        self.client.force_login(user)
        for name in ('dashboard_money', 'dashboard_activity'):
            with self.subTest(partial=name):
                resp = self.client.get(reverse(name))
                self.assertEqual(resp.status_code, 302)

    def test_all_hidden_shows_customize_card(self):
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
        self.assertNotIn('Needs action', body)


class FinancesPageTests(TestCase):
    """The revenue/capacity charts live on the Finances page, not the
    dashboard."""

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
        user = make_user('viewerfin')
        self.client.force_login(user)
        resp = self.client.get(reverse('finances'))
        self.assertEqual(resp.status_code, 200)

    def test_finances_nav_link_on_dashboard(self):
        user = make_user('navuser')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('href="/finances/"', body)


class DashboardPageTests(TestCase):
    """The page's first response: state headline, inbox, strip."""

    def setUp(self):
        self.today = timezone.localdate()
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.rate = RateType.objects.create(name='Grass', daily_rate=5)
        self.flu = VaccinationType.objects.create(name='Flu')

    def _horse(self, name, location):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.owner, location=location, rate_type=self.rate,
            start_date=self.today - timedelta(days=100),
        )
        return horse

    def test_dashboard_has_no_charts(self):
        user = make_user('nochartuser')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('id="revenueChart"', body)
        self.assertNotIn('id="capacityChart"', body)
        self.assertNotIn('id="chart-data"', body)

    def test_title_greets_and_the_subtext_is_the_yards_state(self):
        user = make_user('headeruser')
        user.first_name = 'Sam'
        user.save()
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        title = re.search(r'<h1 class="page-title">([^<]+)</h1>', body).group(1)
        self.assertRegex(title.strip(), r'^Good (morning|afternoon|evening), Sam$')
        subtitle = re.search(r'<p class="page-subtitle">(.*?)</p>', body, re.S).group(1)
        self.assertIn('All clear on the yard', subtitle)
        self.assertIn('Nothing needs doing', body)
        self.assertNotIn('Your dashboard is empty', body)

    def test_search_moved_to_the_app_bar(self):
        """The dashboard had the only search; it is on every page now."""
        user = make_user('searchuser')
        self.client.force_login(user)
        for name in ('dashboard', 'horse_list', 'location_list'):
            with self.subTest(page=name):
                body = self.client.get(reverse(name)).content.decode()
                self.assertIn('id="app-search-results"', body)
                self.assertIn('Find a horse, owner or location', body)
        # ...and not twice on the page it came from.
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertEqual(body.count('id="app-search-results"'), 1)

    def test_overdue_items_stay_on_dashboard_and_count_in_the_title(self):
        """Regression: the due lists once filtered next_due_date >= today, so
        an item vanished the day it became overdue. Overdue is the most
        urgent state — it must render, and the title must count it."""
        field = Location.objects.create(name='Top Paddock', site='Main')
        horse = self._horse('Latebloomer', field)
        Vaccination.objects.create(
            horse=horse, vaccination_type=self.flu,
            date_given=self.today - timedelta(days=300),
            next_due_date=self.today - timedelta(days=3),
        )
        user = make_user('overdueuser')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Latebloomer', body)
        self.assertIn('3 days overdue', body)
        self.assertIn('1 thing needs doing', body)
        # The action opens the record form in the pop-up sheet.
        self.assertIn(reverse('vaccination_create') + f'?horse={horse.pk}', body)
        self.assertIn('data-popup-title="Record vaccination for Latebloomer"', body)

    def test_due_soon_items_go_to_the_strip_not_the_inbox(self):
        field = Location.objects.create(name='Top Paddock', site='Main')
        horse = self._horse('Soon', field)
        Vaccination.objects.create(
            horse=horse, vaccination_type=self.flu,
            date_given=self.today - timedelta(days=300),
            next_due_date=self.today + timedelta(days=5),
        )
        user = make_user('soonuser')
        self.client.force_login(user)
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.context['rows'], [])
        self.assertEqual(resp.context['upcoming']['total'], 1)
        self.assertEqual(resp.context['upcoming']['days'][5]['count'], 1)
        body = resp.content.decode()
        self.assertIn('All clear on the yard', body)
        self.assertIn('Soon', body)  # in the visits list under the strip

    def test_shared_visit_groups_horses_into_one_row(self):
        field = Location.objects.create(name='Top Paddock', site='Main')
        a = self._horse('Huella', field)
        b = self._horse('True', field)
        for horse in (a, b):
            FarrierVisit.objects.create(
                horse=horse, date=self.today - timedelta(days=56),
                next_due_date=self.today - timedelta(days=14),
            )
        user = make_user('visituser')
        self.client.force_login(user)
        resp = self.client.get(reverse('dashboard'))
        rows = resp.context['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, 'visit')
        self.assertEqual({h.pk for h in rows[0].horses}, {a.pk, b.pk})
        body = resp.content.decode()
        self.assertIn('Record for 2', body)
        self.assertIn('1 thing needs doing', body)
        self.assertIn(f'action_type=farrier&amp;horse_ids={a.pk}&amp;horse_ids={b.pk}', body)

    def test_view_only_health_role_gets_no_action_buttons(self):
        field = Location.objects.create(name='Top Paddock', site='Main')
        horse = self._horse('Watcher', field)
        FarrierVisit.objects.create(
            horse=horse, date=self.today - timedelta(days=56),
            next_due_date=self.today - timedelta(days=1),
        )
        user = make_user_with_access('viewonly', dashboard='full', health='view', horses='view')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Watcher', body)
        self.assertNotIn('Record visit', body)

    def test_site_switch_is_remembered_and_narrows_the_inbox(self):
        somerford = Location.objects.create(name='Sandhills', site='Somerford')
        Location.objects.create(name='Bottom Barn', site='Colgate')
        horse = self._horse('Beech', somerford)
        Vaccination.objects.create(
            horse=horse, vaccination_type=self.flu,
            date_given=self.today - timedelta(days=300),
            next_due_date=self.today - timedelta(days=2),
        )
        user = make_user('siteuser')
        self.client.force_login(user)

        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.context['site'], '')
        self.assertEqual(len(resp.context['rows']), 1)
        body = resp.content.decode()
        self.assertIn('All sites', body)
        self.assertIn('?site=Colgate', body)

        resp = self.client.get(reverse('dashboard') + '?site=Colgate')
        self.assertEqual(resp.context['site'], 'Colgate')
        self.assertEqual(resp.context['rows'], [])
        self.assertIn('All clear at Colgate', resp.content.decode())
        self.assertEqual(DashboardPreference.get_for(user).site, 'Colgate')

        # Remembered on the next visit; an unknown site falls back to all.
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.context['site'], 'Colgate')
        resp = self.client.get(reverse('dashboard') + '?site=Nowhere')
        self.assertEqual(resp.context['site'], '')
        self.assertEqual(DashboardPreference.get_for(user).site, '')

    def test_single_site_yard_has_no_site_switch(self):
        Location.objects.create(name='Sandhills', site='Main')
        user = make_user('onesite')
        self.client.force_login(user)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('All sites', body)

    def test_yard_board_lists_sites_and_locations(self):
        field = Location.objects.create(name='Sandhills', site='Somerford', capacity=8)
        Location.objects.create(name='Bottom Barn', site='Colgate', usage='rested')
        self._horse('Beech', field)
        user = make_user('boarduser')
        self.client.force_login(user)
        resp = self.client.get(reverse('dashboard'))
        body = resp.content.decode()
        self.assertIn('Yard board', body)
        self.assertIn('Somerford', body)
        self.assertIn('Colgate', body)
        self.assertIn('1/8', body)  # the occupancy ring
        self.assertIn('Rested', body)
        self.assertEqual(resp.context['horse_count'], 1)


class BulkFormPopupTests(TestCase):
    """The inbox's "Record for N" opens the health bulk form in the pop-up
    sheet with the horses preselected."""

    def setUp(self):
        self.today = timezone.localdate()
        owner = Owner.objects.create(name='Jo Bloggs')
        field = Location.objects.create(name='Top Paddock', site='Main')
        rate = RateType.objects.create(name='Grass', daily_rate=5)
        self.a = Horse.objects.create(name='Huella')
        self.b = Horse.objects.create(name='True')
        for horse in (self.a, self.b):
            Placement.objects.create(
                horse=horse, owner=owner, location=field, rate_type=rate,
                start_date=self.today - timedelta(days=100),
            )
        self.user = make_user('bulkuser')
        self.client.force_login(self.user)

    def test_get_renders_preselected_horses(self):
        url = (reverse('bulk_health_form')
               + f'?action_type=farrier&horse_ids={self.a.pk}&horse_ids={self.b.pk}')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(f'name="horse_ids" value="{self.a.pk}"', body)
        self.assertIn(f'name="horse_ids" value="{self.b.pk}"', body)
        self.assertIn('Huella, True', body)
        self.assertIn('hx-target="#popup-body"', body)
        self.assertIn('Record for 2 horses', body)

    def test_post_records_for_every_horse_and_closes_the_sheet(self):
        resp = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'farrier',
            'horse_ids': [self.a.pk, self.b.pk],
            'date': self.today.isoformat(),
            'work_done': 'trim',
            'cost': '45.00',
        })
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp['HX-Trigger'], 'popup:saved')
        self.assertEqual(FarrierVisit.objects.filter(horse__in=[self.a, self.b], date=self.today).count(), 2)

    def test_list_page_bar_uses_the_same_sheet(self):
        # The Horses / Locations / Owners action bar opens the same form in
        # the same sheet, so its save closes the sheet the same way.
        resp = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'farrier',
            'horse_ids': [self.a.pk],
            'date': self.today.isoformat(),
            'work_done': 'trim',
            'cost': '45.00',
        })
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp['HX-Trigger'], 'popup:saved')

    def test_popup_mode_needs_health_full(self):
        viewer = make_user_with_access('bulkviewer', dashboard='full', health='view')
        self.client.force_login(viewer)
        url = reverse('bulk_health_form') + f'?action_type=farrier&horse_ids={self.a.pk}'
        self.assertEqual(self.client.get(url).status_code, 403)


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

    def test_inactive_horses_included_and_labelled(self):
        # Departed horses stay findable (their records matter after they
        # leave) but are labelled so it's obvious they're no longer on site.
        body = self._find('aligone')
        self.assertIn('ALIGONE', body)
        self.assertIn('Departed', body)

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

"""View-layer enforcement: hidden/view/full per feature, denial behaviour."""

from django.test import TestCase
from django.urls import reverse

from core.models import Horse, Location, Owner, RateType
from core.roles_testutils import make_user_with_access

# One representative read URL and write URL per three-state feature.
# URLs are resolved lazily in setUpTestData once fixture pks exist.
FEATURE_URLS = {
    "horses": {"read": "horse_list", "write": "horse_create"},
    "owners": {"read": "owner_list", "write": "owner_create"},
    "locations": {"read": "location_list", "write": "location_create"},
    "health": {"read": "health_dashboard", "write": "vaccination_create"},
    "breeding": {"read": "breeding_list", "write": "breeding_create"},
    "invoices": {"read": "invoice_list", "write": "invoice_create"},
    "costs": {"read": "costs_list", "write": "yard_cost_create"},
    "charges": {"read": "charge_list", "write": "charge_create"},
    "feed": {"read": "feed_dashboard", "write": "feed_stock_create"},
}


def _url(name):
    return reverse(name)


class EnforcementMatrixTests(TestCase):
    """hidden → redirected away; view → read 200 / write redirected;
    full → both 200. POST while insufficient → 403."""

    def test_hidden_read_redirects_to_dashboard_with_message(self):
        user = make_user_with_access(username="h1", dashboard="full")
        self.client.force_login(user)
        resp = self.client.get(_url("horse_list"))
        self.assertRedirects(resp, reverse("dashboard"))
        messages = list(resp.wsgi_request._messages)
        self.assertIn("Your role doesn't include access", str(messages[0]))

    def test_hidden_read_falls_back_to_settings_when_dashboard_hidden(self):
        user = make_user_with_access(username="h2", invoices="view")
        self.client.force_login(user)
        resp = self.client.get(_url("horse_list"))
        self.assertRedirects(resp, reverse("app_settings"))

    def test_insufficient_post_is_403(self):
        user = make_user_with_access(username="h3", horses="view")
        self.client.force_login(user)
        self.assertEqual(self.client.post(_url("horse_create")).status_code, 403)

    def test_insufficient_htmx_get_is_403(self):
        user = make_user_with_access(username="h4", dashboard="full")
        self.client.force_login(user)
        resp = self.client.get(_url("horse_list"), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_still_redirects_to_login(self):
        resp = self.client.get(_url("horse_list"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_matrix_read_write_per_feature(self):
        for feature, urls in FEATURE_URLS.items():
            with self.subTest(feature=feature, level="view"):
                user = make_user_with_access(username=f"v_{feature}", **{feature: "view"})
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get(_url(urls["read"])).status_code, 200,
                    f"{feature} view-level user should read {urls['read']}",
                )
                write = self.client.get(_url(urls["write"]))
                self.assertEqual(
                    write.status_code, 302,
                    f"{feature} view-level user should be turned away from {urls['write']}",
                )
            with self.subTest(feature=feature, level="full"):
                user = make_user_with_access(username=f"f_{feature}", **{feature: "full"})
                self.client.force_login(user)
                self.assertEqual(self.client.get(_url(urls["read"])).status_code, 200)
                self.assertEqual(self.client.get(_url(urls["write"])).status_code, 200)
            with self.subTest(feature=feature, level="hidden"):
                user = make_user_with_access(username=f"n_{feature}", dashboard="full")
                self.client.force_login(user)
                self.assertEqual(self.client.get(_url(urls["read"])).status_code, 302)

    def test_binary_features(self):
        # settings & users pages require full; finances & dashboard render read-only
        full_map = {
            "settings": _url("rate_type_create"),
            "users": _url("user_create"),
            "finances": _url("finances"),
            "dashboard": _url("dashboard"),
        }
        for feature, url in full_map.items():
            with self.subTest(feature=feature):
                user = make_user_with_access(username=f"b_{feature}", **{feature: "full"})
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).status_code, 200)
                stranger = make_user_with_access(username=f"s_{feature}", dashboard="full")
                self.client.force_login(stranger)
                if feature != "dashboard":
                    self.assertEqual(self.client.get(url).status_code, 302)


class CrossFeatureSeamTests(TestCase):
    """Places where one view spans two features."""

    def test_bulk_health_placement_actions_need_locations_full(self):
        horse = Horse.objects.create(name="Star")
        user = make_user_with_access(username="groom", health="full", dashboard="full")
        self.client.force_login(user)
        resp = self.client.post(reverse("bulk_health_apply"), {
            "action_type": "actual_departure",
            "horse_ids": [horse.pk],
            "date": "2026-07-01",
        })
        self.assertEqual(resp.status_code, 403)

    def test_bulk_health_records_allowed_with_health_full_only(self):
        horse = Horse.objects.create(name="Comet")
        user = make_user_with_access(username="groom2", health="full", dashboard="full")
        self.client.force_login(user)
        resp = self.client.post(reverse("bulk_health_apply"), {
            "action_type": "worming",
            "horse_ids": [horse.pk],
            "date": "2026-07-01",
            "product": "Equest",
        })
        self.assertNotEqual(resp.status_code, 403)

    def test_quick_find_skips_groups_the_role_cannot_view(self):
        Horse.objects.create(name="Meadow Star")
        Owner.objects.create(name="Meadow Family")
        Location.objects.create(name="Meadow Field", site="Main")
        user = make_user_with_access(username="finder", dashboard="full", horses="view")
        self.client.force_login(user)
        html = self.client.get(reverse("quick_find") + "?q=meadow").content.decode()
        self.assertIn("Meadow Star", html)
        self.assertNotIn("Meadow Family", html)
        self.assertNotIn("Meadow Field", html)

    def test_settings_business_post_requires_settings_feature(self):
        user = make_user_with_access(username="sneak", dashboard="full")
        self.client.force_login(user)
        resp = self.client.post(reverse("app_settings"), {
            "save_business": "1",
            "business_name": "Hijacked Yard",
        })
        # The POST branch is skipped entirely for users without settings access
        from core.models import BusinessSettings
        self.assertNotEqual(BusinessSettings.get_settings().business_name, "Hijacked Yard")
        self.assertEqual(resp.status_code, 200)  # page still renders (account section)


class NavigationVisibilityTests(TestCase):
    def test_nav_hides_areas_and_badge_shows_role_name(self):
        user = make_user_with_access(username="bk2", dashboard="full", invoices="full")
        role = user.role_assignment.role
        role.name = "Bookkeeper"
        role.save()
        self.client.force_login(user)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("Invoices", html)
        self.assertIn("Bookkeeper", html)
        self.assertNotIn(reverse("horse_list"), html.replace(reverse("dashboard"), ""))
        self.assertNotIn(">Costs<", html)

    def test_dashboard_widgets_filtered_by_feature(self):
        from core.models import DashboardPreference
        user = make_user_with_access(username="bk3", dashboard="full", invoices="view")
        grouped = DashboardPreference.get_for(user).visible_ordered_keys_by_group()
        flat = [k for keys in grouped.values() for k in keys]
        self.assertIn("table_outstanding", flat)          # invoices viewable
        self.assertNotIn("kpi_total_horses", flat)        # horses hidden
        self.assertNotIn("health_ehv_due", flat)          # health hidden

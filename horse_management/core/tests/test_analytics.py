"""Analytics wiring tests.

Analytics must be invisible when it is not configured, and must never leak
personal data when it is. No test in this file may reach the network: the
PostHog client is replaced in ``setUp``.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from core import analytics
from core.roles_testutils import make_admin


@override_settings(POSTHOG_API_KEY="")
class AnalyticsOffTests(TestCase):
    """With no API key the app must behave exactly as it did before."""

    def setUp(self):
        analytics._client = None
        self.user = make_admin(username="rider")

    def test_is_enabled_false(self):
        self.assertFalse(analytics.is_enabled())

    def test_get_client_returns_none(self):
        self.assertIsNone(analytics.get_client())

    def test_capture_is_a_no_op(self):
        # Must not raise, and must not build a client.
        analytics.capture(self.user, "user_signed_in")
        self.assertIsNone(analytics._client)

    def test_no_snippet_in_page(self):
        self.client.force_login(self.user)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "posthog-config")

    def test_sign_in_still_works(self):
        self.assertTrue(self.client.login(username="rider", password="pw"))


@override_settings(
    POSTHOG_API_KEY="phc_test",
    POSTHOG_SEND_USERNAMES=False,
)
class AnalyticsOnTests(TestCase):
    def setUp(self):
        analytics._client = None
        self.user = make_admin(username="rider")
        # Stand in for the real client so nothing is sent anywhere.
        self.posthog = MagicMock()
        patcher = patch.object(analytics, "get_client", return_value=self.posthog)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: setattr(analytics, "_client", None))

    def test_snippet_renders_for_signed_in_user(self):
        self.client.force_login(self.user)
        resp = self.client.get("/")
        self.assertContains(resp, "posthog-config")
        self.assertContains(resp, "phc_test")

    def test_no_snippet_for_anonymous_visitor(self):
        # The sign-in page must load no tracker and set no analytics cookie.
        resp = self.client.get("/accounts/login/")
        self.assertNotContains(resp, "posthog-config")

    def test_distinct_id_matches_server_and_browser(self):
        request = type("R", (), {"user": self.user})()
        context = analytics.template_context(request)
        self.assertEqual(
            context["posthog"]["distinct_id"],
            analytics.distinct_id_for(self.user),
        )

    def test_person_properties_omit_username_by_default(self):
        props = analytics.person_properties_for(self.user)
        self.assertNotIn("username", props)
        self.assertIn("role", props)

    @override_settings(POSTHOG_SEND_USERNAMES=True)
    def test_person_properties_include_username_when_opted_in(self):
        props = analytics.person_properties_for(self.user)
        self.assertEqual(props["username"], "rider")

    def test_sign_in_captures_an_event(self):
        self.client.login(username="rider", password="pw")
        self.posthog.capture.assert_called_once()
        args, kwargs = self.posthog.capture.call_args
        self.assertEqual(args[0], "user_signed_in")
        self.assertEqual(kwargs["distinct_id"], analytics.distinct_id_for(self.user))

    def test_sign_out_captures_an_event(self):
        self.client.force_login(self.user)
        self.posthog.capture.reset_mock()
        self.client.logout()
        self.posthog.capture.assert_called_once()
        args, _ = self.posthog.capture.call_args
        self.assertEqual(args[0], "user_signed_out")

    def test_failed_sign_in_sends_no_username(self):
        self.client.login(username="rider", password="wrong-password")
        self.posthog.capture.assert_called_once()
        args, kwargs = self.posthog.capture.call_args
        self.assertEqual(args[0], "user_sign_in_failed")
        self.assertEqual(kwargs["distinct_id"], "anonymous")
        self.assertNotIn("rider", str(kwargs["properties"]))
        # Anonymous events must not create a person profile.
        self.assertIs(kwargs["properties"]["$process_person_profile"], False)

    def test_capture_survives_a_broken_client(self):
        self.posthog.capture.side_effect = RuntimeError("boom")
        analytics.capture(self.user, "user_signed_in")  # must not raise

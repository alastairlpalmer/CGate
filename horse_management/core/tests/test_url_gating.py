"""Access-control regression tests (QA #11)."""

from django.test import TestCase


class PlacementsRedirectGatingTests(TestCase):
    """#11 — the /placements/ redirect must itself require login."""

    def test_placements_redirect_gated_when_logged_out(self):
        resp = self.client.get("/placements/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_placements_redirects_to_history_when_logged_in(self):
        from django.contrib.auth import get_user_model
        get_user_model().objects.create_user("u", password="pw")
        self.client.login(username="u", password="pw")
        resp = self.client.get("/placements/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/locations/", resp["Location"])

"""Regression tests: overlapping boosted navigations must not kill the nav.

Two boosted navigations in flight at once: the first response to land
outerHTML-swaps #main-content, detaching the element the second response
captured as its swap target. htmx then throws mid-swap
(htmx:swapError / null parentElement), and the throw skips its
request-lock release — every later click on that link queues behind a
request that never finishes, so sidebar navigation appears dead until a
full reload (reported as "clicking between Owners and Horses doesn't
work").

Two defence layers live in base.html:
1. hx-sync="closest body:replace" on the nav containers — all nav links
   share one sync scope, so a new nav click aborts the in-flight one
   instead of racing it (last click wins, lock released properly);
2. an htmx:beforeOnLoad guard that re-points a detached swap target at
   the live element with the same id before the swap runs, covering
   races hx-sync cannot see (e.g. an in-content boosted link racing a
   sidebar click).
"""

from django.test import TestCase
from django.urls import reverse

from core.roles_testutils import make_admin


class BoostRaceGuardTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        self.body = self.client.get(reverse('dashboard')).content.decode()

    def test_nav_containers_share_an_abortable_sync_scope(self):
        # Sidebar, mobile tab bar, and the More sheet — all three nav
        # surfaces must carry the sync attribute for their links to inherit.
        self.assertGreaterEqual(self.body.count('hx-sync="closest body:replace"'), 3)

    def test_stale_target_guard_is_installed(self):
        self.assertIn("addEventListener('htmx:beforeOnLoad'", self.body)
        self.assertIn('document.body.contains(target)', self.body)
        self.assertIn('document.getElementById(target.id)', self.body)

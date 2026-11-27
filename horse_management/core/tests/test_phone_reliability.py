"""The phone app, phase 1: nothing breaks, nothing is lost.

docs/MOBILE_APP_PLAN.md, items A1 to A7. The behaviour in a browser is
checked by tools/qa/phone_review.js; these tests hold the server's part
and the wiring in the templates.
"""

from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from core.middleware import AppVersionMiddleware, SignInRedirectMiddleware
from core.roles_testutils import make_user_with_access
from core.templatetags.cache_bust import app_version

JS = Path(settings.BASE_DIR) / 'static' / 'js'
SIGN_IN = settings.LOGIN_URL


class SignInEndedTests(TestCase):
    """A1: a tap after the sign-in has ended loads the sign-in page."""

    def test_a_page_load_is_redirected_as_before(self):
        response = self.client.get(reverse('horse_list'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f'{SIGN_IN}?next=/horses/')
        self.assertNotIn('HX-Redirect', response)

    def test_a_tap_on_a_link_loads_the_sign_in_page_in_full(self):
        response = self.client.get(
            '/horses/?location=3', HTTP_HX_REQUEST='true', HTTP_HX_BOOSTED='true',
            HTTP_HX_CURRENT_URL='http://testserver/',
        )
        # htmx only acts on HX-Redirect in an answer it accepts: not a 302,
        # which the browser would follow before htmx saw it.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['HX-Redirect'], f'{SIGN_IN}?next=%2Fhorses%2F%3Flocation%3D3')

    def test_a_part_of_a_page_goes_back_to_the_page_the_person_is_on(self):
        # A sheet is not a page: after sign-in, "next" must not show the
        # sheet's HTML alone.
        response = self.client.get(
            '/horses/14/quick-edit/', HTTP_HX_REQUEST='true',
            HTTP_HX_CURRENT_URL='http://testserver/horses/14/?from=q',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['HX-Redirect'], f'{SIGN_IN}?next=%2Fhorses%2F14%2F%3Ffrom%3Dq')

    def test_a_save_goes_back_to_the_page_the_person_is_on(self):
        response = self.client.post(
            '/horses/14/move/', {'location': '1'}, HTTP_HX_REQUEST='true', HTTP_HX_BOOSTED='true',
            HTTP_HX_CURRENT_URL='http://testserver/horses/14/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['HX-Redirect'], f'{SIGN_IN}?next=%2Fhorses%2F14%2F')

    def test_a_part_with_no_page_address_still_reaches_the_sign_in_page(self):
        response = self.client.get('/notifications/badge/', HTTP_HX_REQUEST='true')
        self.assertEqual(response['HX-Redirect'], SIGN_IN)

    def test_an_address_on_another_host_is_not_used(self):
        response = self.client.get(
            '/notifications/badge/', HTTP_HX_REQUEST='true',
            HTTP_HX_CURRENT_URL='https://example.org/steal/',
        )
        self.assertEqual(response['HX-Redirect'], SIGN_IN)

    def test_a_signed_in_tap_is_not_touched(self):
        self.client.force_login(make_user_with_access('yard', horses='view'))
        response = self.client.get(reverse('horse_list'), HTTP_HX_REQUEST='true', HTTP_HX_BOOSTED='true')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('HX-Redirect', response)


class SignInRedirectMiddlewareTests(SimpleTestCase):
    """The middleware alone: it only acts on a redirect to the sign-in page."""

    def run_with(self, answer, path='/horses/', **headers):
        request = RequestFactory().get(path, **headers)
        return SignInRedirectMiddleware(lambda request: answer)(request)

    def test_another_redirect_is_left_alone(self):
        # A save that answers with a redirect to the saved record.
        response = self.run_with(HttpResponseRedirect('/horses/14/'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('HX-Redirect', response)

    def test_an_answer_that_is_not_a_redirect_is_left_alone(self):
        response = self.run_with(HttpResponse('ok'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.content, b'ok')
        self.assertNotIn('HX-Redirect', response)

    def test_the_sign_in_page_itself_is_left_alone(self):
        response = self.run_with(HttpResponseRedirect(SIGN_IN), path=SIGN_IN, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 302)


class AppVersionTests(TestCase):
    """A5: an open page can see that the server runs a newer version."""

    def test_a_page_carries_its_version_and_the_header_agrees(self):
        self.client.force_login(make_user_with_access('yard', horses='view'))
        response = self.client.get(reverse('horse_list'))
        self.assertEqual(response[AppVersionMiddleware.HEADER], app_version())
        self.assertContains(response, f'<meta name="app-version" content="{app_version()}">', html=True)

    def test_the_version_is_the_one_in_the_static_addresses(self):
        # One value for both: a deploy that changes a script changes the
        # version a page compares.
        self.client.force_login(make_user_with_access('yard', horses='view'))
        response = self.client.get(reverse('horse_list'))
        self.assertContains(response, f'js/app.bundle.js?v={app_version()}')

    def test_an_answer_that_is_not_a_page_has_no_version(self):
        response = self.client.get(reverse('web_health_check'))
        self.assertNotIn(AppVersionMiddleware.HEADER, response)


class PageCopiesTests(TestCase):
    """A6: copies of pages do not stay on the phone after sign-out."""

    def test_the_sign_in_page_deletes_them(self):
        response = self.client.get(reverse('login'))
        self.assertContains(response, "localStorage.removeItem('htmx-history-cache')")

    def test_a_sign_out_from_the_app_deletes_them(self):
        script = (JS / 'connection.js').read_text()
        self.assertIn("removeItem('htmx-history-cache')", script)
        # The app's sign-out forms post to the address the script looks for.
        self.assertRegex(reverse('logout'), r'/logout/$')


class PhoneShellTests(TestCase):
    """The page loads the scripts and holds the marks they need."""

    def setUp(self):
        self.client.force_login(make_user_with_access('yard', horses='view'))
        self.html = self.client.get(reverse('horse_list')).content.decode()

    def test_the_back_helper_loads_before_the_scripts_that_call_it(self):
        from core.tests._page_scripts import bundle_list, page_loads
        names = ('overlay_back', 'popup', 'notifications', 'lightbox', 'split_view', 'locations_mobile', 'alpine.min')
        for name in names:
            self.assertTrue(page_loads(self.html, f'{name}.js'), name)
        order = [bundle_list().index(f'js/{name}.js') for name in names]
        self.assertEqual(order, sorted(order))

    def test_the_no_signal_mark_is_in_the_page_and_hidden(self):
        self.assertRegex(self.html, r'<div id="offline-mark"[^>]*\bhidden\b')

    def test_the_more_sheet_tells_the_back_helper(self):
        self.assertIn("Yardway.overlay.sync('more', open", self.html)

    def test_each_sheet_and_drawer_uses_the_back_helper(self):
        for name, mark in (
            ('split_view.js', "overlay.sync('preview'"),
            ('notifications.js', "Yardway.overlay.opened('notif'"),
            ('lightbox.js', "Yardway.overlay.opened('lightbox'"),
            ('locations_mobile.js', "Yardway.overlay.opened('location'"),
        ):
            with self.subTest(script=name):
                self.assertIn(mark, (JS / name).read_text())

    def test_a_failed_save_in_the_sheet_keeps_the_form(self):
        # A3: the reason goes above the form; the form is not replaced.
        script = (JS / 'popup.js').read_text()
        self.assertIn('function showSaveError', script)
        self.assertIn("body.insertBefore(box, body.firstChild)", script)

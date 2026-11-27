"""The public marketing pages and the choice at ``/``."""

from django.test import TestCase, override_settings
from django.urls import reverse

from core.roles_testutils import make_user_with_access

PAGES = ('marketing:features', 'marketing:pricing', 'marketing:about', 'marketing:contact')


@override_settings(MARKETING_SITE_ENABLED=True)
class SiteOnTests(TestCase):

    def test_a_visitor_at_the_root_gets_the_home_page(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'marketing/home.html')
        self.assertIn('Cookie', response['Vary'])

    def test_a_signed_in_user_at_the_root_gets_the_dashboard(self):
        user = make_user_with_access('yard', dashboard='full')
        self.client.force_login(user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateNotUsed(response, 'marketing/home.html')
        self.assertTemplateUsed(response, 'dashboard.html')
        self.assertIn('Cookie', response['Vary'])

    def test_requests_from_inside_the_app_still_go_to_sign_in(self):
        """An ended session in the app, or the installed phone app, gets the sign-in page."""
        # htmx: SignInRedirectMiddleware turns the redirect into HX-Redirect.
        response = self.client.get('/', HTTP_HX_REQUEST='true')
        self.assertIn(reverse('login'), response['HX-Redirect'])
        response = self.client.get('/', HTTP_X_BACKGROUND='1')
        self.assertEqual(response.status_code, 302)
        response = self.client.get('/?source=app')
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        response = self.client.post('/')
        self.assertNotEqual(response.status_code, 200)

    def test_each_page_opens_for_a_visitor(self):
        for name in PAGES:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)

    def test_the_pages_carry_sign_in_at_the_top_and_no_app_scripts(self):
        for url in ['/'] + [reverse(name) for name in PAGES]:
            html = self.client.get(url).content.decode()
            header = html.split('<header', 1)[1].split('</header>', 1)[0]
            self.assertIn(f'href="{reverse("login")}"', header, url)
            self.assertIn('>Sign in</a>', header, url)
            self.assertNotIn('app.bundle.js', html, url)
            self.assertNotIn('htmx', html, url)
            self.assertNotIn('alpine', html.lower(), url)
            self.assertIn('js/marketing.js', html, url)
            self.assertEqual(html.count('<h1'), 1, url)

    def test_a_signed_in_user_on_a_marketing_page_is_offered_the_app(self):
        self.client.force_login(make_user_with_access('yard2', dashboard='full'))
        html = self.client.get(reverse('marketing:pricing')).content.decode()
        self.assertIn('Open Cgate', html)
        self.assertNotIn('>Sign in</a>', html.split('</header>', 1)[0])

    def test_the_sign_in_page_links_back_home(self):
        html = self.client.get(reverse('login')).content.decode()
        self.assertIn('Back to Cgate home', html)

    def test_the_pages_only_answer_get(self):
        self.assertEqual(self.client.post(reverse('marketing:pricing')).status_code, 405)


class SiteOffTests(TestCase):
    """Off (the default): everything is as it was before the site."""

    def test_a_visitor_at_the_root_goes_to_sign_in(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_the_pages_are_not_found(self):
        for name in PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 404, name)

    def test_the_sign_in_page_has_no_home_link(self):
        self.assertNotIn('Back to Cgate home', self.client.get(reverse('login')).content.decode())

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
            self.assertIn('property="og:title"', html, url)
            self.assertIn(f'rel="canonical" href="http://testserver{url}"', html, url)

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


GOOD_POST = {
    'topic': 'demo', 'name': 'Sam Rider', 'email': 'sam@example.com', 'phone': '',
    'yard_name': 'Hill Farm', 'horses': '22', 'message': 'Line one\nLine <two>', 'consent': 'on',
}


@override_settings(
    MARKETING_SITE_ENABLED=True, MARKETING_CONTACT_EMAIL='team@example.com',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', MARKETING_CONTACT_LIMIT_PER_HOUR=3,
)
class ContactFormTests(TestCase):

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_a_message_is_saved_and_emailed_to_the_team(self):
        from django.core import mail

        from .models import ContactMessage

        response = self.client.post(reverse('marketing:contact'), GOOD_POST)
        self.assertRedirects(response, reverse('marketing:contact') + '?sent=1')
        message = ContactMessage.objects.get()
        self.assertEqual((message.name, message.horses, message.yard_name), ('Sam Rider', 22, 'Hill Farm'))
        self.assertIsNotNone(message.emailed_at)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['team@example.com'])
        self.assertEqual(email.reply_to, ['sam@example.com'])
        html = email.alternatives[0][0]
        self.assertIn('Line one<br>Line &lt;two&gt;', html)
        self.assertIn('Thank you', self.client.get(response['Location']).content.decode())

    def test_without_a_team_address_the_message_is_still_saved(self):
        from django.core import mail

        from .models import ContactMessage

        with self.settings(MARKETING_CONTACT_EMAIL=''):
            self.client.post(reverse('marketing:contact'), GOOD_POST)
        self.assertEqual(ContactMessage.objects.count(), 1)
        self.assertIsNone(ContactMessage.objects.get().emailed_at)
        self.assertEqual(mail.outbox, [])

    def test_a_failed_email_keeps_the_message(self):
        from unittest import mock

        from .models import ContactMessage

        with mock.patch('notifications.mail._Message.send', side_effect=OSError('smtp down')), \
                self.assertLogs('marketing.views', 'ERROR'):
            response = self.client.post(reverse('marketing:contact'), GOOD_POST)
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(ContactMessage.objects.get().emailed_at)

    def test_consent_and_name_and_email_are_needed(self):
        from .models import ContactMessage

        response = self.client.post(reverse('marketing:contact'), {'topic': 'demo'})
        self.assertEqual(response.status_code, 400)
        html = response.content.decode()
        self.assertIn('Tick this box', html)
        self.assertIn('aria-invalid="true"', html)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_a_bot_that_fills_the_hidden_field_is_thanked_and_ignored(self):
        from django.core import mail

        from .models import ContactMessage

        response = self.client.post(reverse('marketing:contact'), {**GOOD_POST, 'website': 'http://spam'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContactMessage.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_too_many_messages_from_one_address_are_refused(self):
        from .models import ContactMessage

        for _ in range(3):
            self.client.post(reverse('marketing:contact'), GOOD_POST)
        response = self.client.post(reverse('marketing:contact'), GOOD_POST)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(ContactMessage.objects.count(), 3)

    def test_a_link_can_choose_the_topic(self):
        html = self.client.get(reverse('marketing:contact') + '?topic=pricing').content.decode()
        self.assertRegex(html, r'value="pricing"[^>]*checked')


@override_settings(MARKETING_SITE_ENABLED=True)
class PricingTests(TestCase):

    def test_the_three_plans_show_their_price_per_horse(self):
        html = self.client.get(reverse('marketing:pricing')).content.decode()
        for name, price, yard in (('Essentials', '5', '100'), ('Professional', '7.50', '150'), ('Complete', '10', '200')):
            self.assertIn(name, html)
            self.assertIn(f'£{price}</span>', html)
            self.assertIn(f'A 20-horse yard pays £{yard} a month', html)

    def test_every_feature_is_in_exactly_one_plan(self):
        from .content import FEATURES, PLANS

        in_plans = [key for plan in PLANS for key in plan['features']]
        self.assertCountEqual(in_plans, [feature['key'] for feature in FEATURES])

    def test_the_features_page_links_match_the_anchors(self):
        from .content import FEATURES

        html = self.client.get(reverse('marketing:features')).content.decode()
        for feature in FEATURES:
            self.assertIn(f'id="{feature["key"]}"', html)

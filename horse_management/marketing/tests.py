"""The public marketing pages and the choice at ``/``."""

from django.test import TestCase, override_settings
from django.urls import reverse

from core.roles_testutils import make_user_with_access

PAGES = ('marketing:pricing', 'marketing:contact')
OLD_PAGES = ('marketing:features', 'marketing:about')


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

    def test_the_old_features_and_about_pages_go_to_their_home_sections(self):
        self.assertRedirects(self.client.get(reverse('marketing:features')), '/#features',
                             status_code=301, fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse('marketing:about')), '/#about',
                             status_code=301, fetch_redirect_response=False)

    def test_home_holds_the_features_and_about_sections(self):
        from .content import ADDONS, FEATURES

        html = self.client.get('/').content.decode()
        for key in ['features', 'about'] + [item['key'] for item in FEATURES + ADDONS]:
            self.assertIn(f'id="{key}"', html)
        self.assertIn('Interactive map tracking of horses', html)
        # The hero's stalls open each example horse's record.
        for name in ('Bramble', 'Duke', 'Pip', 'Maple', 'Otis'):
            self.assertIn(f'aria-label="See {name}\'s record"', html)
        self.assertIn('id="horse-pip"', html)
        self.assertIn('Dun Harley', html)

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
            self.assertIn('css/marketing.css', html, url)
            self.assertNotIn('css/styles.css', html, url)
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
        for name in PAGES + OLD_PAGES:
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

    def test_core_and_pro_have_one_price_per_horse(self):
        from decimal import Decimal

        from .content import quote

        self.assertEqual(quote(1, 'core')['per_horse'], Decimal('5'))
        self.assertEqual(quote(1, 'pro')['per_horse'], Decimal('7'))     # Core + maps (+1) + invoicing (+1)

    def test_the_quote_adds_the_add_ons_per_horse(self):
        from decimal import Decimal

        from .content import quote

        q = quote(22, 'pro')
        self.assertEqual(q['monthly'], Decimal('154'))
        self.assertEqual(q['percent'], 25)
        self.assertEqual(q['offer_label'], '£115.50')
        q = quote(10, 'core', ['training', 'breeding'])                  # 5 + 1.50 + 0.50
        self.assertEqual(q['monthly'], Decimal('70'))
        self.assertEqual(q['partner'], ['Training', 'Breeding'])
        self.assertEqual(q['percent'], 50)
        self.assertEqual(q['offer_label'], '£35')

    def test_bad_input_is_made_safe(self):
        from .content import EXAMPLE_HORSES, quote, quote_from_spec

        self.assertEqual(quote('lots')['horses'], EXAMPLE_HORSES)
        self.assertEqual(quote(-4)['horses'], 1)
        self.assertEqual(quote(150)['horses'], 150)                      # no size limit
        self.assertEqual(quote(10**9)['horses'], 100_000)
        self.assertEqual(quote(5, 'gold')['plan']['key'], 'pro')
        self.assertEqual(quote_from_spec('horses=5&plan=core&addons=nope,training')['addon_keys'], ['training'])
        self.assertIsNone(quote_from_spec('plan=core'))

    def test_the_page_opens_on_pro_with_the_default_quote(self):
        html = self.client.get(reverse('marketing:pricing')).content.decode()
        self.assertIn('data-out="monthly">£140</span>', html)      # 20 x 7
        self.assertIn('a month + VAT', html)
        self.assertIn('Onboarding included', html)
        for worry in ('Can we try it first?', 'Do we have to type in all our data?', 'What support do we get?',
                      'How does billing work?', 'What if we want to leave?'):
            self.assertIn(worry, html)
        self.assertIn('Interactive map tracking of horses', html)
        self.assertIn('Farrier tracking', html)
        self.assertIn('Coming next', html)
        self.assertRegex(html, r'value="pro"[^>]*checked')
        self.assertNotRegex(html, r'value="training"[^>]*checked')
        self.assertNotIn('C-vet', html)

    def test_a_large_yard_gets_a_price_with_no_cut_off(self):
        html = self.client.get(reverse('marketing:pricing') + '?horses=150').content.decode()
        self.assertIn('data-out="monthly">£1,050</span>', html)    # 150 x 7
        self.assertNotIn('More than 60', html)

    def test_a_link_can_open_the_calculator_with_choices(self):
        html = self.client.get(reverse('marketing:pricing') + '?plan=core&horses=12&addon=training').content.decode()
        self.assertIn('data-out="monthly">£78</span>', html)       # 12 x 6.50
        self.assertRegex(html, r'value="core"[^>]*checked')
        self.assertRegex(html, r'value="training"[^>]*checked')
        self.assertIn('50% off', html)

    def test_claim_this_offer_carries_the_quote_to_the_contact_page(self):
        url = reverse('marketing:contact') + '?plan=core&horses=22&addon=breeding&topic=offer'
        html = self.client.get(url).content.decode()
        self.assertIn('Your quote', html)
        self.assertIn('£121', html)                                # 22 x 5.50
        self.assertIn('Core + Breeding', html)
        self.assertIn('50% off your first 6 months: £60.50 a month', html)
        self.assertRegex(html, r'name="horses" value="22"')
        self.assertRegex(html, r'value="offer"[^>]*checked')


@override_settings(
    MARKETING_SITE_ENABLED=True, MARKETING_CONTACT_EMAIL='team@example.com',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class QuoteMessageTests(TestCase):

    def test_the_quote_is_worked_out_again_and_saved_with_the_message(self):
        from django.core import mail
        from django.core.cache import cache

        from .models import ContactMessage

        cache.clear()
        post = {**GOOD_POST, 'topic': 'offer', 'quote_spec': 'horses=22&plan=pro&addons='}
        self.client.post(reverse('marketing:contact'), post)
        message = ContactMessage.objects.get()
        self.assertIn('22 horses', message.quote)
        self.assertIn('Pro · £154 a month + VAT', message.quote)
        self.assertIn('25% off for 6 months (£115.50 a month)', message.quote)
        self.assertIn('£154 a month + VAT', mail.outbox[0].alternatives[0][0])

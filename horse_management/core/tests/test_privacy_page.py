"""The public privacy policy page and its link on the sign-in page."""

from django.test import TestCase
from django.urls import reverse

from core.models import BusinessSettings


class PrivacyPageTests(TestCase):
    def test_anyone_can_open_it_without_signing_in(self):
        response = self.client.get(reverse('privacy_policy'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 class="text-3xl font-bold font-heading mb-2">Privacy policy</h1>', html=False)

    def test_it_is_at_slash_privacy(self):
        self.assertEqual(reverse('privacy_policy'), '/privacy/')

    def test_it_names_the_business_from_settings(self):
        business = BusinessSettings.get_settings()
        business.business_name = 'Oakfield Livery'
        business.email = 'office@oakfield.example'
        business.save()
        response = self.client.get(reverse('privacy_policy'))
        self.assertContains(response, 'Oakfield Livery')
        self.assertContains(response, 'mailto:office@oakfield.example')

    def test_it_has_the_google_limited_use_statement(self):
        response = self.client.get(reverse('privacy_policy'))
        self.assertContains(response, 'Limited Use requirements')

    def test_the_sign_in_page_links_to_it(self):
        response = self.client.get(reverse('login'))
        self.assertContains(response, f'href="{reverse("privacy_policy")}"')

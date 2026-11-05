"""The app is called Cgate everywhere a user can see its name.

The sidebar and the phone header show the product name, not the yard's
business name: that name is for invoices and emails to owners.
"""

from django.test import TestCase
from django.urls import reverse

from core.models import BusinessSettings
from core.roles_testutils import make_admin


class AppNameTests(TestCase):
    def setUp(self):
        BusinessSettings.objects.update_or_create(
            pk=1, defaults={'business_name': 'Colgate Farm Livery'},
        )
        self.client.force_login(make_admin())

    def test_the_shell_shows_cgate_not_the_business_name(self):
        body = self.client.get(reverse('horse_list')).content.decode()
        self.assertIn('Horses - Cgate</title>', body)
        self.assertIn('>Cgate</span>', body)
        self.assertNotIn('Colgate Farm Livery', body)
        self.assertNotIn('Yardway<', body)

    def test_the_sign_in_page_says_cgate(self):
        self.client.logout()
        body = self.client.get(reverse('login')).content.decode()
        self.assertIn('Sign in - Cgate', body)
        self.assertNotIn('>Yardway<', body)

"""Search on the Xero contact mapping admin list.

The search fields named owner__first_name and owner__last_name, which
core.Owner does not have (it has name). Any search raised FieldError and
the page returned a 500.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Owner
from xero_integration.models import XeroContactMapping


class XeroContactMappingAdminSearchTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(
            username='admin', email='admin@example.com', password='pw-for-tests-only',
        )
        self.client.force_login(user)
        self.url = f'/{settings.ADMIN_URL}xero_integration/xerocontactmapping/'

    def test_search_returns_200(self):
        response = self.client.get(self.url, {'q': 'x'})
        self.assertEqual(response.status_code, 200)

    def test_search_finds_mapping_by_owner_name(self):
        owner = Owner.objects.create(name='Harriet Bexley')
        other = Owner.objects.create(name='Tom Ashdown')
        match = XeroContactMapping.objects.create(
            owner=owner, xero_contact_id='c-1', xero_contact_name='HB Racing',
        )
        XeroContactMapping.objects.create(
            owner=other, xero_contact_id='c-2', xero_contact_name='Ashdown Ltd',
        )
        response = self.client.get(self.url, {'q': 'Bexley'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['cl'].result_list), [match])

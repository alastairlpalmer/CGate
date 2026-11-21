"""Dev board check, 21 Nov 2026: a year's land use read "for the 2026"."""

from django.test import TestCase
from django.urls import reverse

from core.models import Location
from core.roles_testutils import make_admin


class YearLabelTests(TestCase):

    def test_a_year_reads_as_a_year(self):
        self.client.force_login(make_admin())
        field = Location.objects.create(name='Rest Paddock', site='Main')
        url = reverse('location_detail', args=[field.pk]) + '?tab=usage&range=year&year=2026'
        response = self.client.get(url)
        self.assertEqual(response.context['usage_period_label'], 'year 2026')
        self.assertNotContains(response, 'for the 2026')
        self.assertNotContains(response, 'in the 2026')

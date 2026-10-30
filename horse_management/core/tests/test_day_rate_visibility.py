"""A placement's day rate is money, so it shows only to users who can see
charges — the same rule as a health record's cost. The rate's name still
shows to everyone."""

import re
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access

PANE = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'horse-preview-body'}
RATE = '7.25'
# The pages write the pound sign as £ or &pound;.
SHOWN_RATE = re.compile(r'(£|&pound;)7\.25')


class DayRateVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        owner = Owner.objects.create(name='Jo Bloggs')
        cls.location = Location.objects.create(name='Big Field', site='Colgate')
        cls.old_location = old_location = Location.objects.create(name='Top Yard', site='Colgate')
        rate = RateType.objects.create(name='Grass Livery', daily_rate=Decimal(RATE))
        cls.horse = Horse.objects.create(name='Dobbin')
        Placement.objects.create(
            horse=cls.horse, owner=owner, location=old_location, rate_type=rate,
            start_date=today - timedelta(days=30), end_date=today - timedelta(days=11),
        )
        Placement.objects.create(
            horse=cls.horse, owner=owner, location=cls.location, rate_type=rate,
            start_date=today - timedelta(days=10),
        )

    def pages(self):
        return {
            'horse page': self.client.get(reverse('horse_detail', args=[self.horse.pk])),
            'preview panel': self.client.get(reverse('horse_preview', args=[self.horse.pk]), **PANE),
            'location page': self.client.get(reverse('location_detail', args=[self.old_location.pk]) + '?tab=history'),
            'movements tab': self.client.get(reverse('horse_list') + '?tab=movements'),
        }

    def test_rate_shown_to_users_who_can_see_charges(self):
        self.client.force_login(make_admin())
        for page, response in self.pages().items():
            with self.subTest(page=page):
                self.assertEqual(response.status_code, 200)
                self.assertRegex(response.content.decode(), SHOWN_RATE)

    def test_rate_hidden_without_charges_access(self):
        self.client.force_login(make_user_with_access(
            'nocharges', horses='full', locations='view', health='view',
        ))
        for page, response in self.pages().items():
            with self.subTest(page=page):
                self.assertEqual(response.status_code, 200)
                self.assertNotRegex(response.content.decode(), SHOWN_RATE)
                self.assertContains(response, 'Grass Livery')

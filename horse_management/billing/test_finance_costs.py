"""Finances: a charge with a margin counts its cost, not its price
(pre-launch review)."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import Horse, Owner
from core.roles_testutils import make_admin


class FinanceCostTests(TestCase):
    def test_costs_use_the_cost_amount_when_there_is_a_margin(self):
        self.client.force_login(make_admin())
        horse = Horse.objects.create(name='Ghost')
        owner = Owner.objects.create(name='Anna')
        today = timezone.localdate()
        ExtraCharge.objects.create(
            horse=horse, owner=owner, charge_type='vet', date=today, description='Visit',
            amount=Decimal('120.00'), cost_amount=Decimal('100.00'),
        )
        ExtraCharge.objects.create(
            horse=horse, owner=owner, charge_type='farrier', date=today, description='Trim',
            amount=Decimal('30.00'),
        )
        response = self.client.get(reverse('finances'))
        self.assertAlmostEqual(response.context['costs_this_month'], 130.0)

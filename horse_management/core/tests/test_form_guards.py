"""Server-side guards that the rendered choices only pretended to enforce."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, FeedOut
from core.forms import MoveHorseForm, NewArrivalForm, SingleArrivalForm
from core.models import Horse, Location, Owner, Placement, RateType
from core.roles_testutils import make_admin
from health.forms import FarrierVisitForm
from health.models import FarrierVisit


class ArchivedLocationFormTests(TestCase):
    """ModelChoiceField validates against .queryset, not .choices, so setting
    grouped choices alone still accepted an archived location's pk."""

    def setUp(self):
        self.archived = Location.objects.create(site='S', name='Old', is_archived=True)
        self.live = Location.objects.create(site='S', name='New')
        self.owner = Owner.objects.create(name='Jo')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)

    def test_move_form_rejects_archived(self):
        form = MoveHorseForm({
            'new_location': self.archived.pk, 'new_owner': self.owner.pk,
            'new_rate_type': self.rate.pk, 'move_date': timezone.localdate().isoformat(),
        })
        self.assertFalse(form.is_valid())
        self.assertIn('new_location', form.errors)

    def test_move_form_accepts_live(self):
        form = MoveHorseForm({
            'new_location': self.live.pk, 'new_owner': self.owner.pk,
            'new_rate_type': self.rate.pk, 'move_date': timezone.localdate().isoformat(),
        })
        self.assertNotIn('new_location', form.errors)

    def test_single_arrival_form_rejects_archived(self):
        form = SingleArrivalForm({
            'location': self.archived.pk, 'owner': self.owner.pk,
            'rate_type': self.rate.pk, 'arrival_date': timezone.localdate().isoformat(),
        })
        self.assertFalse(form.is_valid())
        self.assertIn('location', form.errors)

    def test_new_arrival_form_rejects_archived(self):
        form = NewArrivalForm({'location': self.archived.pk})
        self.assertFalse(form.is_valid())
        self.assertIn('location', form.errors)
        form = NewArrivalForm({'location': self.live.pk})
        self.assertNotIn('location', form.errors)


class FarrierDateEditTests(TestCase):
    def test_changing_visit_date_recomputes_due_date(self):
        horse = Horse.objects.create(name='Ghost')
        first = timezone.localdate() - timedelta(days=60)
        visit = FarrierVisit.objects.create(horse=horse, date=first, work_done='trim')
        self.assertEqual(visit.next_due_date, first + timedelta(weeks=6))
        later = first + timedelta(days=14)
        form = FarrierVisitForm({
            'horse': horse.pk, 'date': later.isoformat(),
            'work_done': visit.work_done,
            'next_due_date': visit.next_due_date.isoformat(),  # untouched
            'cost': '0.00', 'notes': '',
        }, instance=visit)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.next_due_date, later + timedelta(weeks=6))


class ZeroCostRechargeTests(TestCase):
    def setUp(self):
        self.client.force_login(make_admin())
        self.location = Location.objects.create(site='S', name='F')
        owner = Owner.objects.create(name='Jo')
        rate = RateType.objects.create(name='Full', daily_rate=10)
        self.horse = Horse.objects.create(name='Ghost')
        Placement.objects.create(
            horse=self.horse, owner=owner, location=self.location, rate_type=rate,
            start_date=timezone.localdate() - timedelta(days=10),
        )

    def test_no_cost_means_no_charges(self):
        resp = self.client.post(
            reverse('feed_out_create', args=[self.location.pk]),
            {
                'date': timezone.localdate().isoformat(), 'feed_type': 'hay',
                'quantity': '2 bales', 'total_cost': '', 'is_recharged': 'on',
                'recharge_horses': [str(self.horse.pk), 'abc'], 'notes': '',
            },
        )
        self.assertEqual(resp.status_code, 302, getattr(resp, 'context', None) and resp.context['form'].errors)
        self.assertEqual(FeedOut.objects.count(), 1)
        self.assertFalse(FeedOut.objects.get().is_recharged)
        self.assertEqual(ExtraCharge.objects.count(), 0)

    def test_priced_feed_out_still_recharges(self):
        resp = self.client.post(
            reverse('feed_out_create', args=[self.location.pk]),
            {
                'date': timezone.localdate().isoformat(), 'feed_type': 'hay',
                'quantity': '2 bales', 'total_cost': '30.00', 'is_recharged': 'on',
                'recharge_horses': [str(self.horse.pk)], 'notes': '',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('30.00'))

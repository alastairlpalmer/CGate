"""Ownership is shown in one place and the open stay follows it.

The Ownership card on the horse page is the only owner shown. Saving the
ownership screen points the open stay (the one livery is billed against)
at the primary share; editing the open stay's owner moves a single share
the other way. Past stays keep the owner they were billed to. The
``sync_placement_owners`` command finds and fixes stays from before this
rule.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin
from core.services import PlacementService


class OwnershipSyncTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        self.today = timezone.localdate()
        self.location = Location.objects.create(name='Top Paddock', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.milly = Owner.objects.create(name='Milly Hine')
        self.maite = Owner.objects.create(name='Maite Marre')
        self.horse = Horse.objects.create(name='Antoinette')
        self.stay = Placement.objects.create(
            horse=self.horse, owner=self.milly, location=self.location,
            rate_type=self.rate, start_date=self.today - timedelta(days=7),
        )

    def _shares_post(self, *rows):
        data = {
            'ownership_shares-TOTAL_FORMS': str(len(rows)),
            'ownership_shares-INITIAL_FORMS': '0',
            'ownership_shares-MIN_NUM_FORMS': '0',
            'ownership_shares-MAX_NUM_FORMS': '1000',
        }
        for i, (owner, pct, primary) in enumerate(rows):
            data[f'ownership_shares-{i}-owner'] = owner.pk
            data[f'ownership_shares-{i}-share_percentage'] = pct
            data[f'ownership_shares-{i}-notes'] = ''
            if primary:
                data[f'ownership_shares-{i}-is_primary_contact'] = 'on'
        return data

    def test_horse_page_shows_ownership_once(self):
        OwnershipShare.objects.create(horse=self.horse, owner=self.maite, share_percentage=100, is_primary_contact=True)
        page = self.client.get(reverse('horse_detail', args=[self.horse.pk])).content.decode()
        placement_card = page[page.index('Current Placement'):page.index('Details')]
        self.assertNotIn('Owner', placement_card)
        self.assertNotIn('Milly Hine', placement_card)
        self.assertIn('Maite Marre', page)

    def test_saving_ownership_moves_the_open_stay(self):
        response = self.client.post(
            reverse('horse_ownership', args=[self.horse.pk]),
            self._shares_post((self.maite, '100', True)),
            follow=True,
        )
        self.stay.refresh_from_db()
        self.assertEqual(self.stay.owner, self.maite)
        self.assertContains(response, 'The current stay is now billed to Maite Marre.')

    def test_co_ownership_bills_the_stay_to_the_primary_contact(self):
        third = Owner.objects.create(name='Third Party')
        self.client.post(
            reverse('horse_ownership', args=[self.horse.pk]),
            self._shares_post((self.maite, '60', False), (third, '40', True)),
        )
        self.stay.refresh_from_db()
        self.assertEqual(self.stay.owner, third)

    def test_past_stays_are_left_alone(self):
        old = Placement.objects.create(
            horse=self.horse, owner=self.milly, location=self.location, rate_type=self.rate,
            start_date=self.today - timedelta(days=60), end_date=self.today - timedelta(days=8),
        )
        self.client.post(reverse('horse_ownership', args=[self.horse.pk]), self._shares_post((self.maite, '100', True)))
        old.refresh_from_db()
        self.assertEqual(old.owner, self.milly)

    def test_the_open_stay_does_not_offer_an_owner(self):
        """The Ownership screen is the one place the current owner changes:
        the open stay's edit form has no owner field, and an owner sent
        anyway is ignored. A past stay keeps its owner field."""
        OwnershipShare.objects.create(horse=self.horse, owner=self.milly, share_percentage=100, is_primary_contact=True)
        page = self.client.get(reverse('placement_update', args=[self.stay.pk])).content.decode()
        self.assertNotIn('name="owner"', page)
        self.assertIn("billed to the owner on the horse's Ownership card", page)
        self.client.post(reverse('placement_update', args=[self.stay.pk]), {
            'horse': self.horse.pk, 'owner': self.maite.pk, 'location': self.location.pk,
            'rate_type': self.rate.pk, 'start_date': self.stay.start_date.isoformat(),
            'end_date': '', 'expected_departure': '', 'notes': 'gate fixed',
        })
        self.stay.refresh_from_db()
        self.assertEqual(self.stay.owner, self.milly)
        self.assertEqual(self.stay.notes, 'gate fixed')
        self.assertEqual(self.horse.ownership_shares.get().owner, self.milly)

    def test_a_new_stay_still_takes_an_owner(self):
        page = self.client.get(reverse('placement_create')).content.decode()
        self.assertIn('name="owner"', page)

    def test_editing_a_past_stay_leaves_the_share(self):
        OwnershipShare.objects.create(horse=self.horse, owner=self.milly, share_percentage=100, is_primary_contact=True)
        old = Placement.objects.create(
            horse=self.horse, owner=self.milly, location=self.location, rate_type=self.rate,
            start_date=self.today - timedelta(days=60), end_date=self.today - timedelta(days=8),
        )
        self.client.post(reverse('placement_update', args=[old.pk]), {
            'horse': self.horse.pk, 'owner': self.maite.pk, 'location': self.location.pk,
            'rate_type': self.rate.pk, 'start_date': old.start_date.isoformat(),
            'end_date': old.end_date.isoformat(), 'expected_departure': '', 'notes': '',
        })
        old.refresh_from_db()
        self.assertEqual(old.owner, self.maite)
        self.assertEqual(self.horse.ownership_shares.get().owner, self.milly)

    def test_timeline_names_who_a_past_stay_was_billed_to_only_when_it_differs(self):
        OwnershipShare.objects.create(horse=self.horse, owner=self.maite, share_percentage=100, is_primary_contact=True)
        self.stay.owner = self.maite
        self.stay.save()
        Placement.objects.create(
            horse=self.horse, owner=self.milly, location=self.location, rate_type=self.rate,
            start_date=self.today - timedelta(days=60), end_date=self.today - timedelta(days=8),
        )
        page = self.client.get(reverse('horse_detail', args=[self.horse.pk])).content.decode()
        self.assertEqual(page.count('Billed to Milly Hine'), 1)
        self.assertNotIn('Billed to Maite Marre', page)

    def test_sync_service_returns_none_without_shares_or_change(self):
        self.assertIsNone(PlacementService.sync_placement_owner(self.horse))
        OwnershipShare.objects.create(horse=self.horse, owner=self.milly, share_percentage=100, is_primary_contact=True)
        self.assertIsNone(PlacementService.sync_placement_owner(self.horse))

    def test_command_lists_then_fixes_with_write(self):
        OwnershipShare.objects.create(horse=self.horse, owner=self.maite, share_percentage=Decimal('100'), is_primary_contact=True)
        out = StringIO()
        call_command('sync_placement_owners', stdout=out)
        self.assertIn('Antoinette: stay billed to Milly Hine, Ownership card says Maite Marre', out.getvalue())
        self.assertIn('1 stay would change', out.getvalue())
        self.stay.refresh_from_db()
        self.assertEqual(self.stay.owner, self.milly)

        out = StringIO()
        call_command('sync_placement_owners', '--write', stdout=out)
        self.assertIn('1 stay updated', out.getvalue())
        self.stay.refresh_from_db()
        self.assertEqual(self.stay.owner, self.maite)

        out = StringIO()
        call_command('sync_placement_owners', stdout=out)
        self.assertIn('already names', out.getvalue())

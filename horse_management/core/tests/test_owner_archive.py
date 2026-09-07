"""Tests for deleting, archiving and restoring owners.

An owner nothing points at is deleted. An owner with history (stays,
invoices, shares) is archived instead: hidden from the Owners page and
every picker, every record kept. An owner with horses on the yard cannot
be archived until the horses move on.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import ArrivalForm, OwnershipShareFormSet, PlacementForm
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin


class OwnerArchiveTests(TestCase):

    def setUp(self):
        self.client.force_login(make_admin())
        self.today = timezone.localdate()
        self.location = Location.objects.create(name='Top Paddock', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)

    def _stay(self, owner, name='Bella', ended=False):
        horse = Horse.objects.create(name=name, is_active=not ended)
        return Placement.objects.create(
            horse=horse, owner=owner, location=self.location, rate_type=self.rate,
            start_date=self.today - timedelta(days=30),
            end_date=self.today - timedelta(days=1) if ended else None,
        )

    # ── the one button ───────────────────────────────────────────────
    def test_owner_page_offers_delete_when_nothing_points_at_them(self):
        owner = Owner.objects.create(name='Nobody Yet')
        page = self.client.get(reverse('owner_detail', args=[owner.pk])).content.decode()
        self.assertIn(reverse('owner_delete', args=[owner.pk]), page)
        self.assertNotIn(reverse('owner_archive', args=[owner.pk]), page)

    def test_owner_page_offers_archive_when_there_is_history(self):
        owner = Owner.objects.create(name='Jo Bloggs')
        self._stay(owner, ended=True)
        page = self.client.get(reverse('owner_detail', args=[owner.pk])).content.decode()
        self.assertIn(reverse('owner_archive', args=[owner.pk]), page)
        self.assertNotIn(reverse('owner_delete', args=[owner.pk]), page)

    def test_owner_page_explains_what_blocks_archiving(self):
        owner = Owner.objects.create(name='Jo Bloggs')
        self._stay(owner)
        page = self.client.get(reverse('owner_detail', args=[owner.pk])).content.decode()
        self.assertIn('1 horse is on the yard under this owner', page)
        self.assertIn('disabled', page)

    # ── delete ───────────────────────────────────────────────────────
    def test_delete_an_unused_owner(self):
        owner = Owner.objects.create(name='Nobody Yet')
        response = self.client.post(reverse('owner_delete', args=[owner.pk]))
        self.assertRedirects(response, reverse('owner_list'))
        self.assertFalse(Owner.objects.filter(pk=owner.pk).exists())

    def test_delete_with_history_archives_instead(self):
        owner = Owner.objects.create(name='Jo Bloggs')
        stay = self._stay(owner, ended=True)
        response = self.client.post(reverse('owner_delete', args=[owner.pk]), follow=True)
        owner.refresh_from_db()
        self.assertTrue(owner.is_archived)
        self.assertIsNotNone(owner.archived_at)
        self.assertTrue(Placement.objects.filter(pk=stay.pk).exists())
        self.assertContains(response, 'archived rather than deleted')

    def test_delete_needs_a_post(self):
        owner = Owner.objects.create(name='Nobody Yet')
        response = self.client.get(reverse('owner_delete', args=[owner.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Owner.objects.filter(pk=owner.pk).exists())

    # ── archive ──────────────────────────────────────────────────────
    def test_archive_keeps_the_history(self):
        owner = Owner.objects.create(name='Jo Bloggs')
        stay = self._stay(owner, ended=True)
        self.client.post(reverse('owner_archive', args=[owner.pk]))
        owner.refresh_from_db()
        self.assertTrue(owner.is_archived)
        self.assertEqual(Placement.objects.get(pk=stay.pk).owner, owner)

    def test_archive_is_blocked_while_horses_are_on_the_yard(self):
        owner = Owner.objects.create(name='Jo Bloggs')
        self._stay(owner)
        response = self.client.post(reverse('owner_archive', args=[owner.pk]), follow=True)
        owner.refresh_from_db()
        self.assertFalse(owner.is_archived)
        self.assertContains(response, "can&#x27;t be archived")

    def test_archive_is_blocked_by_a_share_in_an_active_horse(self):
        billed = Owner.objects.create(name='Billed Owner')
        co_owner = Owner.objects.create(name='Co Owner')
        stay = self._stay(billed)
        OwnershipShare.objects.create(horse=stay.horse, owner=billed, share_percentage=Decimal('50'), is_primary_contact=True)
        OwnershipShare.objects.create(horse=stay.horse, owner=co_owner, share_percentage=Decimal('50'))
        self.assertEqual(len(co_owner.archive_blockers()), 1)
        self.client.post(reverse('owner_archive', args=[co_owner.pk]))
        co_owner.refresh_from_db()
        self.assertFalse(co_owner.is_archived)

    def test_restore_puts_the_owner_back(self):
        owner = Owner.objects.create(name='Jo Bloggs', is_archived=True, archived_at=timezone.now())
        self.client.post(reverse('owner_restore', args=[owner.pk]))
        owner.refresh_from_db()
        self.assertFalse(owner.is_archived)
        self.assertIsNone(owner.archived_at)

    # ── where archived owners go ─────────────────────────────────────
    def test_archived_owners_leave_the_list_and_have_their_own_page(self):
        Owner.objects.create(name='Still Here')
        Owner.objects.create(name='Gone Away', is_archived=True)
        page = self.client.get(reverse('owner_list')).content.decode()
        self.assertIn('Still Here', page)
        self.assertNotIn('Gone Away', page)
        self.assertIn('1 archived owner', page)
        archived = self.client.get(reverse('owner_list') + '?archived=1').content.decode()
        self.assertIn('Gone Away', archived)
        self.assertNotIn('Still Here', archived)

    def test_archived_owners_leave_the_pickers(self):
        live = Owner.objects.create(name='Still Here')
        gone = Owner.objects.create(name='Gone Away', is_archived=True)
        form = ArrivalForm()
        self.assertIn(live, form.fields['owner'].queryset)
        self.assertNotIn(gone, form.fields['owner'].queryset)
        # An archived pk is refused, not just hidden.
        self.assertFalse(PlacementForm(data={'owner': gone.pk}).is_valid())

    def test_an_old_stay_can_still_be_edited_with_its_archived_owner(self):
        gone = Owner.objects.create(name='Gone Away', is_archived=True)
        stay = self._stay(gone, ended=True)
        form = PlacementForm(instance=stay)
        self.assertIn(gone, form.fields['owner'].queryset)
        formset = OwnershipShareFormSet(instance=stay.horse)
        self.assertNotIn(gone, formset.forms[0].fields['owner'].queryset)

    def test_archived_owner_page_shows_the_banner_and_restore(self):
        owner = Owner.objects.create(name='Gone Away', is_archived=True, archived_at=timezone.now())
        page = self.client.get(reverse('owner_detail', args=[owner.pk])).content.decode()
        self.assertIn('Archived on', page)
        self.assertIn(reverse('owner_restore', args=[owner.pk]), page)

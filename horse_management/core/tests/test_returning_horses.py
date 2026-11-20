"""Returning horses (core/returning.py): New Arrival finds the old record by
chip, or shows same-name horses as possible matches, and "Bring it back"
logs the arrival on the old record instead of making a second one."""

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.returning import find_returning
from core.roles_testutils import make_admin, make_user_with_access

CHIP = '826098000123456'


class Base(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.client.force_login(make_admin())
        self.owner = Owner.objects.create(name='Jo Bloggs')
        self.new_owner = Owner.objects.create(name='Sam Hill')
        self.field = Location.objects.create(name='Top Field', site='Main')
        self.barn = Location.objects.create(name='Back Barn', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)
        self.rosie = self.departed('Rosie', microchip=CHIP, date_of_birth=date(2015, 5, 1))

    def departed(self, name, **kw):
        horse = Horse.objects.create(name=name, **kw)
        OwnershipShare.objects.create(horse=horse, owner=self.owner, share_percentage=100,
                                      is_primary_contact=True)
        Placement.objects.create(horse=horse, owner=self.owner, location=self.field, rate_type=self.rate,
                                 start_date=self.today - timedelta(days=400),
                                 end_date=self.today - timedelta(days=100))
        Horse.objects.filter(pk=horse.pk).update(is_active=False)
        horse.refresh_from_db()
        return horse

    def form(self, **kw):
        data = {'name': '', 'microchip': '', 'owner': self.new_owner.pk, 'location': self.barn.pk,
                'rate_type': self.rate.pk, 'arrival_date': self.today.isoformat()}
        data.update(kw)
        return data

    def post(self, **kw):
        return self.client.post(reverse('horse_new_arrival'), self.form(**kw))


class FindTests(Base):

    def test_by_chip_in_any_format(self):
        found = find_returning(microchip='FDX-B 826 098 000 123 456')
        self.assertEqual(found.by_chip, self.rosie)
        self.assertTrue(found.chip_matches)
        self.assertIsNone(found.by_chip.here)
        self.assertEqual(found.by_chip.left.location, self.field)

    def test_by_name_only_suggests_with_the_same_foaling_date_first(self):
        other = self.departed('ROSIE', date_of_birth=date(2009, 1, 1))
        found = find_returning(name=' rosie ', date_of_birth=date(2015, 5, 1))
        self.assertIsNone(found.by_chip)
        self.assertFalse(found.chip_matches)
        self.assertEqual(found.possible, [self.rosie, other])
        found = find_returning(name='rosie', date_of_birth=date(2009, 1, 1))
        self.assertEqual(found.possible[0], other)

    def test_an_unknown_chip_falls_back_to_the_name(self):
        found = find_returning(microchip='999000000000001', name='Rosie')
        self.assertIsNone(found.by_chip)
        self.assertEqual(found.possible, [self.rosie])

    def test_nothing(self):
        self.assertFalse(find_returning(microchip='', name='Nobody').found)


class CheckPanelTests(Base):

    def test_chip_match_offers_bring_back(self):
        response = self.client.get(reverse('horse_new_arrival_check'), {'microchip': CHIP})
        self.assertContains(response, 'This horse was here before')
        self.assertContains(response, f'name="bring_back" value="{self.rosie.pk}"')
        self.assertContains(response, 'Left Top Field on')
        self.assertContains(response, 'Make a new record anyway')

    def test_name_match_is_only_possible(self):
        response = self.client.get(reverse('horse_new_arrival_check'), {'name': 'rosie'})
        self.assertContains(response, 'Possible match')
        self.assertContains(response, 'Same name only.')
        self.assertNotContains(response, 'This horse was here before')

    def test_nothing_found_is_an_empty_answer(self):
        response = self.client.get(reverse('horse_new_arrival_check'), {'name': 'Nobody', 'date_of_birth': 'bad'})
        self.assertEqual(response.content, b'')

    def test_needs_full_horse_access(self):
        self.client.force_login(make_user_with_access('v', horses='view'))
        response = self.client.get(reverse('horse_new_arrival_check'), {'microchip': CHIP})
        self.assertNotContains(response, 'Rosie', status_code=response.status_code)

    def test_the_page_has_the_chip_first_and_the_panel(self):
        body = self.client.get(reverse('horse_new_arrival')).content.decode()
        self.assertLess(body.index('id="id_microchip"'), body.index('id="id_name"'))
        self.assertIn(f'hx-get="{reverse("horse_new_arrival_check")}"', body)


class BringBackTests(Base):

    def test_chip_match_stops_the_save_once(self):
        response = self.post(name='Rosie Two', microchip=CHIP)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Not saved yet')
        self.assertContains(response, 'This horse was here before')
        self.assertEqual(Horse.objects.count(), 1)
        # "Make a new record anyway" goes on.
        response = self.post(name='Rosie Two', microchip=CHIP, new_record='1')
        self.assertEqual(Horse.objects.count(), 2)

    def test_bring_back_by_chip_keeps_the_old_record(self):
        response = self.post(microchip=CHIP, bring_back=str(self.rosie.pk), owner='')
        self.assertRedirects(response, reverse('horse_detail', args=[self.rosie.pk]), fetch_redirect_response=False)
        self.assertEqual(Horse.objects.count(), 1)
        self.rosie.refresh_from_db()
        self.assertTrue(self.rosie.is_active)
        stay = self.rosie.placements.get(end_date__isnull=True)
        self.assertEqual(stay.location, self.barn)
        self.assertEqual(stay.start_date, self.today)
        self.assertEqual(stay.owner, self.owner)  # the old primary owner, as Log Arrival does
        self.assertEqual(self.rosie.placements.count(), 2)  # the history stays
        page = self.client.get(reverse('horse_detail', args=[self.rosie.pk]))
        self.assertContains(page, 'is back at Back Barn, on the old record')

    def test_bring_back_by_name_keeps_a_new_chip(self):
        dobbin = self.departed('Dobbin')
        self.post(name='Dobbin', microchip='FDX-B 372 141 000 999 888', bring_back=str(dobbin.pk))
        dobbin.refresh_from_db()
        self.assertEqual(dobbin.microchip, '372141000999888')
        self.assertTrue(dobbin.is_active)

    def test_a_chip_on_file_is_not_overwritten(self):
        self.post(name='Rosie', microchip='372141000999888', bring_back=str(self.rosie.pk))
        self.rosie.refresh_from_db()
        self.assertEqual(self.rosie.microchip, CHIP)

    def test_missing_placement_shows_the_error_and_keeps_the_panel(self):
        response = self.post(microchip=CHIP, bring_back=str(self.rosie.pk), location='')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="returning-chip"')
        self.assertFalse(self.rosie.placements.filter(end_date__isnull=True).exists())

    def test_a_horse_on_the_yard_is_not_brought_back(self):
        here = Horse.objects.create(name='Here', microchip='372141000555666')
        Placement.objects.create(horse=here, owner=self.owner, location=self.field, rate_type=self.rate,
                                 start_date=self.today - timedelta(days=10))
        response = self.client.get(reverse('horse_new_arrival_check'), {'microchip': '372141000555666'})
        self.assertContains(response, 'on the yard now')
        self.assertNotContains(response, 'name="bring_back"')
        response = self.post(name='Here', bring_back=str(here.pk))
        self.assertContains(response, 'Here is on the yard now.')
        self.assertEqual(here.placements.count(), 1)

    def test_no_match_still_makes_a_new_horse(self):
        response = self.post(name='Brand New', microchip='372141000777000')
        horse = Horse.objects.get(name='Brand New')
        self.assertRedirects(response, reverse('horse_detail', args=[horse.pk]), fetch_redirect_response=False)

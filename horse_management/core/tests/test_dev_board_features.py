"""Dev board Features cards, done on 22 Nov 2026:

- "The pending departures tab is showing as individual buttons": Confirm
  all departures shows on every tab of the Notifications panel.
- "When adding a new horse, don't get ownership split": Add owner on the
  new-horse form, and other owners on the Create and arrive page.
- "Confirmation step when horses actually depart": a passed expected
  departure asks "Did it leave?".
- "Yesterday, Today, Tomorrow on each date field": the script is on every
  page (static/js/date_quick.js, tested in static/js/tests).
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard import attention
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access


class Base(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.jo = Owner.objects.create(name='Jo Bloggs')
        self.sam = Owner.objects.create(name='Sam Hine')
        self.kit = Owner.objects.create(name='Kit Marre')
        self.field = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Full', daily_rate=10)

    def stay(self, name, **kw):
        horse = Horse.objects.create(name=name)
        kw.setdefault('start_date', self.today - timedelta(days=30))
        placement = Placement.objects.create(horse=horse, owner=self.jo, location=self.field,
                                             rate_type=self.rate, **kw)
        return horse, placement


class ConfirmAllOnEveryTabTests(Base):

    def test_new_and_all_tabs_have_the_button(self):
        for name in ('Avicii', 'Ballerina'):
            self.stay(name, end_date=self.today - timedelta(days=1))
        panel = reverse('notification_panel')
        self.assertContains(self.client.get(panel, {'fresh': '1'}), 'Confirm all 2 departures')
        self.assertContains(self.client.get(panel, {'tab': 'all'}), 'Confirm all 2 departures')
        self.assertContains(self.client.get(panel, {'tab': 'action'}), 'Confirm all 2 departures')
        # The page stays on the tab it was on.
        self.assertContains(self.client.get(panel, {'tab': 'all'}),
                            f'value="{panel}?tab=all&amp;fresh=1"')

    def test_not_for_a_role_that_cannot_depart(self):
        for name in ('Avicii', 'Ballerina'):
            self.stay(name, end_date=self.today - timedelta(days=1))
        self.client.force_login(make_user_with_access('looker', horses='view', dashboard='view'))
        self.assertNotContains(self.client.get(reverse('notification_panel'), {'fresh': '1'}), 'Confirm all')


class NewHorseOwnershipTests(Base):

    def shares(self, *rows):
        data = {'name': 'Avicii', 'ownership_shares-TOTAL_FORMS': str(len(rows)),
                'ownership_shares-INITIAL_FORMS': '0', 'ownership_shares-MIN_NUM_FORMS': '0',
                'ownership_shares-MAX_NUM_FORMS': '1000'}
        for i, (owner, pct, primary) in enumerate(rows):
            data[f'ownership_shares-{i}-owner'] = owner.pk
            data[f'ownership_shares-{i}-share_percentage'] = pct
            if primary:
                data[f'ownership_shares-{i}-is_primary_contact'] = 'on'
        return data

    def test_the_form_has_add_owner(self):
        body = self.client.get(reverse('horse_create')).content.decode()
        self.assertIn('data-testid="add-owner"', body)
        self.assertIn('ownership_shares-__prefix__-owner', body)  # the blank row
        self.assertIn('Shares must add up to 100%', body)

    def test_two_owners_in_one_save(self):
        response = self.client.post(reverse('horse_create'), self.shares((self.jo, '60', True), (self.sam, '40', False)))
        self.assertEqual(response.status_code, 302)
        horse = Horse.objects.get(name='Avicii')
        self.assertEqual(
            sorted((s.owner.name, s.share_percentage, s.is_primary_contact) for s in horse.ownership_shares.all()),
            [('Jo Bloggs', Decimal('60.00'), True), ('Sam Hine', Decimal('40.00'), False)])

    def test_manage_ownership_has_add_owner_too(self):
        horse = Horse.objects.create(name='Clover')
        self.assertContains(self.client.get(reverse('horse_ownership', args=[horse.pk])), 'data-testid="add-owner"')


class NewArrivalOwnershipTests(Base):

    def post(self, *others, owner_share='100'):
        data = {'name': 'Avicii', 'owner': self.jo.pk, 'owner_share': owner_share, 'location': self.field.pk,
                'rate_type': self.rate.pk, 'arrival_date': self.today.isoformat(),
                'co-TOTAL_FORMS': str(len(others)), 'co-INITIAL_FORMS': '0',
                'co-MIN_NUM_FORMS': '0', 'co-MAX_NUM_FORMS': '10'}
        for i, (owner, pct) in enumerate(others):
            data[f'co-{i}-owner'] = owner.pk if owner else ''
            data[f'co-{i}-share_percentage'] = pct
        return self.client.post(reverse('horse_new_arrival'), data)

    def shares(self):
        horse = Horse.objects.get(name='Avicii')
        return sorted((s.owner.name, s.share_percentage, s.is_primary_contact) for s in horse.ownership_shares.all())

    def test_the_page_offers_other_owners(self):
        body = self.client.get(reverse('horse_new_arrival')).content.decode()
        self.assertIn('data-testid="add-co-owner"', body)
        self.assertIn('co-__prefix__-owner', body)
        self.assertIn('This owner gets 100% ownership', body)

    def test_one_owner_gets_100(self):
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(self.shares(), [('Jo Bloggs', Decimal('100.00'), True)])

    def test_split_between_three(self):
        response = self.post((self.sam, '30'), (self.kit, '20'), owner_share='50')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.shares(), [('Jo Bloggs', Decimal('50.00'), True),
                                         ('Kit Marre', Decimal('20.00'), False),
                                         ('Sam Hine', Decimal('30.00'), False)])
        # The stay is billed to the main owner.
        self.assertEqual(Placement.objects.get(horse__name='Avicii').owner, self.jo)

    def test_shares_must_add_up(self):
        response = self.post((self.sam, '30'), owner_share='60')
        self.assertContains(response, 'The shares must add up to 100%. They add up to 90% now.')
        self.assertFalse(Horse.objects.filter(name='Avicii').exists())

    def test_an_owner_twice(self):
        response = self.post((self.jo, '50'), owner_share='50')
        self.assertContains(response, 'Jo Bloggs is on the list twice.')
        self.assertFalse(Horse.objects.filter(name='Avicii').exists())

    def test_a_row_needs_both_boxes(self):
        response = self.post((self.sam, ''), owner_share='50')
        self.assertContains(response, 'Enter this owner')
        response = self.post((None, '50'), owner_share='50')
        self.assertContains(response, 'Choose the owner for this share.')

    def test_an_empty_row_is_skipped(self):
        self.assertEqual(self.post((None, '')).status_code, 302)
        self.assertEqual(self.shares(), [('Jo Bloggs', Decimal('100.00'), True)])


class DidItLeaveTests(Base):

    def item(self):
        items = [i for i in attention.collect(self.admin, today=self.today) if i.kind == 'departure_expected']
        self.assertEqual(len(items), 1)
        return items[0]

    def test_a_passed_date_asks(self):
        horse, placement = self.stay('Avicii', expected_departure=self.today - timedelta(days=2))
        item = self.item()
        self.assertEqual(item.severity, 'overdue')
        self.assertIn('Did it leave?', item.detail)
        self.assertEqual([a.label for a in item.actions], ['Yes, it left', 'Still here', 'Change date'])
        # It reaches the Notifications panel with its buttons.
        body = self.client.get(reverse('notification_panel'), {'tab': 'action', 'fresh': '1'}).content.decode()
        self.assertIn('Did it leave?', body)
        self.assertIn(reverse('expected_departure_left', args=[placement.pk]), body)

    def test_a_coming_date_does_not_ask(self):
        self.stay('Avicii', expected_departure=self.today + timedelta(days=3))
        item = self.item()
        self.assertEqual(item.severity, 'due')
        self.assertEqual(item.actions, [])

    def test_yes_it_left(self):
        horse, placement = self.stay('Avicii', expected_departure=self.today - timedelta(days=2))
        response = self.client.post(reverse('expected_departure_left', args=[placement.pk]), follow=True)
        self.assertContains(response, 'Avicii left Top Field on')
        placement.refresh_from_db()
        horse.refresh_from_db()
        self.assertEqual(placement.end_date, self.today - timedelta(days=2))
        self.assertFalse(horse.is_active)
        self.assertFalse([i for i in attention.collect(self.admin, today=self.today) if i.kind == 'departure_expected'])

    def test_still_here(self):
        horse, placement = self.stay('Avicii', expected_departure=self.today - timedelta(days=2))
        response = self.client.post(reverse('expected_departure_stays', args=[placement.pk]), follow=True)
        self.assertContains(response, 'Avicii is still here.')
        placement.refresh_from_db()
        self.assertIsNone(placement.expected_departure)
        self.assertIsNone(placement.end_date)
        horse.refresh_from_db()
        self.assertTrue(horse.is_active)

    def test_buttons_need_full_access_and_a_post(self):
        horse, placement = self.stay('Avicii', expected_departure=self.today - timedelta(days=2))
        self.assertEqual(self.client.get(reverse('expected_departure_left', args=[placement.pk])).status_code, 405)
        viewer = make_user_with_access('looker', horses='view', dashboard='view')
        self.assertEqual(self.item().actions != [], True)
        self.client.force_login(viewer)
        self.client.post(reverse('expected_departure_left', args=[placement.pk]))
        placement.refresh_from_db()
        self.assertIsNone(placement.end_date)
        items = [i for i in attention.collect(viewer, today=self.today) if i.kind == 'departure_expected']
        self.assertEqual(items[0].actions, [])

    def test_a_closed_stay_is_left_alone(self):
        horse, placement = self.stay('Avicii', expected_departure=self.today - timedelta(days=2),
                                     end_date=self.today - timedelta(days=1))
        self.client.post(reverse('expected_departure_left', args=[placement.pk]))
        placement.refresh_from_db()
        self.assertEqual(placement.end_date, self.today - timedelta(days=1))


class DateButtonsTests(Base):

    def test_the_script_is_on_every_page(self):
        self.assertContains(self.client.get(reverse('dashboard')), 'js/date_quick.js')

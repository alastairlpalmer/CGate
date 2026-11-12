"""The right rate for where a horse is (docs/BARNS_AND_STABLES_PLAN.md, phase 2b).

Two halves, as principle 3.7 has them. The move form *offers* the right
rate when a horse goes between a field and a barn, and never sets one
without showing it. And whatever path a horse took — a bulk move, a
foaling, an edit, the admin — one left on a rate that belongs elsewhere
shows on the dashboard, so it is seen before the invoice is.

A yard that leaves every rate type on "Fields and barns" sees none of it.
"""

import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.dashboard import activity, attention
from core.models import Horse, Location, Owner, Placement, RateType
from core.rates import (
    mismatch_text, mismatched_placements, rate_mismatch, suggest_rate,
)
from core.roles_testutils import make_admin, make_user_with_access
from core.services import PlacementService
from core.stables import StableService, add_stables

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}
BARN, FIELD = RateType.AppliesTo.BARN, RateType.AppliesTo.FIELD
PRICE = '31.75'


class RateTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.owner = Owner.objects.create(name='Jo Bloggs')
        cls.other_owner = Owner.objects.create(name='Sam Reed')
        cls.today = timezone.localdate()
        cls.barn = Location.objects.create(
            name='Top Barn', site='Manor Farm', location_type='barn', layout='aisle')
        add_stables(cls.barn, 6)
        cls.field = Location.objects.create(name='Back Paddock', site='Manor Farm')
        cls.other_field = Location.objects.create(name='Hill Field', site='Manor Farm')
        cls.box = RateType.objects.create(name='Box care', daily_rate=Decimal(PRICE), applies_to=BARN)
        cls.grass = RateType.objects.create(name='Field care', daily_rate=Decimal('12'), applies_to=FIELD)
        cls.either = RateType.objects.create(name='Full livery', daily_rate=Decimal('40'))

    def setUp(self):
        self.client.force_login(self.admin)

    def horse_in(self, location, rate, name='Marram', owner=None, days_ago=30):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=owner or self.owner, location=location, rate_type=rate,
            start_date=self.today - timedelta(days=days_ago),
        )
        return horse

    def current(self, horse):
        return horse.placements.select_related('rate_type', 'location').get(end_date__isnull=True)


class MismatchTests(RateTestCase):
    def test_a_rate_for_the_other_type_is_a_mismatch(self):
        self.assertTrue(rate_mismatch(self.grass, self.barn))
        self.assertTrue(rate_mismatch(self.box, self.field))
        self.assertFalse(rate_mismatch(self.box, self.barn))
        self.assertFalse(rate_mismatch(self.grass, self.field))

    def test_a_rate_for_both_is_never_a_mismatch(self):
        self.assertFalse(rate_mismatch(self.either, self.barn))
        self.assertFalse(rate_mismatch(self.either, self.field))

    def test_text_names_the_rate_and_no_price(self):
        self.assertEqual(mismatch_text(self.grass, self.barn), 'In a barn on Field care')
        self.assertEqual(mismatch_text(self.box, self.field), 'In a field on Box care')

    def test_mismatched_placements(self):
        wrong = self.horse_in(self.barn, self.grass, 'Wrong')
        self.horse_in(self.barn, self.box, 'Right')
        self.horse_in(self.barn, self.either, 'Either')
        gone = self.horse_in(self.field, self.box, 'Gone')
        PlacementService.depart_horse(gone, self.today - timedelta(days=1))
        self.assertEqual([p.horse for p in mismatched_placements()], [wrong])


class SuggestRateTests(RateTestCase):
    def suggest(self, horse, location):
        return suggest_rate(horse, self.current(horse), location)

    def test_nothing_to_say_inside_one_type(self):
        horse = self.horse_in(self.field, self.grass)
        self.assertIsNone(self.suggest(horse, self.other_field))

    def test_nothing_to_say_on_a_rate_for_both(self):
        horse = self.horse_in(self.field, self.either)
        self.assertIsNone(self.suggest(horse, self.barn))

    def test_nothing_to_say_with_no_placement(self):
        self.assertIsNone(suggest_rate(Horse.objects.create(name='New'), None, self.barn))

    def test_the_only_rate_for_the_new_type(self):
        horse = self.horse_in(self.field, self.grass)
        suggestion = self.suggest(horse, self.barn)
        self.assertEqual(suggestion.rate, self.box)
        self.assertIn('from a field to a barn', suggestion.text)
        self.assertIn('It is on Field care', suggestion.text)
        self.assertIn('Suggested rate: Box care', suggestion.text)
        self.assertNotIn(PRICE, suggestion.text)
        self.assertNotIn('£', suggestion.text)

    def test_and_back_the_other_way(self):
        horse = self.horse_in(self.barn, self.box)
        suggestion = self.suggest(horse, self.field)
        self.assertEqual(suggestion.rate, self.grass)
        self.assertIn('from a barn to a field', suggestion.text)

    def test_the_rate_this_horse_last_had_there_comes_first(self):
        rest = RateType.objects.create(name='Box rest', daily_rate=Decimal('30'), applies_to=BARN)
        horse = Horse.objects.create(name='Marram')
        Placement.objects.create(
            horse=horse, owner=self.owner, location=self.barn, rate_type=rest,
            start_date=self.today - timedelta(days=60), end_date=self.today - timedelta(days=31))
        Placement.objects.create(
            horse=horse, owner=self.owner, location=self.field, rate_type=self.grass,
            start_date=self.today - timedelta(days=30))
        self.assertEqual(self.suggest(horse, self.barn).rate, rest)

    def test_then_the_rate_the_owners_other_horses_have_there(self):
        agreed = RateType.objects.create(name='Box care (Reed)', daily_rate=Decimal('18'), applies_to=BARN)
        self.horse_in(self.barn, agreed, 'Stablemate', owner=self.other_owner)
        horse = self.horse_in(self.field, self.grass, owner=self.other_owner)
        self.assertEqual(self.suggest(horse, self.barn).rate, agreed)

    def test_an_owner_on_two_rates_there_is_no_guide(self):
        agreed = RateType.objects.create(name='Box care (Reed)', daily_rate=Decimal('18'), applies_to=BARN)
        self.horse_in(self.barn, agreed, 'One', owner=self.other_owner)
        self.horse_in(self.barn, self.box, 'Two', owner=self.other_owner)
        horse = self.horse_in(self.field, self.grass, owner=self.other_owner)
        suggestion = self.suggest(horse, self.barn)
        # Two barn rates and nothing to choose between them: say so, guess nothing.
        self.assertIsNone(suggestion.rate)
        self.assertIn('Choose the new rate', suggestion.text)
        self.assertIn('Box care', suggestion.text)

    def test_an_inactive_rate_is_never_offered(self):
        self.box.is_active = False
        self.box.save()
        horse = self.horse_in(self.field, self.grass)
        suggestion = self.suggest(horse, self.barn)
        self.assertIsNone(suggestion.rate)
        self.assertIn('Check the rate', suggestion.text)

    def test_a_past_rate_that_does_not_fit_is_not_offered(self):
        horse = Horse.objects.create(name='Marram')
        # It was once in the barn on a field rate, by mistake.
        Placement.objects.create(
            horse=horse, owner=self.owner, location=self.barn, rate_type=self.grass,
            start_date=self.today - timedelta(days=60), end_date=self.today - timedelta(days=31))
        Placement.objects.create(
            horse=horse, owner=self.owner, location=self.field, rate_type=self.grass,
            start_date=self.today - timedelta(days=30))
        self.assertEqual(self.suggest(horse, self.barn).rate, self.box)


class MoveFormTests(RateTestCase):
    def hint(self, horse, location, user=None):
        if user is not None:
            self.client.force_login(user)
        response = self.client.get(
            reverse('stable_options'), {'horse': horse.pk, 'new_location': location.pk})
        return response, json.loads(response['HX-Trigger'])['rate-hint']

    def move(self, horse, location, **data):
        return self.client.post(reverse('horse_move', args=[horse.pk]), {
            'new_location': location.pk, 'move_date': self.today.isoformat(), 'notes': '', **data,
        }, **POPUP)

    def test_the_form_is_wired_to_ask(self):
        horse = self.horse_in(self.field, self.grass)
        response = self.client.get(reverse('horse_move', args=[horse.pk]), **POPUP)
        self.assertContains(response, f'hx-get="{reverse("stable_options")}?horse={horse.pk}"')
        self.assertContains(response, '@rate-hint=')
        self.assertContains(response, 'x-ref="rate"')
        self.assertContains(response, 'x-ref="more"')
        self.assertContains(response, 'data-testid="rate-hint"')
        self.assertContains(response, 'Keep the current rate')

    def test_the_answer_carries_the_rate_and_the_sentence(self):
        horse = self.horse_in(self.field, self.grass)
        response, hint = self.hint(horse, self.barn)
        self.assertEqual(hint['rate'], self.box.pk)
        self.assertIn('Suggested rate: Box care', hint['text'])
        # The stable picker still comes back in the body.
        self.assertContains(response, 'name="stable"')

    def test_a_move_that_needs_no_change_clears_the_hint(self):
        horse = self.horse_in(self.field, self.grass)
        _response, hint = self.hint(horse, self.other_field)
        self.assertEqual(hint, {'rate': None, 'text': ''})

    def test_no_price_reaches_a_role_that_cannot_see_charges(self):
        hand = make_user_with_access('hand', horses='full', locations='view')
        horse = self.horse_in(self.field, self.grass)
        response, hint = self.hint(horse, self.barn, user=hand)
        self.assertIn('Box care', hint['text'])
        self.assertNotIn(PRICE, hint['text'])
        self.assertNotIn(PRICE, response.content.decode())

    def test_no_hint_for_someone_who_cannot_move_horses(self):
        looker = make_user_with_access('looker', horses='view', locations='view')
        self.client.force_login(looker)
        horse = self.horse_in(self.field, self.grass)
        response = self.client.get(
            reverse('stable_options'), {'horse': horse.pk, 'new_location': self.barn.pk})
        self.assertNotIn('HX-Trigger', response)

    def test_taking_the_suggestion_says_the_rate_changed(self):
        horse = self.horse_in(self.field, self.grass)
        self.assertEqual(self.move(horse, self.barn, new_rate_type=self.box.pk).status_code, 204)
        self.assertEqual(self.current(horse).rate_type, self.box)
        page = self.client.get(reverse('horse_list'))
        self.assertContains(page, 'Rate changed to Box care.')
        self.assertNotContains(page, 'Check the rate')

    def test_keeping_the_rate_saves_it_and_warns(self):
        horse = self.horse_in(self.field, self.grass)
        self.assertEqual(self.move(horse, self.barn).status_code, 204)
        # Nothing was changed behind the form's back.
        self.assertEqual(self.current(horse).rate_type, self.grass)
        page = self.client.get(reverse('horse_list'))
        self.assertContains(page, 'in a barn on Field care. Check the rate.')

    def test_an_ordinary_move_says_nothing_about_rates(self):
        horse = self.horse_in(self.field, self.grass)
        self.move(horse, self.other_field)
        page = self.client.get(reverse('horse_list'))
        self.assertNotContains(page, 'Rate changed')
        self.assertNotContains(page, 'Check the rate')


class OtherPathsTests(RateTestCase):
    def test_a_single_arrival_on_the_wrong_rate_warns(self):
        horse = Horse.objects.create(name='Low Tide')
        self.client.post(reverse('horse_arrive', args=[horse.pk]), {
            'location': self.barn.pk, 'owner': self.owner.pk, 'rate_type': self.grass.pk,
            'arrival_date': self.today.isoformat(), 'notes': '',
        }, **POPUP)
        page = self.client.get(reverse('horse_list'))
        self.assertContains(page, 'in a barn on Field care. Check the rate.')

    def test_a_group_arrival_on_the_wrong_rate_warns_once(self):
        horses = [Horse.objects.create(name=name) for name in ('A', 'B')]
        response = self.client.post(reverse('location_arrive', args=[self.barn.pk]), {
            'horses': [h.pk for h in horses], 'owner': self.owner.pk, 'rate_type': self.grass.pk,
            'arrival_date': self.today.isoformat(), 'notes': '',
        }, follow=True)
        self.assertContains(response, 'Field care is not a rate for a barn', count=1)

    def test_a_bulk_move_names_the_horses_left_on_the_wrong_rate(self):
        from health.views import bulk_health_apply  # noqa: F401  (the view under test exists)
        wrong = self.horse_in(self.field, self.grass, 'Wrong')
        fine = self.horse_in(self.field, self.either, 'Fine')
        response = self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'move', 'horse_ids': [wrong.pk, fine.pk],
            'new_location': self.barn.pk, 'move_date': self.today.isoformat(),
        }, **POPUP)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.current(wrong).location, self.barn)
        page = self.client.get(reverse('horse_list'))
        self.assertContains(page, 'Check the rate: Wrong is still on Field care')
        self.assertNotContains(page, 'Check the rate: Wrong, Fine')


class AttentionTests(RateTestCase):
    def rates(self, user=None):
        return [item for item in attention.collect(user or self.admin) if item.kind == 'rate']

    def test_a_horse_on_the_wrong_rate_is_listed_whatever_put_it_there(self):
        # Straight into the table, as the admin or a script would.
        horse = self.horse_in(self.barn, self.grass)
        items = self.rates()
        self.assertEqual([item.horse for item in items], [horse])
        item = items[0]
        self.assertEqual(item.detail, 'In a barn on Field care')
        self.assertEqual((item.location, item.site), ('Top Barn', 'Manor Farm'))
        self.assertTrue(attention.is_inbox(item, self.today))
        self.assertEqual(item.actions[0].url, reverse('placement_update', args=[self.current(horse).pk]))

    def test_and_it_goes_once_the_rate_is_right(self):
        horse = self.horse_in(self.barn, self.grass)
        placement = self.current(horse)
        placement.rate_type = self.box
        placement.save()
        self.assertEqual(self.rates(), [])

    def test_a_yard_that_set_nothing_sees_nothing(self):
        RateType.objects.update(applies_to=RateType.AppliesTo.ANY)
        self.horse_in(self.barn, self.grass, 'One')
        self.horse_in(self.field, self.box, 'Two')
        self.assertEqual(self.rates(), [])

    def test_only_for_those_who_move_horses(self):
        self.horse_in(self.barn, self.grass)
        looker = make_user_with_access('looker', dashboard='full', horses='view', locations='view')
        self.assertEqual(self.rates(looker), [])
        hand = make_user_with_access('hand', dashboard='full', horses='full', locations='view')
        items = self.rates(hand)
        self.assertEqual(len(items), 1)
        # Seen, but the edit sheet is not theirs to open.
        self.assertEqual(items[0].actions, [])

    def test_the_dashboard_draws_it_with_no_price(self):
        self.horse_in(self.barn, self.grass)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'In a barn on Field care')
        self.assertNotContains(response, '12.00')


class SettingsTests(RateTestCase):
    def test_the_rate_form_has_the_setting(self):
        response = self.client.get(reverse('rate_type_update', args=[self.box.pk]))
        self.assertContains(response, 'name="applies_to"')
        self.assertContains(response, 'Used for')

    def test_new_rate_types_default_to_both(self):
        self.assertEqual(RateType.objects.create(name='New', daily_rate=1).applies_to, 'any')


class WorkListTests(RateTestCase):
    def test_a_rate_change_in_place_is_not_a_move_in_the_activity_feed(self):
        horse = self.horse_in(self.barn, self.box)
        rest = RateType.objects.create(name='Box rest', daily_rate=Decimal('30'), applies_to=BARN)
        PlacementService.move_horse(
            horse, new_location=self.barn, move_date=self.today - timedelta(days=2),
            new_rate_type=rest)
        texts = [event.text for day in activity.recent(self.admin) for event in day['events']]
        self.assertIn('Marram · Rate changed to Box rest', texts)
        self.assertFalse(any('Moved from Top Barn to Top Barn' in text for text in texts))

    def test_an_owner_change_in_place_says_nothing(self):
        horse = self.horse_in(self.barn, self.box)
        PlacementService.move_horse(
            horse, new_location=self.barn, move_date=self.today - timedelta(days=2),
            new_owner=self.other_owner)
        texts = [event.text for day in activity.recent(self.admin) for event in day['events']]
        self.assertFalse(any('Moved from' in text or 'Rate changed' in text for text in texts))

    def test_a_real_move_still_reads_as_one(self):
        horse = self.horse_in(self.field, self.grass)
        PlacementService.move_horse(
            horse, new_location=self.barn, move_date=self.today - timedelta(days=2),
            new_rate_type=self.box)
        texts = [event.text for day in activity.recent(self.admin) for event in day['events']]
        self.assertIn('Marram · Moved from Back Paddock to Top Barn', texts)

    def test_stable_changes_are_in_the_feed(self):
        horse = self.horse_in(self.barn, self.box)
        StableService.assign(horse, self.barn.stables.get(label='2'), self.today - timedelta(days=3))
        StableService.assign(horse, self.barn.stables.get(label='5'), self.today - timedelta(days=1))
        whats = [event.what for day in activity.recent(self.admin) for event in day['events']]
        self.assertIn('Given a stable in Top Barn', whats)
        self.assertIn('Moved to stable 5 in Top Barn', whats)


class FarrierRowsTests(RateTestCase):
    def setUp(self):
        from billing.models import ServiceProvider
        from health import farrier
        self.farrier = farrier
        self.provider = ServiceProvider.objects.create(
            name='Pat Smith', provider_type=ServiceProvider.ProviderType.FARRIER)
        for name, label in (('Zephyr', 1), ('Alder', 4), ('Marram', 2)):
            StableService.assign(
                self.horse_in(self.barn, self.box, name), self.barn.stables.get(label=str(label)))
        self.horse_in(self.barn, self.box, 'Brook')

    def rows(self, **kwargs):
        due, others = self.farrier.horse_rows(self.provider, **kwargs)
        return due + others

    def test_each_row_carries_its_stable(self):
        labels = {horse.name: horse.stable_label for horse in self.rows()}
        self.assertEqual(labels, {'Zephyr': '1', 'Alder': '4', 'Marram': '2', 'Brook': None})

    def test_one_barn_is_listed_in_the_order_you_walk_it(self):
        names = [horse.name for horse in self.rows(location_id=self.barn.pk)]
        self.assertEqual(names, ['Zephyr', 'Marram', 'Alder', 'Brook'])

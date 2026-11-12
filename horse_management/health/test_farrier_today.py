"""Farrier page, step 3: Today's visit (docs/FARRIER_PAGE_PLAN.md, section 5).

The horse list (due first, search, location filter) and the price panel
that records one line per horse on the farrier's open visit. No charge
is made until Finish visit (step 4).
"""

import importlib
from datetime import timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, ServiceItem, ServiceProvider
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType, Role
from core.roles_testutils import assign_role
from health.models import FarrierNote, FarrierVisit, ProviderVisit

User = get_user_model()
seed_migration = importlib.import_module('core.migrations.0053_seed_farrier_role')


def make_farrier(username='farrier', name='Sam Shoe'):
    seed_migration.seed_farrier_role(django_apps, None)
    user = User.objects.create_user(username=username, password='pw')
    assign_role(user, Role.objects.get(name='Farrier'))
    provider = ServiceProvider.objects.create(name=name, provider_type='farrier', user=user)
    return user, provider


class TodayBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.user, self.provider = make_farrier()
        self.full_set = ServiceItem.objects.create(
            name='Full set', category='farrier', price=Decimal('95.00'),
            farrier_work='full_set', provider=self.provider, sort_order=2,
        )
        self.trim = ServiceItem.objects.create(
            name='Trim', category='farrier', price=Decimal('35.00'),
            farrier_work='trim', provider=self.provider, sort_order=1,
        )
        self.top = Location.objects.create(name='Top Field', site='Main')
        self.stable = Location.objects.create(name='Stable 4', site='Main')
        rate = RateType.objects.create(name='Grass', daily_rate=5)
        owner = Owner.objects.create(name='Jo Bloggs')
        self.bramble = self._horse('Bramble', self.top, owner, rate)
        self.juno = self._horse('Juno', self.stable, owner, rate)
        self.pickle = self._horse('Pickle', self.top, owner, rate)
        # Bramble: overdue (last full set 8 weeks ago, due after 6).
        FarrierVisit.objects.create(horse=self.bramble, date=self.today - timedelta(weeks=8), work_done='full_set')
        # Juno: due in 5 days.
        FarrierVisit.objects.create(
            horse=self.juno, date=self.today - timedelta(weeks=5),
            next_due_date=self.today + timedelta(days=5), work_done='trim',
        )
        # Pickle: done last week, not due.
        FarrierVisit.objects.create(horse=self.pickle, date=self.today - timedelta(weeks=1), work_done='trim')
        self.client.force_login(self.user)

    def _horse(self, name, location, owner, rate):
        horse = Horse.objects.create(name=name)
        OwnershipShare.objects.create(horse=horse, owner=owner, share_percentage=100, is_primary_contact=True)
        Placement.objects.create(
            horse=horse, owner=owner, location=location, rate_type=rate,
            start_date=self.today - timedelta(days=60),
        )
        return horse

    def add(self, horse, htmx=True, **data):
        headers = {'HTTP_HX_REQUEST': 'true'} if htmx else {}
        return self.client.post(reverse('farrier:horse_add', args=[horse.pk]), data, **headers)

    def line(self, horse):
        visit = ProviderVisit.objects.get(provider=self.provider, status='open')
        return visit.farrier_visits.get(horse=horse)


class HorseListTests(TodayBase):
    """Today's visit shows only the yard's list and added horses; search finds the rest."""

    def names(self, response, key):
        return [h.name for h in response.context[key]]

    def test_with_no_list_no_horses_are_shown_until_a_search(self):
        response = self.client.get(reverse('farrier:today'))
        self.assertEqual(self.names(response, 'list_horses'), [])
        self.assertEqual(self.names(response, 'results'), [])
        self.assertContains(response, 'no horses on your list today')
        self.assertContains(response, 'Find another horse')
        for name in ('Bramble', 'Juno', 'Pickle'):
            self.assertNotContains(response, name)

    def test_no_location_filter_or_due_list(self):
        response = self.client.get(reverse('farrier:today'))
        self.assertNotContains(response, 'All places')
        self.assertNotContains(response, 'All other horses')
        self.assertNotContains(response, 'Due now')

    def test_departed_horses_are_not_found(self):
        self.pickle.is_active = False
        self.pickle.save()
        response = self.client.get(reverse('farrier:today'), {'q': 'pick'})
        self.assertEqual(self.names(response, 'results'), [])

    def test_search_by_name_finds_any_horse(self):
        response = self.client.get(reverse('farrier:today'), {'q': 'jun'})
        self.assertEqual(self.names(response, 'results'), ['Juno'])
        response = self.client.get(reverse('farrier:today'), {'q': 'PICK'})
        self.assertEqual(self.names(response, 'results'), ['Pickle'])

    def test_search_with_no_hit_says_so(self):
        response = self.client.get(reverse('farrier:today'), {'q': 'zzz'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'No other horse called')

    def test_htmx_search_returns_only_the_results(self):
        response = self.client.get(reverse('farrier:today'), {'q': 'bram'}, HTTP_HX_REQUEST='true')
        self.assertTemplateUsed(response, 'farrier/_search_results.html')
        self.assertTemplateNotUsed(response, 'farrier/today.html')
        self.assertContains(response, 'Bramble')
        self.assertNotContains(response, 'Juno')

    def test_open_panel_shows_only_the_farriers_prices(self):
        ServiceItem.objects.create(name='Yard shoes', category='farrier', price=Decimal('45.00'))
        response = self.client.get(reverse('farrier:horse_panel', args=[self.bramble.pk]), HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Full set')
        self.assertContains(response, 'Trim')
        self.assertNotContains(response, 'Yard shoes')

    def test_panel_has_work_cost_and_notes_in_one_form(self):
        response = self.client.get(reverse('farrier:horse_panel', args=[self.bramble.pk]), HTTP_HX_REQUEST='true')
        self.assertContains(response, '1. Work done')
        self.assertContains(response, '2. Cost')
        self.assertContains(response, '3. Notes')
        self.assertContains(response, f'value="item:{self.full_set.pk}"')
        # Work types with no price of their own are still offered.
        self.assertContains(response, 'value="work:remedial"')
        self.assertNotContains(response, 'value="work:full_set"')
        self.assertNotContains(response, 'Other or remedial work')
        self.assertNotContains(response, 'Note only')

    def test_panel_without_script_opens_on_the_full_page(self):
        response = self.client.get(reverse('farrier:horse_panel', args=[self.bramble.pk]))
        self.assertRedirects(response, f"{reverse('farrier:today')}?open={self.bramble.pk}#horse-{self.bramble.pk}",
                             fetch_redirect_response=False)
        response = self.client.get(reverse('farrier:today'), {'open': self.bramble.pk})
        self.assertContains(response, reverse('farrier:horse_add', args=[self.bramble.pk]))

    def test_owner_names_never_shown(self):
        response = self.client.get(reverse('farrier:today'))
        self.assertNotContains(response, 'Jo Bloggs')
        response = self.client.get(reverse('farrier:horse_panel', args=[self.bramble.pk]), HTTP_HX_REQUEST='true')
        self.assertNotContains(response, 'Jo Bloggs')


class RecordLineTests(TodayBase):
    def test_tap_a_price_opens_a_visit_and_records_a_line(self):
        response = self.add(self.bramble, item=self.full_set.pk)
        self.assertEqual(response.status_code, 200)
        visit = ProviderVisit.objects.get(provider=self.provider)
        self.assertTrue(visit.is_open)
        self.assertEqual(visit.created_by, self.user)
        line = self.line(self.bramble)
        self.assertEqual(line.work_done, 'full_set')
        self.assertEqual(line.cost, Decimal('95.00'))
        self.assertEqual(line.service_provider, self.provider)
        self.assertEqual(line.date, visit.date)
        # The work type already says "Full set"; no repeat in the notes.
        self.assertEqual(line.notes, '')

    def test_custom_price_without_a_work_type_keeps_its_name(self):
        rasp = ServiceItem.objects.create(name='Rasp and balance', category='farrier',
                                          price=Decimal('25.00'), provider=self.provider)
        self.add(self.bramble, item=rasp.pk, note='Left hind')
        line = self.line(self.bramble)
        self.assertEqual(line.work_done, 'remedial')
        self.assertEqual(line.notes, 'Rasp and balance · Left hind')

    def test_no_charge_until_finish(self):
        self.add(self.bramble, item=self.full_set.pk)
        self.assertFalse(ExtraCharge.objects.exists())
        self.assertIsNone(self.line(self.bramble).extra_charge)

    def test_response_shows_the_tick_and_updates_the_counter(self):
        response = self.add(self.bramble, item=self.full_set.pk)
        self.assertContains(response, '&#10003; Full Set')
        self.assertContains(response, 'id="visit-counter" hx-swap-oob="true"')
        self.assertContains(response, '1 horse · £95.00')

    def test_second_tap_replaces_the_line(self):
        self.add(self.bramble, item=self.full_set.pk)
        self.add(self.bramble, item=self.trim.pk)
        visit = ProviderVisit.objects.get(provider=self.provider)
        self.assertEqual(visit.farrier_visits.count(), 1)
        self.assertEqual(self.line(self.bramble).work_done, 'trim')
        self.assertEqual(visit.total, Decimal('35.00'))

    def test_double_tap_makes_one_visit_and_one_line(self):
        self.add(self.bramble, item=self.full_set.pk)
        self.add(self.bramble, item=self.full_set.pk)
        self.assertEqual(ProviderVisit.objects.filter(provider=self.provider).count(), 1)
        self.assertEqual(FarrierVisit.objects.filter(provider_visit__isnull=False).count(), 1)

    def test_several_horses_share_one_visit(self):
        self.add(self.bramble, item=self.full_set.pk)
        self.add(self.juno, item=self.trim.pk)
        visit = ProviderVisit.objects.get(provider=self.provider)
        self.assertEqual(visit.farrier_visits.count(), 2)
        self.assertEqual(visit.total, Decimal('130.00'))

    def test_price_override_for_one_horse(self):
        self.add(self.bramble, item=self.full_set.pk, price='110')
        self.assertEqual(self.line(self.bramble).cost, Decimal('110.00'))
        self.full_set.refresh_from_db()
        self.assertEqual(self.full_set.price, Decimal('95.00'))

    def test_note_is_kept(self):
        self.add(self.bramble, item=self.full_set.pk, note='Loose clench LF')
        self.assertIn('Loose clench LF', self.line(self.bramble).notes)

    def test_other_work_needs_work_and_price(self):
        response = self.add(self.bramble, item='', work_done='remedial', price='')
        self.assertContains(response, 'Enter a price')
        response = self.add(self.bramble, item='', work_done='', price='60')
        self.assertContains(response, 'Choose the work done')
        self.assertFalse(FarrierVisit.objects.filter(provider_visit__isnull=False).exists())
        self.add(self.bramble, item='', work_done='remedial', price='60')
        line = self.line(self.bramble)
        self.assertEqual((line.work_done, line.cost), ('remedial', Decimal('60.00')))

    def test_bad_prices_are_refused(self):
        for price in ('-5', 'abc', '100000'):
            with self.subTest(price=price):
                response = self.add(self.bramble, item=self.full_set.pk, price=price)
                self.assertContains(response, 'role="alert"')
        self.assertFalse(FarrierVisit.objects.filter(provider_visit__isnull=False).exists())

    def test_another_farriers_price_is_not_found(self):
        _, other = make_farrier(username='rival', name='Rival Forge')
        theirs = ServiceItem.objects.create(name='Cheap', category='farrier', price=Decimal('1.00'), provider=other)
        self.assertEqual(self.add(self.bramble, item=theirs.pk).status_code, 404)

    def test_the_yards_price_is_not_found(self):
        yard = ServiceItem.objects.create(name='Yard shoes', category='farrier', price=Decimal('45.00'))
        self.assertEqual(self.add(self.bramble, item=yard.pk).status_code, 404)

    def test_a_hidden_price_is_refused(self):
        self.full_set.is_active = False
        self.full_set.save()
        response = self.add(self.bramble, item=self.full_set.pk)
        self.assertContains(response, 'not on your list')

    def test_departed_horse_is_not_found(self):
        self.bramble.is_active = False
        self.bramble.save()
        self.assertEqual(self.add(self.bramble, item=self.full_set.pk).status_code, 404)

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse('farrier:horse_add', args=[self.bramble.pk]))
        self.assertEqual(response.status_code, 405)

    def test_without_script_redirects_back_to_the_list(self):
        response = self.add(self.bramble, htmx=False, item=self.full_set.pk)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('farrier:today'), response['Location'])

    def test_an_added_horse_joins_todays_horses_with_a_tick(self):
        self.add(self.pickle, item=self.full_set.pk)
        response = self.client.get(reverse('farrier:today'))
        horses = response.context['list_horses']
        self.assertEqual([h.name for h in horses], ['Pickle'])
        self.assertIsNotNone(horses[0].line)
        # It is not found again by a search: it is already on the screen.
        response = self.client.get(reverse('farrier:today'), {'q': 'pick'})
        self.assertEqual(response.context['results'], [])

    def test_a_finished_visit_is_not_changed(self):
        self.add(self.bramble, item=self.full_set.pk)
        visit = ProviderVisit.objects.get(provider=self.provider)
        visit.status = ProviderVisit.Status.FINISHED
        visit.finished_at = timezone.now()
        visit.save()
        self.add(self.bramble, item=self.trim.pk)
        self.assertEqual(visit.farrier_visits.get().work_done, 'full_set')
        new_visit = ProviderVisit.objects.get(provider=self.provider, status='open')
        self.assertEqual(new_visit.farrier_visits.get().work_done, 'trim')

    def test_two_farriers_each_have_their_own_visit(self):
        rival_user, rival = make_farrier(username='rival', name='Rival Forge')
        rival_trim = ServiceItem.objects.create(name='Trim', category='farrier', price=Decimal('30.00'),
                                                farrier_work='trim', provider=rival)
        self.add(self.bramble, item=self.full_set.pk)
        self.client.force_login(rival_user)
        self.add(self.bramble, item=rival_trim.pk)
        self.assertEqual(ProviderVisit.objects.filter(status='open').count(), 2)
        response = self.client.get(reverse('farrier:today'))
        bramble = response.context['list_horses'][0]
        self.assertEqual(bramble.line.cost, Decimal('30.00'))


class OneFormTests(TodayBase):
    """Work done, cost and notes are saved from one form."""

    def test_a_price_tile_uses_its_price_when_cost_is_empty(self):
        self.add(self.bramble, choice=f'item:{self.full_set.pk}', price='')
        line = self.line(self.bramble)
        self.assertEqual((line.work_done, line.cost), ('full_set', Decimal('95.00')))

    def test_a_price_tile_with_a_typed_cost(self):
        self.add(self.bramble, choice=f'item:{self.full_set.pk}', price='110', note='Pads on fronts')
        line = self.line(self.bramble)
        self.assertEqual((line.cost, line.notes), (Decimal('110.00'), 'Pads on fronts'))

    def test_a_work_tile_needs_a_cost(self):
        response = self.add(self.bramble, choice='work:remedial', price='')
        self.assertContains(response, 'Enter a price')
        self.add(self.bramble, choice='work:remedial', price='60')
        line = self.line(self.bramble)
        self.assertEqual((line.work_done, line.cost), ('remedial', Decimal('60.00')))

    def test_a_cost_with_no_work_is_refused(self):
        response = self.add(self.bramble, price='60')
        self.assertContains(response, 'Choose the work done')
        self.assertFalse(ProviderVisit.objects.exists())

    def test_no_work_and_no_cost_saves_a_note_only(self):
        response = self.add(self.bramble, note='Heat in near fore')
        self.assertContains(response, 'Note saved. No charge.')
        self.assertContains(response, 'Heat in near fore')
        note = FarrierNote.objects.get()
        self.assertEqual((note.horse, note.provider), (self.bramble, self.provider))
        self.assertFalse(FarrierVisit.objects.filter(horse=self.bramble, date=self.today).exists())

    def test_an_empty_form_is_refused(self):
        response = self.add(self.bramble)
        self.assertContains(response, 'Choose the work done, or write a note')
        self.assertFalse(FarrierNote.objects.exists())

    def test_reopening_a_saved_horse_shows_its_work_cost_and_note(self):
        self.add(self.bramble, choice=f'item:{self.trim.pk}', price='40', note='Short toes')
        response = self.client.get(reverse('farrier:horse_panel', args=[self.bramble.pk]), HTTP_HX_REQUEST='true')
        self.assertContains(response, f'value="item:{self.trim.pk}" class="sr-only" checked')
        self.assertContains(response, 'value="40.00"')
        self.assertContains(response, 'Short toes</textarea>')

    def test_a_custom_price_name_is_not_repeated_on_a_second_save(self):
        custom = ServiceItem.objects.create(name='Stud holes', category='farrier', price=Decimal('20.00'),
                                            provider=self.provider)
        self.add(self.bramble, choice=f'item:{custom.pk}')
        self.assertEqual(self.line(self.bramble).notes, 'Stud holes')
        self.add(self.bramble, choice=f'item:{custom.pk}', note=self.line(self.bramble).notes)
        self.assertEqual(self.line(self.bramble).notes, 'Stud holes')


class RemoveLineTests(TodayBase):
    def test_remove_takes_the_horse_off_the_visit(self):
        self.add(self.bramble, item=self.full_set.pk)
        response = self.client.post(reverse('farrier:horse_remove', args=[self.bramble.pk]), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        visit = ProviderVisit.objects.get(provider=self.provider)
        self.assertFalse(visit.farrier_visits.exists())
        self.assertTrue(visit.is_open)
        self.assertContains(response, '0 horses')

    def test_remove_with_no_line_does_nothing(self):
        response = self.client.post(reverse('farrier:horse_remove', args=[self.bramble.pk]), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)

    def test_a_billed_line_cannot_be_removed(self):
        self.add(self.bramble, item=self.full_set.pk)
        line = self.line(self.bramble)
        line.extra_charge = ExtraCharge.objects.create(
            horse=self.bramble, owner=Owner.objects.get(name='Jo Bloggs'),
            date=self.today, description='Farrier', amount=Decimal('95.00'),
        )
        line.save()
        response = self.client.post(reverse('farrier:horse_remove', args=[self.bramble.pk]), HTTP_HX_REQUEST='true')
        self.assertContains(response, 'already billed')
        self.assertTrue(FarrierVisit.objects.filter(pk=line.pk).exists())

    def test_staff_records_are_never_removed(self):
        staff_record = FarrierVisit.objects.get(horse=self.pickle)
        self.client.post(reverse('farrier:horse_remove', args=[self.pickle.pk]), HTTP_HX_REQUEST='true')
        self.assertTrue(FarrierVisit.objects.filter(pk=staff_record.pk).exists())

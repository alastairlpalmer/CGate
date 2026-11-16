"""Phase 4: what a visit needs (docs/C_VET_PLAN.md, section 7c).

The price list, the items done and their prices, who pays, what is due,
and Finish visit.
"""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.utils import timezone

from core.models import Horse, Owner, OwnershipShare
from core.roles_testutils import make_admin
from health.models import Vaccination, VaccinationType

from . import access
from .models import ClinicalNote, NoteLine, PriceItem, Visit
from .tests import NOTES, YARD, YardCase, note_data
from .tests_ownership import TripwireTests
from .testutils import make_practice, make_vet

PRICES = '/c-vet/api/prices/'


def finish_url(visit_id):
    return f'/c-vet/api/visits/{visit_id}/finish/'


def price_data(**extra):
    data = {'id': str(uuid.uuid4()), 'name': 'Flu/Tet booster', 'price': '65.00', 'vat_rate': '20'}
    data.update(extra)
    return data


def line(**extra):
    data = {
        'id': str(uuid.uuid4()), 'description': 'Flu/Tet booster',
        'quantity': '1', 'unit_price': '65.00', 'vat_rate': '20.00',
    }
    data.update(extra)
    return data


class PriceListTests(YardCase):
    def test_the_vet_adds_a_price_and_the_phone_downloads_it(self):
        response = self.client.post(PRICES, price_data())
        self.assertEqual(response.status_code, 201)
        item = PriceItem.objects.get()
        self.assertEqual(item.practice, self.practice)
        self.assertEqual(item.price_inc_vat, Decimal('65.00'))
        self.assertEqual(item.vat_rate, Decimal('20.00'))
        self.assertEqual(self.client.get(YARD).json()['prices'], [{
            'id': str(item.client_id), 'name': 'Flu/Tet booster',
            'price': '65.00', 'vat_rate': '20.00', 'is_active': True,
        }])

    def test_the_same_id_changes_the_price_and_makes_no_second_one(self):
        data = price_data()
        self.client.post(PRICES, data)
        response = self.client.post(PRICES, dict(data, price='68.50', is_active='false'))
        self.assertEqual(response.status_code, 200)
        item = PriceItem.objects.get()
        self.assertEqual(item.price_inc_vat, Decimal('68.50'))
        self.assertFalse(item.is_active)

    def test_bad_prices_are_refused(self):
        for change, error in (
            ({'id': 'x'}, 'id_invalid'),
            ({'name': ' '}, 'name_invalid'),
            ({'price': 'sixty'}, 'price_invalid'),
            ({'price': '-1'}, 'price_invalid'),
            ({'price': '1.005'}, 'price_invalid'),
            ({'price': 'NaN'}, 'price_invalid'),
            ({'vat_rate': '120'}, 'vat_rate_invalid'),
        ):
            response = self.client.post(PRICES, price_data(**change))
            self.assertEqual(response.status_code, 400, change)
            self.assertEqual(response.json(), {'error': error})
        self.assertFalse(PriceItem.objects.exists())

    def test_one_practice_cannot_change_or_read_anothers_prices(self):
        data = price_data()
        self.client.post(PRICES, data)
        self.client.force_login(make_vet('other', practice=make_practice('Other Vets')))
        response = self.client.post(PRICES, dict(data, price='1.00'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(PriceItem.objects.get().price_inc_vat, Decimal('65.00'))
        self.assertEqual(self.client.get(YARD).json()['prices'], [])

    def test_the_yard_cannot_read_or_set_the_price_list(self):
        self.client.post(PRICES, price_data())
        self.client.force_login(make_admin())
        self.assertEqual(self.client.post(PRICES, price_data()).status_code, 403)
        self.assertEqual(PriceItem.objects.count(), 1)

    def test_prices_and_lines_are_on_the_tripwire_list(self):
        # Not in the Django admin, and no yard code may name them.
        self.assertIn(PriceItem, TripwireTests.CLINICAL)
        self.assertIn(NoteLine, TripwireTests.CLINICAL)


class NoteLineTests(YardCase):
    def _post(self, lines, **extra):
        return self.client.post(NOTES, note_data(
            horse_id=str(self.horse.pk), lines=json.dumps(lines), **extra,
        ))

    def test_items_are_stored_with_their_own_price(self):
        self.client.post(PRICES, price_data())
        price = PriceItem.objects.get()
        response = self._post([
            line(price_id=str(price.client_id)),
            line(description='Sedation', quantity='2', unit_price='27.50'),
        ])
        self.assertEqual(response.status_code, 201)
        booster, sedation = NoteLine.objects.order_by('pk')
        self.assertEqual(booster.price_item, price)
        self.assertEqual(booster.practice, self.practice)
        self.assertEqual(booster.total_inc_vat, Decimal('65.00'))
        self.assertIsNone(sedation.price_item)
        self.assertEqual(sedation.total_inc_vat, Decimal('55.00'))

    def test_a_later_change_of_the_price_list_does_not_change_work_done(self):
        data = price_data()
        self.client.post(PRICES, data)
        self._post([line(price_id=data['id'])])
        self.client.post(PRICES, dict(data, price='80.00', name='Booster'))
        done = NoteLine.objects.get()
        self.assertEqual(done.unit_price_inc_vat, Decimal('65.00'))
        self.assertEqual(done.description, 'Flu/Tet booster')

    def test_a_note_can_be_items_with_no_text(self):
        # "Flu/Tet booster" added to ten horses at once has no note text.
        self.assertEqual(self._post([line()], note='').status_code, 201)

    def test_the_same_note_sent_twice_adds_no_second_line(self):
        data = note_data(lines=json.dumps([line()]))
        self.client.post(NOTES, data)
        self.assertEqual(self.client.post(NOTES, data).json()['status'], 'duplicate')
        self.assertEqual(NoteLine.objects.count(), 1)

    def test_bad_lines_are_refused_and_nothing_is_stored(self):
        for lines in (
            'not json', '{"a": 1}', '[1]',
            json.dumps([line(id='x')]),
            json.dumps([line(description='')]),
            json.dumps([line(quantity='0')]),
            json.dumps([line(quantity='-1')]),
            json.dumps([line(unit_price='1e9')]),
            json.dumps([line(unit_price='abc')]),
            json.dumps([line() for _ in range(51)]),
        ):
            response = self.client.post(NOTES, note_data(lines=lines))
            self.assertEqual(response.status_code, 400, lines[:40])
        self.assertFalse(ClinicalNote.objects.exists())
        self.assertFalse(NoteLine.objects.exists())

    def test_a_line_id_in_use_stores_nothing(self):
        one = line()
        self._post([one])
        response = self._post([one])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(ClinicalNote.objects.count(), 1)

    def test_another_practices_price_id_is_not_linked(self):
        other = make_practice('Other Vets')
        theirs = PriceItem.objects.create(practice=other, name='Theirs', price_inc_vat=1)
        self._post([line(price_id=str(theirs.client_id))])
        self.assertIsNone(NoteLine.objects.get().price_item)

    def test_the_list_gives_lines_and_the_follow_up_date(self):
        recheck = (timezone.localdate() + timedelta(days=14)).isoformat()
        self._post([line(quantity='2')], follow_up_date=recheck)
        note = self.client.get(f'{NOTES}?horse={self.horse.pk}').json()['notes'][0]
        self.assertEqual(note['follow_up_date'], recheck)
        self.assertEqual(note['lines'][0]['quantity'], '2')
        self.assertEqual(note['lines'][0]['total'], '130.00')

    def test_a_bad_follow_up_date_is_refused(self):
        response = self.client.post(NOTES, note_data(follow_up_date='in two weeks'))
        self.assertEqual(response.json(), {'error': 'follow_up_date_invalid'})

    def test_deleting_the_note_deletes_its_lines(self):
        self._post([line()])
        ClinicalNote.objects.get().delete()
        self.assertFalse(NoteLine.objects.exists())


class PayerTests(YardCase):
    def _share(self, name, percent, primary=False):
        owner = Owner.objects.create(name=name)
        OwnershipShare.objects.create(
            horse=self.horse, owner=owner, share_percentage=percent, is_primary_contact=primary,
        )
        return owner

    def test_the_download_gives_the_split_between_co_owners(self):
        self._share('Ali Khan', 40)
        self._share('Sarah Thompson', 60, primary=True)
        horse = self.client.get(YARD).json()['horses'][0]
        self.assertEqual(horse['payers'], [
            {'name': 'Sarah Thompson', 'share': '60.00'},
            {'name': 'Ali Khan', 'share': '40.00'},
        ])
        self.assertNotIn('owner_id', json.dumps(horse))

    def test_a_horse_with_no_owner_has_no_payer(self):
        loose = Horse.objects.create(name='No Owner Ned')
        horses = {h['name']: h for h in self.client.get(YARD).json()['horses']}
        self.assertEqual(horses[loose.name]['payers'], [])

    def test_the_note_keeps_who_paid_when_it_arrived(self):
        # The owner changed after the phone downloaded the list. The
        # server uses the yard's record at the time the note arrives.
        ali = self._share('Ali Khan', 100)
        self.client.post(NOTES, note_data(horse_id=str(self.horse.pk)))
        note = ClinicalNote.objects.get()
        self.assertEqual(note.payers, [{'owner_id': ali.pk, 'name': 'Ali Khan', 'share': '100.00'}])
        # A later change at the yard does not rewrite the note.
        OwnershipShare.objects.all().delete()
        self.assertEqual(ClinicalNote.objects.get().payers[0]['name'], 'Ali Khan')

    def test_a_note_for_a_horse_the_practice_cannot_see_has_no_payer(self):
        self.client.post(NOTES, note_data(horse_id='999999'))
        self.assertEqual(ClinicalNote.objects.get().payers, [])


class DueTests(YardCase):
    def _due(self):
        return self.client.get(YARD).json()['horses'][0]['due']

    def test_a_vaccination_due_date_comes_from_the_yards_records(self):
        flu = VaccinationType.objects.create(name='Flu/Tet')
        today = timezone.localdate()
        Vaccination.objects.create(
            horse=self.horse, vaccination_type=flu, date_given=today - timedelta(days=700),
            next_due_date=today - timedelta(days=335),
        )
        # The newest dose of the type is the one that counts.
        Vaccination.objects.create(
            horse=self.horse, vaccination_type=flu, date_given=today - timedelta(days=350),
            next_due_date=today + timedelta(days=15),
        )
        self.assertEqual(self._due(), [{
            'kind': 'vaccination', 'label': 'Flu/Tet',
            'date': (today + timedelta(days=15)).isoformat(),
        }])

    def test_a_follow_up_comes_from_the_practices_newest_note(self):
        recheck = (timezone.localdate() + timedelta(days=14)).isoformat()
        self.client.post(NOTES, note_data(horse_id=str(self.horse.pk), follow_up_date=recheck))
        self.assertEqual(self._due(), [{'kind': 'follow_up', 'label': 'Recheck', 'date': recheck}])

    def test_a_newer_note_with_no_date_clears_the_follow_up(self):
        earlier = (timezone.now() - timedelta(days=3)).isoformat()
        self.client.post(NOTES, note_data(
            horse_id=str(self.horse.pk), device_created_at=earlier,
            follow_up_date=timezone.localdate().isoformat(),
        ))
        self.client.post(NOTES, note_data(horse_id=str(self.horse.pk), note='Sound. No recheck.'))
        self.assertEqual(self._due(), [])

    def test_another_practices_follow_up_is_not_shown(self):
        other = make_vet('other', practice=make_practice('Other Vets'))
        self.client.force_login(other)
        self.client.post(NOTES, note_data(
            horse_id=str(self.horse.pk), follow_up_date=timezone.localdate().isoformat(),
        ))
        self.client.force_login(self.vet)
        self.assertEqual(self._due(), [])

    def test_test_notes_leave_the_history_when_test_mode_ends(self):
        self.client.post(NOTES, note_data(
            horse_id=str(self.horse.pk), follow_up_date=timezone.localdate().isoformat(),
        ))
        self.assertEqual(len(self._due()), 1)
        with override_settings(CVET_TEST_MODE=False):
            self.assertEqual(self._due(), [])
            self.assertEqual(self.client.get(NOTES).json()['notes'], [])
            self.assertFalse(access.current_notes_for(self.practice).exists())
        self.assertEqual(access.notes_for(self.practice).count(), 1)

    def test_the_download_takes_the_same_number_of_queries_for_1_horse_or_20(self):
        def count():
            from django.db import connection
            from django.test.utils import CaptureQueriesContext
            with CaptureQueriesContext(connection) as queries:
                self.client.get(YARD)
            return len(queries)
        one = count()
        for number in range(19):
            Horse.objects.create(name=f'Horse {number}')
        self.assertEqual(count(), one)


class FinishVisitTests(YardCase):
    def _visit_with_notes(self):
        visit_id = str(uuid.uuid4())
        second = Horse.objects.create(name='Clover')
        self.client.post(NOTES, note_data(
            horse_id=str(self.horse.pk), visit_id=visit_id, lines=json.dumps([line()]),
        ))
        self.client.post(NOTES, note_data(
            horse_id=str(second.pk), horse_name='Clover', visit_id=visit_id,
            lines=json.dumps([line(description='Sedation', quantity='2', unit_price='27.50')]),
        ))
        return visit_id

    def test_finish_closes_the_visit_and_gives_the_totals(self):
        visit_id = self._visit_with_notes()
        finished = (timezone.now() - timedelta(minutes=30)).isoformat()
        response = self.client.post(finish_url(visit_id), {'finished_at': finished})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'finished')
        self.assertEqual((data['notes'], data['horses'], data['total']), (2, 2, '120.00'))
        visit = Visit.objects.get()
        self.assertEqual(visit.status, 'finished')
        self.assertEqual(visit.finished_at.isoformat(), finished)

    def test_finish_twice_keeps_the_first_time(self):
        visit_id = self._visit_with_notes()
        first = self.client.post(finish_url(visit_id)).json()['finished_at']
        second = self.client.post(finish_url(visit_id)).json()['finished_at']
        self.assertEqual(first, second)

    def test_a_note_that_arrives_after_finish_still_joins_the_visit(self):
        visit_id = self._visit_with_notes()
        self.client.post(finish_url(visit_id))
        self.assertEqual(self.client.post(NOTES, note_data(visit_id=visit_id)).status_code, 201)
        self.assertEqual(Visit.objects.get().notes.count(), 3)

    def test_nobody_else_can_finish_it(self):
        visit_id = self._visit_with_notes()
        self.client.force_login(make_vet('other', practice=make_practice('Other Vets')))
        self.assertEqual(self.client.post(finish_url(visit_id)).status_code, 404)
        self.client.force_login(make_admin())
        self.assertEqual(self.client.post(finish_url(visit_id)).status_code, 403)
        self.assertEqual(Visit.objects.get().status, 'open')

    def test_an_unknown_visit_is_not_found(self):
        self.assertEqual(self.client.post(finish_url(uuid.uuid4())).status_code, 404)

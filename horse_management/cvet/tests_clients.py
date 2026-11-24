"""The practice's clients (docs/C_VET_PLAN.md, section 7g).

A vet has many clients: livery yards, studs, racing yards, owners. The
C-gate yard is one of them. Every other client holds horses of its own,
which C-gate never sees, and a visit for it writes nothing at the yard.
The vet reads a visit back after it is sent: its notes and recordings.
"""

import json
import uuid

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from billing.models import ExtraCharge, YardCost
from health.models import VetVisit

from .models import Bill, ClinicalNote, Client, ClientHorse, Visit
from .tests import NOTES, YARD, YardCase, note_data
from .tests_billing import BillingCase
from .tests_visit import PRICES, finish_url, line, price_data
from .testutils import make_practice, make_vet

CLIENTS = '/c-vet/api/clients/'
HORSES = '/c-vet/api/horses/'
VISITS = '/c-vet/api/visits/'


def client_data(**extra):
    data = {'id': str(uuid.uuid4()), 'name': 'Marsh Stud', 'kind': 'stud', 'contact_name': 'Sam Marsh'}
    data.update(extra)
    return data


def horse_data(client_id, **extra):
    data = {'id': str(uuid.uuid4()), 'client_id': client_id, 'name': 'Juniper', 'owner_name': 'Sam Marsh'}
    data.update(extra)
    return data


class ClientListTests(YardCase):
    def test_the_vet_adds_a_client_and_its_horse_and_the_phone_downloads_them(self):
        client = client_data()
        self.assertEqual(self.client.post(CLIENTS, client).status_code, 201)
        horse = horse_data(client['id'], microchip='985100099999999')
        self.assertEqual(self.client.post(HORSES, horse).status_code, 201)

        data = self.client.get(YARD).json()
        self.assertEqual(
            [(item['name'], item['kind'], item['is_yard']) for item in data['clients']],
            [('Marsh Stud', 'stud', False), ('Palmer Farm', 'yard', True)],
        )
        own = [item for item in data['horses'] if item.get('own')]
        self.assertEqual(own, [{
            'id': horse['id'], 'client_id': client['id'], 'name': 'Juniper',
            'microchip': '985100099999999', 'passport': '', 'about': '', 'location': '', 'owner': 'Sam Marsh',
            'payers': [], 'due': [], 'own': True, 'is_active': True,
        }])
        # The yard's horse stays with the yard client.
        yard_client = next(item for item in data['clients'] if item['is_yard'])
        self.assertEqual(
            [item['client_id'] for item in data['horses'] if not item.get('own')], [yard_client['id']],
        )

    def test_a_horse_of_the_practices_own_client_has_a_description(self):
        client = client_data()
        self.client.post(CLIENTS, client)
        horse = horse_data(client['id'], about='Bay mare, 12yo')
        self.assertEqual(self.client.post(HORSES, horse).json()['about'], 'Bay mare, 12yo')
        own = next(item for item in self.client.get(YARD).json()['horses'] if item.get('own'))
        self.assertEqual(own['about'], 'Bay mare, 12yo')
        self.assertEqual(self.client.post(HORSES, dict(horse, about='x' * 101)).json(), {'error': 'text_too_long'})

    def test_the_same_id_changes_the_client_and_makes_no_second_one(self):
        client = client_data()
        self.client.post(CLIENTS, client)
        response = self.client.post(CLIENTS, dict(client, name='Marsh Stud Ltd', is_active='false'))
        self.assertEqual(response.status_code, 200)
        saved = Client.objects.get(client_id=client['id'])
        self.assertEqual((saved.name, saved.is_active, saved.practice), ('Marsh Stud Ltd', False, self.practice))
        self.assertEqual(Client.objects.filter(yard_link__isnull=True).count(), 1)

    def test_a_client_needs_a_name_and_a_known_kind(self):
        self.assertEqual(self.client.post(CLIENTS, client_data(name=' ')).json(), {'error': 'name_invalid'})
        self.assertEqual(self.client.post(CLIENTS, client_data(kind='zoo')).json(), {'error': 'kind_invalid'})
        self.assertEqual(self.client.post(CLIENTS, client_data(id='x')).json(), {'error': 'id_invalid'})
        self.assertFalse(Client.objects.exists())

    def test_the_yard_client_is_not_changed_from_the_phone(self):
        yard_client = next(item for item in self.client.get(YARD).json()['clients'] if item['is_yard'])
        response = self.client.post(CLIENTS, client_data(id=yard_client['id'], name='Mine now'))
        self.assertEqual((response.status_code, response.json()), (409, {'error': 'yard_client'}))
        # Its horses are the yard's records: the phone adds none.
        response = self.client.post(HORSES, horse_data(yard_client['id']))
        self.assertEqual((response.status_code, response.json()), (400, {'error': 'client_invalid'}))
        self.assertFalse(ClientHorse.objects.exists())

    def test_another_practice_cannot_read_or_change_them(self):
        client = client_data()
        self.client.post(CLIENTS, client)
        horse = horse_data(client['id'])
        self.client.post(HORSES, horse)
        self.client.force_login(make_vet('other', practice=make_practice('Far Vets', linked=False)))
        data = self.client.get(YARD).json()
        self.assertEqual((data['clients'], data['horses']), ([], []))
        self.assertEqual(self.client.post(CLIENTS, dict(client, name='Taken')).status_code, 409)
        self.assertEqual(self.client.post(HORSES, dict(horse, name='Taken')).status_code, 400)
        self.assertEqual(self.client.post(HORSES, horse_data(client['id'])).json(), {'error': 'client_invalid'})
        self.assertEqual(Client.objects.get(client_id=client['id']).name, 'Marsh Stud')
        self.assertEqual(ClientHorse.objects.get().name, 'Juniper')

    def test_a_price_has_a_category(self):
        self.assertEqual(self.client.post(PRICES, price_data(category='Vaccination')).status_code, 201)
        self.assertEqual(self.client.get(YARD).json()['prices'][0]['category'], 'Vaccination')
        self.assertEqual(self.client.post(PRICES, price_data(category='x' * 51)).json(), {'error': 'category_invalid'})


@override_settings(CVET_TEST_MODE=False)
class OwnClientVisitTests(BillingCase):
    """A visit for a client that is not the C-gate yard."""

    def setUp(self):
        super().setUp()
        self.stud = client_data()
        self.client.post(CLIENTS, self.stud)
        self.juniper = horse_data(self.stud['id'])
        self.client.post(HORSES, self.juniper)

    def own_note(self, visit_id, **extra):
        data = note_data(
            visit_id=visit_id, visit_client_id=self.stud['id'], client_name='Marsh Stud',
            client_horse_id=self.juniper['id'], horse_name='Juniper',
            lines=json.dumps([line(description='Scan', unit_price='60.00')]), note='In foal.',
        )
        data.update(extra)
        response = self.client.post(NOTES, data)
        self.assertEqual(response.status_code, 201, response.content)
        return data

    def test_the_bill_goes_to_the_client_and_nothing_goes_to_the_yard(self):
        visit_id = str(uuid.uuid4())
        self.own_note(visit_id)
        data = self.finish(visit_id)
        bill = Bill.objects.get()
        self.assertEqual((bill.to_name, bill.bill_to, bill.total_inc_vat), ('Marsh Stud', 'yard', 60))
        self.assertEqual(bill.practice_client.client_id, uuid.UUID(self.stud['id']))
        self.assertEqual((data['yard'], data['client_id'], data['total']), ('Marsh Stud', self.stud['id'], '60.00'))
        note = ClinicalNote.objects.get()
        self.assertEqual((note.horse, note.client_horse.name, note.yard_sent_at), (None, 'Juniper', None))
        # The yard's books, health records and inbox hold nothing of it.
        self.assertFalse(YardCost.objects.exists())
        self.assertFalse(VetVisit.objects.exists())
        self.assertFalse(ExtraCharge.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_each_client_gets_its_own_visit_and_its_own_bill(self):
        own_visit, yard_visit = str(uuid.uuid4()), str(uuid.uuid4())
        self.own_note(own_visit)
        self.visit_at_yard = self.client.post(NOTES, note_data(
            horse_id=str(self.horse.pk), visit_id=yard_visit, lines=json.dumps([line()]),
        ))
        self.finish(own_visit)
        self.finish(yard_visit)
        self.assertEqual(
            sorted(Bill.objects.values_list('to_name', 'total_inc_vat')),
            [('Marsh Stud', 60), ('Palmer Farm', 65)],
        )
        # Only the yard's bill is a cost in the yard's books.
        self.assertEqual(YardCost.objects.count(), 1)
        self.assertTrue(Visit.objects.get(client_id=yard_visit).practice_client.is_yard)

    def test_a_client_and_a_horse_made_with_no_signal_arrive_with_the_note(self):
        # The phone made both at the yard. Their own saves did not arrive
        # before the note did: the note still keeps its client and horse.
        new_client, new_horse = str(uuid.uuid4()), str(uuid.uuid4())
        self.own_note(str(uuid.uuid4()), visit_client_id=new_client, client_name='Ridge Racing',
                      client_horse_id=new_horse, horse_name='Comet')
        client = Client.objects.get(client_id=new_client)
        self.assertEqual((client.name, client.practice), ('Ridge Racing', self.practice))
        note = ClinicalNote.objects.get()
        self.assertEqual((note.client_horse.name, note.visit.practice_client), ('Comet', client))
        # The saves that arrive later change them and make no second one.
        self.assertEqual(self.client.post(CLIENTS, client_data(id=new_client, name='Ridge Racing Ltd')).status_code, 200)
        self.assertEqual(self.client.post(HORSES, horse_data(new_client, id=new_horse)).status_code, 200)
        self.assertEqual(ClientHorse.objects.filter(client_id=new_horse).count(), 1)

    def test_a_note_for_another_practices_client_joins_no_visit(self):
        other = make_practice('Far Vets', linked=False)
        theirs = Client.objects.create(practice=other, name='Their client')
        self.own_note(str(uuid.uuid4()), visit_client_id=str(theirs.client_id))
        self.assertIsNone(ClinicalNote.objects.get().visit)
        self.assertFalse(Visit.objects.exists())

    def test_the_recheck_date_of_an_own_horse_is_due_on_the_download(self):
        self.own_note(str(uuid.uuid4()), follow_up_date='2030-01-15')
        own = next(item for item in self.client.get(YARD).json()['horses'] if item.get('own'))
        self.assertEqual(own['due'], [{'kind': 'follow_up', 'label': 'Recheck', 'date': '2030-01-15'}])

    def test_the_history_of_an_own_horse(self):
        self.own_note(str(uuid.uuid4()))
        notes = self.client.get(NOTES, {'client_horse': self.juniper['id']}).json()['notes']
        self.assertEqual([(note['horse_name'], note['findings']) for note in notes], [('Juniper', 'In foal.')])
        self.assertEqual(self.client.get(NOTES, {'client_horse': str(uuid.uuid4())}).json()['notes'], [])
        self.assertEqual(self.client.get(NOTES, {'client_horse': 'x'}).status_code, 400)


class VisitRecordTests(YardCase):
    """The vet reads a visit back after it is sent."""

    def send(self, visit_id, **extra):
        response = self.client.post(NOTES, note_data(
            horse_id=str(self.horse.pk), visit_id=visit_id, lines=json.dumps([line()]), **extra,
        ))
        self.assertEqual(response.status_code, 201, response.content)

    def test_the_list_and_one_visit_with_its_notes_and_recording(self):
        visit_id = str(uuid.uuid4())
        self.send(visit_id, audio=SimpleUploadedFile('audio', b'voice-bytes', 'audio/mp4'))
        self.client.post(finish_url(visit_id))

        listed = self.client.get(VISITS).json()['visits']
        self.assertEqual(
            [(v['id'], v['yard'], v['status'], v['horses'], v['notes'], v['total']) for v in listed],
            [(visit_id, 'Palmer Farm', 'finished', 1, 1, '65.00')],
        )
        self.assertEqual(listed[0]['bills'][0]['number'], 'TEST-CV-0001')

        detail = self.client.get(f'{VISITS}{visit_id}/').json()
        note = detail['note_list'][0]
        self.assertEqual((note['horse_name'], note['findings']), ('Bramble', 'Mild heat left fore.'))
        self.assertEqual(note['lines'][0]['description'], 'Flu/Tet booster')
        # The recording plays from the address the note gives.
        recording = note['files'][0]
        self.assertEqual(recording['kind'], 'audio')
        self.assertEqual(b''.join(self.client.get(recording['url']).streaming_content), b'voice-bytes')

    def test_the_list_takes_the_same_number_of_queries_for_1_visit_or_5(self):
        def count():
            from django.db import connection
            from django.test.utils import CaptureQueriesContext
            with CaptureQueriesContext(connection) as queries:
                self.client.get(VISITS)
            return len(queries)
        self.send(str(uuid.uuid4()))
        one = count()
        for _ in range(4):
            self.send(str(uuid.uuid4()))
        self.assertEqual(count(), one)

    def test_one_client_only(self):
        self.send(str(uuid.uuid4()))
        yard_client = Client.objects.get()
        self.assertEqual(len(self.client.get(VISITS, {'client': str(yard_client.client_id)}).json()['visits']), 1)
        self.assertEqual(self.client.get(VISITS, {'client': str(uuid.uuid4())}).json()['visits'], [])
        self.assertEqual(self.client.get(VISITS, {'client': 'x'}).status_code, 400)

    def test_another_practice_reads_none_of_it(self):
        visit_id = str(uuid.uuid4())
        self.send(visit_id)
        self.client.force_login(make_vet('other', practice=make_practice('Far Vets', linked=False)))
        self.assertEqual(self.client.get(VISITS).json()['visits'], [])
        self.assertEqual(self.client.get(f'{VISITS}{visit_id}/').status_code, 404)

    def test_test_visits_leave_the_list_when_test_mode_is_turned_off(self):
        self.send(str(uuid.uuid4()))
        with override_settings(CVET_TEST_MODE=False):
            self.assertEqual(self.client.get(VISITS).json()['visits'], [])


class NoteLengthTests(YardCase):
    def test_a_line_break_counts_as_one_character_as_on_the_phone(self):
        # The phone lets the vet type 5,000 characters. A browser sends
        # each line break as two: the note must not be refused for that.
        text = ('a' * 99 + '\r\n') * 50
        response = self.client.post(NOTES, note_data(horse_id=str(self.horse.pk), note=text))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(len(ClinicalNote.objects.get().findings), 5000)

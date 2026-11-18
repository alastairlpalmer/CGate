"""The practice office (docs/C_VET_PLAN.md, section 7j).

Bills that are paid or not, a statement for a client, a bill by email to
a client of the practice's own, reports of the work done, and the price
list as a file. All of it belongs to the practice: another practice and
the yard's administrator reach none of it.
"""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from core.roles_testutils import make_admin

from . import office
from .models import Bill, Client, PriceItem
from .tests import NOTES, note_data
from .tests_billing import BillingCase
from .tests_clients import CLIENTS, HORSES, client_data, horse_data
from .tests_visit import line
from .testutils import make_practice, make_vet

BILLS = '/c-vet/bills/'
REPORTS = '/c-vet/reports/'
PRICE_FILE = '/c-vet/prices/file/'


def price_csv(text):
    return {'file': SimpleUploadedFile('prices.csv', text.encode(), 'text/csv')}


@override_settings(CVET_TEST_MODE=False)
class OfficeCase(BillingCase):
    """Two bills: one to the yard, one to a stud that is the practice's own client."""

    def setUp(self):
        super().setUp()
        self.stud = client_data(email='office@marshstud.example')
        self.client.post(CLIENTS, self.stud)
        self.juniper = horse_data(self.stud['id'])
        self.client.post(HORSES, self.juniper)
        yard_visit = self.visit((self.horse, [line()], {}))
        self.finish(yard_visit)
        stud_visit = str(uuid.uuid4())
        self.client.post(NOTES, note_data(
            visit_id=stud_visit, visit_client_id=self.stud['id'], client_horse_id=self.juniper['id'],
            horse_name='Juniper', note='In foal. A clinical finding.',
            lines=json.dumps([line(description='Scan', quantity='2', unit_price='30.00')]),
        ))
        self.finish(stud_visit)
        mail.outbox.clear()
        self.yard_bill = Bill.objects.get(to_name='Palmer Farm')
        self.stud_bill = Bill.objects.get(to_name='Marsh Stud')


class PaidTests(OfficeCase):
    def test_a_bill_is_marked_paid_and_not_paid_again(self):
        url = f'{BILLS}{self.stud_bill.pk}/paid/'
        self.assertRedirects(self.client.post(url, {'paid_on': '2026-11-10'}), BILLS)
        self.stud_bill.refresh_from_db()
        self.assertEqual(str(self.stud_bill.paid_on), '2026-11-10')
        self.client.post(url, {'paid': 'no'})
        self.stud_bill.refresh_from_db()
        self.assertIsNone(self.stud_bill.paid_on)
        # With no date: today.
        self.client.post(url)
        self.stud_bill.refresh_from_db()
        self.assertEqual(self.stud_bill.paid_on, timezone.localdate())

    def test_the_button_goes_back_to_the_page_it_was_on_and_to_no_other_site(self):
        url = f'{BILLS}{self.stud_bill.pk}/paid/'
        back = f'{BILLS}?state=unpaid'
        self.assertRedirects(self.client.post(url, {'next': back}), back, fetch_redirect_response=False)
        for elsewhere in ('https://evil.example/', '//evil.example/', '/c-vet//evil.example'):
            self.assertRedirects(self.client.post(url, {'next': elsewhere}), BILLS)

    def test_the_list_is_found_by_client_by_month_and_by_paid(self):
        self.client.post(f'{BILLS}{self.stud_bill.pk}/paid/')
        page = self.client.get(BILLS)
        self.assertEqual((page.context['total'], page.context['paid'], page.context['to_pay']),
                         (Decimal('125.00'), Decimal('60.00'), Decimal('65.00')))
        def numbers(**query):
            return [bill.number for bill in self.client.get(BILLS, query).context['bills']]
        self.assertEqual(numbers(client=self.stud['id']), [self.stud_bill.number])
        self.assertEqual(numbers(state='paid'), [self.stud_bill.number])
        self.assertEqual(numbers(state='unpaid'), [self.yard_bill.number])
        self.assertEqual(len(numbers(month=timezone.localdate().strftime('%Y-%m'))), 2)
        self.assertEqual(numbers(month='2001-01'), [])
        # A choice that is not one is no choice: the whole list, and no error.
        self.assertEqual(len(numbers(client='x', month='x', state='x')), 2)

    def test_another_practice_cannot_mark_it(self):
        self.client.force_login(make_vet('other', practice=make_practice('Far Vets', linked=False)))
        self.assertEqual(self.client.post(f'{BILLS}{self.stud_bill.pk}/paid/').status_code, 404)
        self.stud_bill.refresh_from_db()
        self.assertIsNone(self.stud_bill.paid_on)


class StatementTests(OfficeCase):
    def test_a_client_statement_lists_its_bills_and_what_is_to_pay(self):
        page = self.client.get(f"/c-vet/statements/{self.stud['id']}/")
        self.assertEqual((page.context['total'], page.context['paid'], page.context['to_pay']),
                         (Decimal('60.00'), Decimal('0.00'), Decimal('60.00')))
        self.assertContains(page, self.stud_bill.number)
        self.assertNotContains(page, self.yard_bill.number)
        self.assertNotContains(page, 'clinical finding')
        self.client.post(f'{BILLS}{self.stud_bill.pk}/paid/')
        self.assertEqual(self.client.get(f"/c-vet/statements/{self.stud['id']}/").context['to_pay'], 0)

    def test_another_practice_gets_no_statement(self):
        self.client.force_login(make_vet('other', practice=make_practice('Far Vets', linked=False)))
        self.assertEqual(self.client.get(f"/c-vet/statements/{self.stud['id']}/").status_code, 404)


class EmailTests(OfficeCase):
    def test_a_bill_goes_to_the_client_of_the_practice_with_replies_to_the_practice(self):
        self.practice.email = 'office@hillvets.example'
        self.practice.save()
        response = self.client.post(f'{BILLS}{self.stud_bill.pk}/email/')
        self.assertRedirects(response, f'{BILLS}{self.stud_bill.pk}/?sent=yes')
        sent = mail.outbox[0]
        self.assertEqual((sent.to, sent.reply_to), (['office@marshstud.example'], ['office@hillvets.example']))
        self.assertIn(self.stud_bill.number, sent.subject)
        html = sent.alternatives[0][0]
        self.assertIn('Scan', html)
        self.assertIn('60.00', html)
        # The bill says what was done and what it cost. Never the note.
        self.assertNotIn('clinical finding', html)
        self.assertNotIn('clinical finding', sent.body)
        self.stud_bill.refresh_from_db()
        self.assertEqual(self.stud_bill.emailed_to, 'office@marshstud.example')
        self.assertIsNotNone(self.stud_bill.emailed_at)

    def test_no_send_for_the_yard_for_a_client_with_no_address_or_for_a_test_bill(self):
        self.assertEqual(self.client.post(f'{BILLS}{self.yard_bill.pk}/email/').status_code, 404)
        Client.objects.filter(client_id=self.stud['id']).update(email='')
        self.assertEqual(self.client.post(f'{BILLS}{self.stud_bill.pk}/email/').status_code, 404)
        Client.objects.filter(client_id=self.stud['id']).update(email='office@marshstud.example')
        Bill.objects.filter(pk=self.stud_bill.pk).update(is_test=True)
        self.stud_bill.refresh_from_db()
        self.assertFalse(office.can_email(self.stud_bill))
        self.assertEqual(mail.outbox, [])

    def test_the_bill_page_offers_the_send_only_where_it_can_go(self):
        self.assertContains(self.client.get(f'{BILLS}{self.stud_bill.pk}/'), 'Send to office@marshstud.example')
        self.assertNotContains(self.client.get(f'{BILLS}{self.yard_bill.pk}/'), 'Send to')


class ReportTests(OfficeCase):
    def test_the_work_of_a_period_by_client_by_vet_and_by_item(self):
        page = self.client.get(REPORTS)
        self.assertEqual(page.context['total'], Decimal('125.00'))
        self.assertEqual(
            [(row['name'], row['visits'], row['horses'], row['total']) for row in page.context['clients']],
            [('Palmer Farm', 1, 1, Decimal('65.00')), ('Marsh Stud', 1, 1, Decimal('60.00'))],
        )
        self.assertEqual([(row['visits'], row['total']) for row in page.context['vets']], [(2, Decimal('125.00'))])
        self.assertEqual(
            [(row['name'], row['quantity'], row['total']) for row in page.context['items']],
            [('Flu/Tet booster', 1, Decimal('65.00')), ('Scan', 2, Decimal('60.00'))],
        )
        # Items and money only.
        self.assertNotContains(page, 'clinical finding')

    def test_the_period_and_the_file(self):
        before = timezone.localdate() - timedelta(days=400)
        page = self.client.get(REPORTS, {'from': before.isoformat(), 'to': (before + timedelta(days=5)).isoformat()})
        self.assertEqual((page.context['total'], page.context['items']), (0, []))
        # Dates the wrong way round are put right.
        today = timezone.localdate()
        page = self.client.get(REPORTS, {'from': today.isoformat(), 'to': before.isoformat()})
        self.assertEqual(page.context['total'], Decimal('125.00'))
        file = self.client.get(REPORTS, {'csv': 'items'})
        self.assertEqual(file['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('Scan,2', file.content.decode())

    def test_test_work_and_another_practice_are_not_counted(self):
        with override_settings(CVET_TEST_MODE=True):
            self.assertEqual(self.client.get(REPORTS).context['total'], 0)
        self.client.force_login(make_vet('other', practice=make_practice('Far Vets', linked=False)))
        self.assertEqual(self.client.get(REPORTS).context['total'], 0)


class PriceFileTests(OfficeCase):
    def test_a_file_adds_and_changes_prices_and_leaves_the_rest(self):
        PriceItem.objects.create(practice=self.practice, name='Sedation', price_inc_vat=Decimal('27.50'))
        PriceItem.objects.create(practice=self.practice, name='Visit fee', price_inc_vat=Decimal('40.00'))
        page = self.client.post(PRICE_FILE, price_csv(
            'Name,Category,Price,VAT\nsedation,Sedation,£30.00,20\nDental check,Dental,48,\n\n'
        ))
        self.assertEqual(page.context['done'], {'added': 1, 'changed': 1, 'same': 0})
        prices = {item.name: (item.category, item.price_inc_vat, item.vat_rate) for item in PriceItem.objects.all()}
        self.assertEqual(prices, {
            'Sedation': ('Sedation', Decimal('30.00'), Decimal('20.00')),
            'Dental check': ('Dental', Decimal('48.00'), Decimal('20.00')),
            'Visit fee': ('', Decimal('40.00'), Decimal('20.00')),
        })

    def test_one_wrong_line_and_nothing_is_changed(self):
        page = self.client.post(PRICE_FILE, price_csv('name,price\nScan,60\nRasp,forty\nScan,61\n'))
        self.assertEqual(len(page.context['problems']), 2)
        self.assertContains(page, 'Line 3: the price of &quot;Rasp&quot; is not a number')
        self.assertContains(page, 'Line 4: &quot;Scan&quot; is in the file twice')
        self.assertFalse(PriceItem.objects.exists())

    def test_a_file_that_is_not_a_price_list_is_refused(self):
        for text in ('item,cost\nScan,60\n', 'name,price\n'):
            self.assertTrue(self.client.post(PRICE_FILE, price_csv(text)).context['problems'])
        binary = {'file': SimpleUploadedFile('prices.csv', b'\xff\xfe\x00', 'text/csv')}
        self.assertIn('not text', self.client.post(PRICE_FILE, binary).context['problems'][0])
        self.assertEqual(self.client.post(PRICE_FILE).context['problems'], ['Choose a file first.'])
        self.assertFalse(PriceItem.objects.exists())

    def test_the_list_comes_back_as_a_file_that_can_be_sent_again(self):
        PriceItem.objects.create(practice=self.practice, name='Scan', category='Stud', price_inc_vat=Decimal('60.00'))
        file = self.client.get(PRICE_FILE, {'download': '1'}).content
        self.assertIn(b'Scan,Stud,60.00,20.00', file)
        page = self.client.post(PRICE_FILE, {'file': SimpleUploadedFile('prices.csv', file, 'text/csv')})
        self.assertEqual(page.context['done'], {'added': 0, 'changed': 0, 'same': 1})

    def test_another_practice_keeps_its_own_list(self):
        self.client.post(PRICE_FILE, price_csv('name,price\nScan,60\n'))
        self.assertEqual(PriceItem.objects.get().practice, self.practice)


class GateTests(OfficeCase):
    def test_the_yard_administrator_opens_none_of_it(self):
        self.client.force_login(make_admin())
        for url in (BILLS, REPORTS, PRICE_FILE, f"/c-vet/statements/{self.stud['id']}/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(f'{BILLS}{self.stud_bill.pk}/paid/').status_code, 403)
        self.assertEqual(self.client.post(f'{BILLS}{self.stud_bill.pk}/email/').status_code, 403)
        self.stud_bill.refresh_from_db()
        self.assertIsNone(self.stud_bill.paid_on)
        self.assertEqual(mail.outbox, [])

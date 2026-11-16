"""Phase 6: the bill to the yard, and what goes back to the yard
(docs/C_VET_PLAN.md, section 7e).

The practice bills the yard; each horse's part becomes a charge in
C-gate, so the yard's own invoices bill each owner; vaccination and
recheck dates go into the yard's records. Nothing clinical goes with
them, and nothing at all goes in test mode.
"""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import ExtraCharge, YardCost
from core.models import BusinessSettings, Horse, Owner, OwnershipShare
from core.roles_testutils import make_admin
from health.models import Vaccination, VaccinationType, VetVisit

from . import billing
from .admin import delete_test_notes
from .models import Bill, BillLine, ClinicalNote, PracticeYardLink, PriceItem, Visit
from .tests import NOTES, YARD, YardCase, note_data
from .tests_visit import PRICES, finish_url, line, price_data
from .testutils import make_practice, make_vet

SECRET = 'zqxjkv clinical words that the yard must not get'


class SplitAmountTests(TestCase):
    """The same rule as the phone's splitByShare, to the penny."""

    def _split(self, total, *shares):
        payers = [{'name': name, 'share': share} for name, share in shares]
        return [(payer['name'], amount) for payer, amount in billing.split_amount(Decimal(total), payers)]

    def test_owners_on_record_pay_all_of_it_in_proportion(self):
        self.assertEqual(self._split('100.00', ('A', '60.00'), ('B', '20.00')), [
            ('A', Decimal('75.00')), ('B', Decimal('25.00')),
        ])
        self.assertEqual(self._split('100.00', ('A', '60.00')), [('A', Decimal('100.00'))])

    def test_the_parts_add_up_to_the_total(self):
        parts = self._split('100.00', ('A', '33.33'), ('B', '33.33'), ('C', '33.34'))
        self.assertEqual([amount for _, amount in parts], [Decimal('33.33'), Decimal('33.33'), Decimal('33.34')])
        halves = self._split('65.01', ('A', '50.00'), ('B', '50.00'))
        self.assertEqual(sum(amount for _, amount in halves), Decimal('65.01'))

    def test_no_owner_on_record_gives_no_parts(self):
        self.assertEqual(billing.split_amount(Decimal('65.00'), []), [])
        self.assertEqual(billing.split_amount(Decimal('65.00'), None), [])

    def test_vat_inside_a_price(self):
        self.assertEqual(billing.vat_in('65.00', '20.00'), Decimal('10.83'))
        self.assertEqual(billing.vat_in('60.00', '20.00'), Decimal('10.00'))
        self.assertEqual(billing.vat_in('65.00', '0.00'), Decimal('0.00'))


@override_settings(CVET_TEST_MODE=False)
class BillingCase(YardCase):
    """A real (not test) visit, with a yard that has an email address."""

    def setUp(self):
        super().setUp()
        BusinessSettings.objects.update(email='office@palmerfarm.example')
        self.owner = Owner.objects.get(name='Jo Smith')

    def visit(self, *notes):
        """Send the notes of one visit. Each: (horse, lines, extra fields)."""
        visit_id = str(uuid.uuid4())
        for horse, lines, extra in notes:
            response = self.client.post(NOTES, note_data(
                horse_id=str(horse.pk), horse_name=horse.name, visit_id=visit_id,
                lines=json.dumps(lines), note=SECRET, **extra,
            ))
            self.assertEqual(response.status_code, 201, response.content)
        return visit_id

    def finish(self, visit_id):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(finish_url(visit_id))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


class BillToTheYardTests(BillingCase):
    def test_finish_makes_one_bill_to_the_yard(self):
        second = Horse.objects.create(name='Clover')
        visit_id = self.visit(
            (self.horse, [line()], {}),
            (second, [line(description='Sedation', quantity='2', unit_price='27.50')], {}),
        )
        data = self.finish(visit_id)
        bill = Bill.objects.get()
        self.assertEqual(data['bills'], [{'number': 'CV-0001', 'to': 'Palmer Farm', 'total': '120.00'}])
        self.assertEqual((bill.number, bill.bill_to, bill.to_name), ('CV-0001', 'yard', 'Palmer Farm'))
        self.assertEqual((bill.total_inc_vat, bill.total_vat, bill.total_ex_vat),
                         (Decimal('120.00'), Decimal('20.00'), Decimal('100.00')))
        self.assertEqual(
            list(bill.lines.values_list('horse_name', 'description', 'amount_inc_vat', 'share')),
            [('Bramble', 'Flu/Tet booster', Decimal('65.00'), Decimal('100.00')),
             ('Clover', 'Sedation', Decimal('55.00'), Decimal('100.00'))],
        )

    def test_finish_twice_makes_one_bill_and_one_set_of_yard_records(self):
        visit_id = self.visit((self.horse, [line()], {}))
        self.finish(visit_id)
        self.finish(visit_id)
        billing.close_out(Visit.objects.get())
        self.assertEqual(Bill.objects.count(), 1)
        self.assertEqual(VetVisit.objects.count(), 1)
        self.assertEqual(ExtraCharge.objects.count(), 1)
        self.assertEqual(YardCost.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_numbers_run_with_no_gaps_and_use_the_practices_letters(self):
        self.practice.bill_prefix = 'HV'
        self.practice.save()
        for _ in range(3):
            self.finish(self.visit((self.horse, [line()], {})))
        self.assertEqual(sorted(Bill.objects.values_list('number', flat=True)), ['HV-0001', 'HV-0002', 'HV-0003'])

    def test_a_visit_with_nothing_to_bill_makes_no_bill(self):
        self.finish(self.visit((self.horse, [], {})))
        self.assertFalse(Bill.objects.exists())
        self.assertFalse(YardCost.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_a_note_after_finish_goes_on_a_further_bill(self):
        visit_id = self.visit((self.horse, [line()], {}))
        self.finish(visit_id)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(NOTES, note_data(
                horse_id=str(self.horse.pk), visit_id=visit_id,
                lines=json.dumps([line(description='Visit fee', unit_price='40.00')]),
            ))
        first, second = Bill.objects.order_by('pk')
        # The first bill did not change.
        self.assertEqual((first.number, first.total_inc_vat, first.lines.count()), ('CV-0001', Decimal('65.00'), 1))
        self.assertEqual((second.number, second.total_inc_vat, second.visit), ('CV-0002', Decimal('40.00'), first.visit))
        self.assertEqual(VetVisit.objects.count(), 2)
        self.assertEqual(YardCost.objects.count(), 2)
        self.assertFalse(ClinicalNote.objects.filter(yard_sent_at__isnull=True).exists())


class YardRecordsTests(BillingCase):
    def test_each_horses_part_is_a_charge_for_its_owner_in_c_gate(self):
        self.finish(self.visit((self.horse, [
            line(description='Sedation', quantity='2', unit_price='27.50'),
            line(description='Visit fee', unit_price='40.00'),
        ], {})))
        record = VetVisit.objects.get()
        self.assertEqual((record.horse, record.date, record.cost), (self.horse, timezone.localdate(), Decimal('95.00')))
        self.assertEqual(record.reason, '2 x Sedation; Visit fee')
        charge = ExtraCharge.objects.get()
        self.assertEqual(record.extra_charge, charge)
        self.assertEqual((charge.horse, charge.owner, charge.amount, charge.charge_type),
                         (self.horse, self.owner, Decimal('95.00'), 'vet'))
        self.assertEqual(charge.description, 'Vet - 2 x Sedation; Visit fee')
        self.assertFalse(charge.invoiced)
        self.assertTrue(charge.split_by_ownership)
        self.assertIn('From C-vet: Hill Vets', record.notes)

    def test_nothing_clinical_goes_to_the_yard(self):
        self.finish(self.visit((self.horse, [line()], {'follow_up_date': timezone.localdate().isoformat()})))
        record = VetVisit.objects.get()
        charge = ExtraCharge.objects.get()
        cost = YardCost.objects.get()
        everything = ' '.join(str(value) for row in (record, charge, cost) for value in vars(row).values())
        everything += ' '.join(str(message.body) + str(message.alternatives) for message in mail.outbox)
        self.assertNotIn('zqxjkv', everything)
        self.assertEqual((record.diagnosis, record.treatment), ('', ''))

    def test_the_charge_goes_on_the_owners_next_yard_invoice(self):
        from invoicing.models import InvoiceLineItem
        from invoicing.services import InvoiceService

        self.finish(self.visit((self.horse, [line()], {})))
        today = timezone.localdate()
        InvoiceService.generate_monthly_invoices(today.year, today.month)
        item = InvoiceLineItem.objects.get(description__icontains='Flu/Tet booster')
        self.assertEqual(item.invoice.owner, self.owner)
        self.assertEqual(item.line_total, Decimal('65.00'))

    def test_a_vaccination_is_recorded_in_the_yards_vaccination_records(self):
        flu = VaccinationType.objects.create(name='Flu/Tet', interval_months=12)
        data = price_data(vaccination_type_id=str(flu.pk))
        self.assertEqual(self.client.post(PRICES, data).status_code, 201)
        self.finish(self.visit((self.horse, [
            line(price_id=data['id']),
            line(description='Visit fee', unit_price='40.00'),
        ], {})))
        dose = Vaccination.objects.get()
        today = timezone.localdate()
        self.assertEqual((dose.horse, dose.vaccination_type, dose.date_given, dose.cost),
                         (self.horse, flu, today, Decimal('65.00')))
        self.assertEqual(dose.next_due_date, Vaccination._add_months(today, 12))
        self.assertEqual(dose.extra_charge.charge_type, 'vaccination')
        # The other item is the vet visit record; the two add up to the bill.
        self.assertEqual(VetVisit.objects.get().cost, Decimal('40.00'))
        self.assertEqual(sum(c.amount for c in ExtraCharge.objects.all()), Bill.objects.get().total_inc_vat)
        # The vet's own list now shows the next dose as the one due.
        due = self.client.get(YARD).json()['horses'][0]['due']
        self.assertEqual(due, [{'kind': 'vaccination', 'label': 'Flu/Tet', 'date': dose.next_due_date.isoformat()}])

    def test_a_recheck_date_goes_to_the_yard_with_no_reason(self):
        recheck = timezone.localdate() + timedelta(days=14)
        self.finish(self.visit((self.horse, [], {'follow_up_date': recheck.isoformat()})))
        record = VetVisit.objects.get()
        self.assertEqual((record.follow_up_date, record.cost, record.reason), (recheck, Decimal('0.00'), 'Vet visit'))
        self.assertFalse(ExtraCharge.objects.exists())
        self.assertFalse(Bill.objects.exists())

    def test_a_yard_that_charges_vat_passes_on_the_amount_without_vat(self):
        BusinessSettings.objects.update(vat_rate=Decimal('20.00'))
        self.finish(self.visit((self.horse, [line()], {})))
        # The yard's invoice adds its own 20% to this, which gives 65.00 again.
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('54.17'))

    def test_the_bill_is_a_cost_in_the_yards_books(self):
        self.finish(self.visit((self.horse, [line()], {})))
        cost = YardCost.objects.get()
        self.assertEqual((cost.category, cost.supplier, cost.amount, cost.vat_amount),
                         ('professional', 'Hill Vets', Decimal('54.17'), Decimal('10.83')))
        self.assertEqual(cost.total_with_vat, Decimal('65.00'))
        self.assertIn('CV-0001', cost.description)
        self.assertEqual(Bill.objects.get().yard_cost, cost)

    def test_the_yard_gets_the_bill_by_email(self):
        self.finish(self.visit((self.horse, [line()], {})))
        message = mail.outbox[0]
        self.assertEqual(message.to, ['office@palmerfarm.example'])
        self.assertIn('CV-0001', message.subject)
        self.assertIn('65.00', message.subject)
        html = message.alternatives[0][0]
        for text in ('Bramble', 'Flu/Tet booster', 'Hill Vets'):
            self.assertIn(text, html)
        self.assertIsNotNone(Bill.objects.get().emailed_at)

    def test_a_horse_with_no_owner_is_billed_and_the_yard_is_told(self):
        loose = Horse.objects.create(name='No Owner Ned')
        self.finish(self.visit((loose, [line()], {})))
        self.assertEqual(Bill.objects.get().total_inc_vat, Decimal('65.00'))
        self.assertEqual(VetVisit.objects.get().cost, Decimal('65.00'))
        self.assertFalse(ExtraCharge.objects.exists())
        self.assertIn('No Owner Ned', mail.outbox[0].alternatives[0][0])
        self.assertIn('has no', mail.outbox[0].alternatives[0][0])

    def test_a_horse_that_left_c_gate_is_billed_and_the_yard_is_told(self):
        visit_id = str(uuid.uuid4())
        self.client.post(NOTES, note_data(
            horse_id='999999', horse_name='Gone Away', visit_id=visit_id, lines=json.dumps([line()]),
        ))
        self.finish(visit_id)
        self.assertEqual(Bill.objects.get().lines.get().horse_name, 'Gone Away')
        self.assertFalse(VetVisit.objects.exists())
        self.assertIn('Gone Away', mail.outbox[0].alternatives[0][0])

    def test_the_yard_can_turn_each_thing_off(self):
        PracticeYardLink.objects.update(sends_charges=False, sends_follow_up_dates=False, sends_vaccination_dates=False)
        flu = VaccinationType.objects.create(name='Flu/Tet')
        PriceItem.objects.create(practice=self.practice, name='Booster', price_inc_vat=65, vaccination_type=flu)
        recheck = timezone.localdate().isoformat()
        self.finish(self.visit((self.horse, [line()], {'follow_up_date': recheck})))
        self.assertEqual(Bill.objects.count(), 1)
        for model in (VetVisit, Vaccination, ExtraCharge, YardCost):
            self.assertFalse(model.objects.exists(), model.__name__)
        self.assertEqual(self.client.get(YARD).json()['vaccination_types'], [])

    def test_nothing_goes_to_the_yard_after_the_link_ends(self):
        visit_id = self.visit((self.horse, [line()], {}))
        PracticeYardLink.objects.update(ended_on=timezone.localdate())
        self.finish(visit_id)
        self.assertEqual(Bill.objects.count(), 1)
        for model in (VetVisit, ExtraCharge, YardCost):
            self.assertFalse(model.objects.exists(), model.__name__)
        self.assertEqual(mail.outbox, [])


class MonthlyBillTests(BillingCase):
    """A practice that bills the yard once a month."""

    def setUp(self):
        super().setUp()
        PracticeYardLink.objects.update(bill_when='month')

    def _visit_on(self, day, *notes):
        visit_id = self.visit(*notes)
        self.finish(visit_id)
        Visit.objects.filter(client_id=visit_id).update(date=day)
        return visit_id

    def test_finish_makes_no_bill_but_the_yard_gets_its_records_at_once(self):
        visit_id = self.visit((self.horse, [line()], {}))
        data = self.finish(visit_id)
        self.assertEqual((data['bills'], data['bill_when']), ([], 'month'))
        self.assertFalse(Bill.objects.exists())
        # The owner's charge does not wait for the month's bill.
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('65.00'))
        self.assertFalse(YardCost.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_the_monthly_run_makes_one_bill_for_the_month_before(self):
        today = timezone.localdate()
        first = today.replace(day=1)
        second = Horse.objects.create(name='Clover')
        self._visit_on(first - timedelta(days=20), (self.horse, [line()], {}))
        self._visit_on(first - timedelta(days=3), (second, [line(description='Sedation', unit_price='27.50')], {}))
        # A visit of this month waits for next month's bill.
        self._visit_on(first, (self.horse, [line(description='Visit fee', unit_price='40.00')], {}))

        with self.captureOnCommitCallbacks(execute=True):
            bills = billing.bill_month_end(today)
        bill = Bill.objects.get()
        self.assertEqual(bills, [bill])
        self.assertIsNone(bill.visit)
        self.assertEqual((bill.period_start, bill.period_end), (first - timedelta(days=20), first - timedelta(days=3)))
        self.assertEqual((bill.total_inc_vat, bill.to_name), (Decimal('92.50'), 'Palmer Farm'))
        self.assertEqual(
            list(bill.lines.values_list('date', 'horse_name', 'description')),
            [(first - timedelta(days=20), 'Bramble', 'Flu/Tet booster'), (first - timedelta(days=3), 'Clover', 'Sedation')],
        )
        # One cost and one email for the month, not one for each visit.
        self.assertEqual(YardCost.objects.get().total_with_vat, Decimal('92.50'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('visits from', mail.outbox[0].alternatives[0][0])

    def test_the_monthly_run_twice_bills_nothing_twice(self):
        self._visit_on(timezone.localdate().replace(day=1) - timedelta(days=5), (self.horse, [line()], {}))
        billing.bill_month_end()
        self.assertEqual(billing.bill_month_end(), [])
        from .tasks import bill_monthly
        self.assertEqual(bill_monthly(), '0 monthly bill(s) made')
        self.assertEqual(Bill.objects.count(), 1)

    def test_the_monthly_run_leaves_a_practice_that_bills_each_visit_alone(self):
        PracticeYardLink.objects.update(bill_when='visit')
        self.visit((self.horse, [line()], {}))   # not finished: nothing to bill
        self.assertEqual(billing.bill_month_end(), [])

    def test_an_open_visit_is_not_billed(self):
        self.visit((self.horse, [line()], {}))
        Visit.objects.update(date=timezone.localdate() - timedelta(days=60))
        self.assertEqual(billing.bill_month_end(), [])

    def test_the_office_can_bill_now_from_the_bills_page(self):
        self.finish(self.visit((self.horse, [line()], {})))
        page = self.client.get('/c-vet/bills/')
        self.assertContains(page, 'Not on a bill yet: £65.00')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertRedirects(self.client.post('/c-vet/bills/now/'), '/c-vet/bills/')
        self.assertEqual(Bill.objects.get().total_inc_vat, Decimal('65.00'))
        self.assertContains(self.client.get('/c-vet/bills/'), 'All finished work is on a bill')
        self.assertEqual(len(mail.outbox), 1)

    def test_the_practice_chooses_when_it_bills(self):
        self.assertContains(self.client.get('/c-vet/bills/'), 'value="month" checked')
        self.client.post('/c-vet/bills/settings/', {'bill_when': 'visit'})
        self.assertEqual(PracticeYardLink.objects.get().bill_when, 'visit')
        self.client.post('/c-vet/bills/settings/', {'bill_when': 'whenever'})
        self.assertEqual(PracticeYardLink.objects.get().bill_when, 'visit')

    def test_only_the_practice_can_choose_or_bill(self):
        self.finish(self.visit((self.horse, [line()], {})))
        self.client.force_login(make_admin())
        self.assertEqual(self.client.post('/c-vet/bills/settings/', {'bill_when': 'visit'}).status_code, 403)
        self.assertEqual(self.client.post('/c-vet/bills/now/').status_code, 403)
        self.assertEqual(PracticeYardLink.objects.get().bill_when, 'month')
        self.assertFalse(Bill.objects.exists())


class YardMarginTests(BillingCase):
    """The yard adds its margin when a vet item becomes an owner's charge."""

    def test_the_margin_is_on_the_owners_charge_and_not_on_the_vets_bill(self):
        PracticeYardLink.objects.update(yard_margin_percent=Decimal('10.00'))
        self.finish(self.visit((self.horse, [line()], {})))
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('71.50'))
        self.assertEqual(Bill.objects.get().total_inc_vat, Decimal('65.00'))
        self.assertEqual(YardCost.objects.get().total_with_vat, Decimal('65.00'))

    def test_no_margin_passes_the_amount_on(self):
        self.finish(self.visit((self.horse, [line()], {})))
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('65.00'))

    def test_a_yard_that_charges_vat_adds_its_margin_to_the_amount_without_vat(self):
        BusinessSettings.objects.update(vat_rate=Decimal('20.00'))
        PracticeYardLink.objects.update(yard_margin_percent=Decimal('10.00'))
        self.finish(self.visit((self.horse, [line()], {})))
        # 65.00 with VAT is 54.17 without. Plus 10% is 59.59.
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('59.59'))

    def test_the_margin_is_worked_out_item_by_item(self):
        self.assertEqual(billing.yard_amount('65.00', '20.00', False, '12.5'), Decimal('73.13'))
        self.assertEqual(billing.yard_amount('0.00', '20.00', False, '10'), Decimal('0.00'))
        self.assertEqual(billing.yard_amount('65.00', '20.00', False, None), Decimal('65.00'))


class TestModeTests(YardCase):
    """In test mode a bill is made, and nothing goes to the yard."""

    def test_a_test_bill_has_its_own_numbers_and_touches_no_yard_record(self):
        BusinessSettings.objects.update(email='office@palmerfarm.example')
        visit_id = str(uuid.uuid4())
        self.client.post(NOTES, note_data(
            horse_id=str(self.horse.pk), visit_id=visit_id, lines=json.dumps([line()]),
            follow_up_date=timezone.localdate().isoformat(),
        ))
        with self.captureOnCommitCallbacks(execute=True):
            data = self.client.post(finish_url(visit_id)).json()
        self.assertEqual(data['bills'][0]['number'], 'TEST-CV-0001')
        self.assertTrue(Bill.objects.get().is_test)
        for model in (VetVisit, Vaccination, ExtraCharge, YardCost):
            self.assertFalse(model.objects.exists(), model.__name__)
        self.assertEqual(mail.outbox, [])
        self.practice.refresh_from_db()
        self.assertEqual((self.practice.next_bill_number, self.practice.next_test_bill_number), (1, 2))

    def test_deleting_test_notes_deletes_test_bills(self):
        visit_id = str(uuid.uuid4())
        self.client.post(NOTES, note_data(visit_id=visit_id, lines=json.dumps([line()])))
        self.client.post(finish_url(visit_id))
        self.assertEqual(delete_test_notes(self.practice), 1)
        self.assertFalse(Bill.objects.exists())
        self.assertFalse(BillLine.objects.exists())
        self.assertFalse(Visit.objects.exists())


class BillEachOwnerTests(BillingCase):
    """A yard set to "bill each owner": the vet invoices the owners."""

    def setUp(self):
        super().setUp()
        PracticeYardLink.objects.update(bill_to='owners')
        self.ali = Owner.objects.create(name='Ali Khan')
        OwnershipShare.objects.create(horse=self.horse, owner=self.owner, share_percentage=60, is_primary_contact=True)
        OwnershipShare.objects.create(horse=self.horse, owner=self.ali, share_percentage=20)

    def test_one_bill_for_each_owner_and_they_pay_all_of_it(self):
        data = self.finish(self.visit((self.horse, [line(unit_price='100.00')], {})))
        self.assertEqual([(b['to'], b['total']) for b in data['bills']], [('Jo Smith', '75.00'), ('Ali Khan', '25.00')])
        jo, ali = Bill.objects.order_by('pk')
        self.assertEqual((jo.bill_to, jo.to_owner, ali.to_owner), ('owners', self.owner, self.ali))
        self.assertEqual((jo.lines.get().share, ali.lines.get().share), (Decimal('75.00'), Decimal('25.00')))
        self.assertEqual(jo.total_inc_vat + ali.total_inc_vat, Decimal('100.00'))

    def test_the_yard_gets_dates_but_no_charge_no_cost_and_no_bill_email(self):
        flu = VaccinationType.objects.create(name='Flu/Tet')
        data = price_data(vaccination_type_id=str(flu.pk))
        self.client.post(PRICES, data)
        self.finish(self.visit((self.horse, [line(price_id=data['id'])], {
            'follow_up_date': timezone.localdate().isoformat(),
        })))
        self.assertEqual(Vaccination.objects.get().cost, Decimal('0.00'))
        self.assertEqual(VetVisit.objects.get().cost, Decimal('0.00'))
        self.assertFalse(ExtraCharge.objects.exists())
        self.assertFalse(YardCost.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_a_horse_with_no_owner_on_record_is_billed_to_the_yard(self):
        loose = Horse.objects.create(name='No Owner Ned')
        self.finish(self.visit((loose, [line()], {})))
        bill = Bill.objects.get()
        self.assertEqual((bill.bill_to, bill.to_name), ('yard', 'Palmer Farm'))


class BillPagesTests(BillingCase):
    def setUp(self):
        super().setUp()
        self.finish(self.visit((self.horse, [line()], {})))
        self.bill = Bill.objects.get()

    def test_the_practice_sees_its_bills(self):
        response = self.client.get('/c-vet/bills/')
        self.assertContains(response, 'CV-0001')
        self.assertContains(response, 'Palmer Farm')
        page = self.client.get(f'/c-vet/bills/{self.bill.pk}/')
        for text in ('Invoice CV-0001', 'Bramble', 'Flu/Tet booster', '£65.00', '£54.17', '£10.83', 'Hill Vets'):
            self.assertContains(page, text)
        self.assertNotContains(page, 'zqxjkv')

    def test_the_csv_file_has_a_row_for_each_line(self):
        response = self.client.get('/c-vet/bills/export.csv')
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        rows = response.content.decode('utf-8-sig').strip().splitlines()
        self.assertEqual(len(rows), 2)
        today = timezone.localdate().isoformat()
        self.assertEqual(rows[1].split(',')[:6], ['CV-0001', today, 'Palmer Farm', today, 'Bramble', 'Flu/Tet booster'])
        self.assertTrue(rows[1].endswith('54.17,10.83,65.00'))

    def test_nobody_else_sees_them(self):
        url = f'/c-vet/bills/{self.bill.pk}/'
        self.client.force_login(make_vet('other', practice=make_practice('Other Vets')))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get('/c-vet/bills/'), 'CV-0001')
        self.assertEqual(len(self.client.get('/c-vet/bills/export.csv').content.decode('utf-8-sig').strip().splitlines()), 1)
        self.client.force_login(make_admin())
        for address in (url, '/c-vet/bills/', '/c-vet/bills/export.csv'):
            self.assertEqual(self.client.get(address).status_code, 403, address)

    def test_test_bills_and_real_bills_are_not_mixed(self):
        with override_settings(CVET_TEST_MODE=True):
            self.assertNotContains(self.client.get('/c-vet/bills/'), 'CV-0001')


class VaccinationPriceTests(YardCase):
    def test_a_price_can_be_marked_as_one_of_the_yards_vaccinations(self):
        flu = VaccinationType.objects.create(name='Flu/Tet')
        VaccinationType.objects.create(name='Old one', is_active=False)
        self.assertEqual(self.client.get(YARD).json()['vaccination_types'], [{'id': flu.pk, 'name': 'Flu/Tet'}])
        response = self.client.post(PRICES, price_data(vaccination_type_id=str(flu.pk)))
        self.assertEqual(response.json()['vaccination_type_id'], flu.pk)
        self.assertEqual(PriceItem.objects.get().vaccination_type, flu)

    def test_a_type_that_is_not_the_yards_is_refused(self):
        for bad in ('999999', 'abc'):
            response = self.client.post(PRICES, price_data(vaccination_type_id=bad))
            self.assertEqual(response.json(), {'error': 'vaccination_type_invalid'})
        self.assertFalse(PriceItem.objects.exists())

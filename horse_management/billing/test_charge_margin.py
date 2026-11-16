"""Add margin: the yard's margin on many unbilled charges in one step.

The yard pays a vet or a farrier and bills each owner, normally with a
margin and sometimes with none. billing.margin holds the rules; these
tests hold them.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing import margin
from billing.models import ExtraCharge, ServiceProvider
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_admin, make_user_with_access
from health.models import VetVisit
from health.views import sync_record_charge

URL = '/billing/charges/margin/'


class MarginRulesTests(TestCase):
    def test_a_percentage_as_a_person_types_it(self):
        for text, value in (('10', '10'), (' 12.5 ', '12.5'), ('10%', '10'), ('0', '0'), ('500', '500')):
            self.assertEqual(margin.parse_percent(text), Decimal(value), text)
        for bad in ('', 'ten', '-1', '501', '1.234', 'NaN', 'inf', '1e2x'):
            self.assertIsNone(margin.parse_percent(bad), bad)

    def test_the_amount_for_a_cost_and_a_margin(self):
        self.assertEqual(margin.with_margin('65.00', '10'), Decimal('71.50'))
        self.assertEqual(margin.with_margin('27.50', '12.5'), Decimal('30.94'))
        self.assertEqual(margin.with_margin('54.17', '10'), Decimal('59.59'))
        self.assertEqual(margin.with_margin('65.00', '0'), Decimal('65.00'))
        self.assertEqual(margin.with_margin('0.00', '10'), Decimal('0.00'))


class MarginCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        location = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Grass', daily_rate=5)
        self.jo = Owner.objects.create(name='Jo Bloggs')
        self.bramble = Horse.objects.create(name='Bramble')
        OwnershipShare.objects.create(horse=self.bramble, owner=self.jo, share_percentage=100, is_primary_contact=True)
        Placement.objects.create(
            horse=self.bramble, owner=self.jo, location=location, rate_type=self.rate,
            start_date=self.today - timedelta(days=30),
        )
        self.vet = ServiceProvider.objects.create(name='Hill Vets', provider_type='vet')
        self.client.force_login(make_admin(username='margin-admin'))

    def charge(self, amount='65.00', **fields):
        data = {
            'horse': self.bramble, 'owner': self.jo, 'charge_type': 'vet', 'date': self.today,
            'description': 'Vet - Sedation', 'amount': Decimal(amount), 'service_provider': self.vet,
        }
        data.update(fields)
        return ExtraCharge.objects.create(**data)

    def post(self, charges, percent, **filters):
        data = {'charge': [c.pk for c in charges], 'percent': percent}
        data.update(filters)
        return self.client.post(URL, data)


class ApplyMarginTests(MarginCase):
    def test_the_margin_goes_on_and_the_cost_is_remembered(self):
        charge = self.charge()
        response = self.post([charge], '10')
        self.assertEqual(response.status_code, 302)
        charge.refresh_from_db()
        self.assertEqual((charge.amount, charge.cost_amount, charge.margin_amount),
                         (Decimal('71.50'), Decimal('65.00'), Decimal('6.50')))

    def test_the_same_margin_twice_is_not_added_twice(self):
        charge = self.charge()
        self.post([charge], '10')
        self.post([charge], '10')
        charge.refresh_from_db()
        self.assertEqual((charge.amount, charge.cost_amount), (Decimal('71.50'), Decimal('65.00')))

    def test_a_new_margin_is_worked_out_from_the_cost(self):
        charge = self.charge()
        self.post([charge], '10')
        self.post([charge], '20')
        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal('78.00'))

    def test_nought_takes_the_margin_off(self):
        charge = self.charge()
        self.post([charge], '10')
        response = self.post([charge], '0')
        charge.refresh_from_db()
        self.assertEqual((charge.amount, charge.cost_amount), (Decimal('65.00'), Decimal('65.00')))
        self.assertIn('Margin taken off 1 charge.', [str(m) for m in response.wsgi_request._messages])

    def test_only_the_ticked_charges_change(self):
        ticked, left = self.charge(), self.charge(description='Vet - Visit fee', amount='40.00')
        self.post([ticked], '10')
        left.refresh_from_db()
        self.assertEqual((left.amount, left.cost_amount), (Decimal('40.00'), None))

    def test_a_charge_on_an_invoice_is_never_changed(self):
        billed = self.charge(invoiced=True)
        response = self.post([billed], '10')
        billed.refresh_from_db()
        self.assertEqual((billed.amount, billed.cost_amount), (Decimal('65.00'), None))
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any('1 charge was not changed' in m for m in messages), messages)

    def test_a_charge_with_a_line_on_a_live_invoice_is_never_changed(self):
        # A split charge on a co-owned horse: one owner is invoiced, the
        # charge still reads "not invoiced". It must not change now.
        from invoicing.models import Invoice, InvoiceLineItem

        charge = self.charge()
        invoice = Invoice.objects.create(
            owner=self.jo, invoice_number='INV-1', period_start=self.today, period_end=self.today,
            status=Invoice.Status.SENT,
        )
        InvoiceLineItem.objects.create(
            invoice=invoice, charge=charge, description='Vet - Sedation', quantity=1,
            unit_price=Decimal('32.50'), line_total=Decimal('32.50'),
        )
        self.assertNotIn(charge, list(margin.unbilled_charges()))
        self.post([charge], '10')
        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal('65.00'))

    def test_a_deleted_charge_is_skipped(self):
        charge = self.charge()
        pk = charge.pk
        charge.delete()
        self.assertEqual(margin.apply_margin([pk], Decimal('10')), (0, 1))

    def test_a_bad_margin_changes_nothing_and_says_why(self):
        charge = self.charge()
        for bad in ('ten', '-5', '900', ''):
            response = self.post([charge], bad)
            messages = [str(m) for m in response.wsgi_request._messages]
            self.assertTrue(any('from 0 to 500' in m for m in messages), bad)
        charge.refresh_from_db()
        self.assertEqual((charge.amount, charge.cost_amount), (Decimal('65.00'), None))

    def test_no_charge_ticked_says_so(self):
        response = self.post([], '10')
        self.assertIn('Tick one charge or more first.', [str(m) for m in response.wsgi_request._messages])

    def test_the_page_comes_back_with_the_same_filters(self):
        response = self.post([self.charge()], '10', type='vet', search='sed')
        self.assertIn('type=vet', response['Location'])
        self.assertIn('search=sed', response['Location'])
        self.assertIn('percent=10', response['Location'])


class HealthRecordTests(MarginCase):
    """A charge made from a health record keeps its margin when the record is saved again."""

    def test_the_records_cost_keeps_step_so_the_margin_is_not_undone(self):
        record = VetVisit.objects.create(
            horse=self.bramble, date=self.today, vet=self.vet, reason='Sedation', cost=Decimal('65.00'),
        )
        self.assertEqual(sync_record_charge(record), 'created')
        charge = ExtraCharge.objects.get()
        self.post([charge], '10')
        record.refresh_from_db()
        self.assertEqual(record.cost, Decimal('71.50'))
        # Staff change the date on the record and save it: the charge is
        # brought into line with the record, and the margin is still there.
        record.date = self.today - timedelta(days=1)
        record.save()
        sync_record_charge(record)
        charge.refresh_from_db()
        self.assertEqual((charge.amount, charge.cost_amount), (Decimal('71.50'), Decimal('65.00')))


class PageTests(MarginCase):
    def test_the_page_lists_unbilled_charges_with_cost_and_new_amount(self):
        self.charge()
        self.charge(description='Vet - Visit fee', amount='40.00')
        self.charge(description='Already billed', invoiced=True)
        response = self.client.get(URL, {'percent': '10'})
        self.assertContains(response, 'Vet - Sedation')
        self.assertContains(response, '£71.50')
        self.assertContains(response, '£44.00')
        self.assertNotContains(response, 'Already billed')
        self.assertEqual(response.context['cost_total'], Decimal('105.00'))
        self.assertEqual(response.context['new_total'], Decimal('115.50'))
        self.assertEqual(response.context['margin_total'], Decimal('10.50'))
        self.assertContains(response, 'Set a margin of 10% on the ticked charges')

    def test_no_template_comment_is_printed_on_the_page(self):
        self.charge()
        body = self.client.get(URL, {'percent': '10'}).content.decode()
        self.assertNotIn('{#', body)
        self.assertNotIn('{%', body)

    def test_each_charge_has_one_tick_box(self):
        # Two boxes for one charge (a phone layout and a desktop layout)
        # would let a charge that was unticked in one still be changed.
        self.charge()
        self.charge(description='Vet - Visit fee', amount='40.00')
        response = self.client.get(URL, {'percent': '10'})
        self.assertEqual(response.content.decode().count('name="charge"'), 2)

    def test_a_charge_sent_twice_is_counted_and_changed_once(self):
        charge = self.charge()
        response = self.client.post(URL, {'charge': [charge.pk, charge.pk], 'percent': '10'})
        self.assertIn('Margin of 10% set on 1 charge.', [str(m) for m in response.wsgi_request._messages])
        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal('71.50'))

    def test_with_no_margin_typed_nothing_can_be_changed(self):
        self.charge()
        response = self.client.get(URL)
        self.assertContains(response, 'Type a margin above')
        self.assertNotContains(response, 'on the ticked charges')

    def test_the_last_margin_used_is_offered_next_time(self):
        self.post([self.charge()], '12.5')
        self.assertContains(self.client.get(URL), 'value="12.5"')

    def test_a_charge_that_has_a_margin_shows_its_cost_not_its_amount_as_the_base(self):
        charge = self.charge()
        self.post([charge], '10')
        row = self.client.get(URL, {'percent': '20'}).context['rows'][0]
        self.assertEqual((row['cost'], row['new']), (Decimal('65.00'), Decimal('78.00')))

    def test_filters(self):
        farrier = ServiceProvider.objects.create(name='Sam Shoe', provider_type='farrier')
        self.charge(description='Vet one')
        self.charge(description='Vaccination one', charge_type='vaccination')
        self.charge(description='Shoes', charge_type='farrier', service_provider=farrier)
        self.charge(description='Old vet', date=self.today - timedelta(days=40))

        def shown(**params):
            return sorted(row['charge'].description for row in self.client.get(URL, params).context['rows'])

        self.assertEqual(shown(type='vet_all'), ['Old vet', 'Vaccination one', 'Vet one'])
        self.assertEqual(shown(type='farrier'), ['Shoes'])
        self.assertEqual(shown(provider=str(farrier.pk)), ['Shoes'])
        self.assertEqual(shown(**{'from': (self.today - timedelta(days=7)).isoformat()}), ['Shoes', 'Vaccination one', 'Vet one'])
        self.assertEqual(shown(to=(self.today - timedelta(days=7)).isoformat()), ['Old vet'])
        self.assertEqual(shown(search='shoe'), ['Shoes'])
        self.assertEqual(shown(provider='abc'), ['Old vet', 'Shoes', 'Vaccination one', 'Vet one'])

    def test_a_bad_margin_in_the_address_is_shown_as_an_error(self):
        self.charge()
        response = self.client.get(URL, {'percent': 'lots'})
        self.assertContains(response, 'from 0 to 500')
        self.assertNotContains(response, 'on the ticked charges')

    def test_no_match_says_so(self):
        self.assertContains(self.client.get(URL), 'No unbilled charges match')

    def test_the_charges_list_links_to_it_and_shows_the_cost(self):
        charge = self.charge()
        self.post([charge], '10')
        response = self.client.get(reverse('charge_list'))
        self.assertContains(response, 'href="/billing/charges/margin/"')
        self.assertContains(response, 'cost £65.00')


class AccessTests(MarginCase):
    def test_view_only_access_to_charges_cannot_open_or_use_it(self):
        charge = self.charge()
        self.client.force_login(make_user_with_access('viewer', charges='view', dashboard='full'))
        self.assertEqual(self.client.get(URL).status_code, 302)
        self.assertEqual(self.post([charge], '10').status_code, 403)
        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal('65.00'))
        self.assertNotContains(self.client.get(reverse('charge_list')), 'Add margin')

    def test_signed_out_goes_to_sign_in(self):
        self.client.logout()
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

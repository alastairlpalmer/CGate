"""Phase 5: training charges (docs/TRAINING_MODULE_PLAN.md 9.6, D8 and D11)."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge
from core.models import Horse, Location, Owner, OwnershipShare, Placement, RateType
from core.roles_testutils import make_user_with_access
from invoicing.models import InvoiceLineItem
from invoicing.services import InvoiceService

from . import board, charges, services
from .models import Activity, Lot, WorkType

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


def _last_month():
    end = timezone.localdate().replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


class Base(TestCase):
    def setUp(self):
        self.start, self.end = _last_month()
        self.today = timezone.localdate()
        self.jo = Owner.objects.create(name='Jo Bloggs', email='jo@example.com')
        self.ali = Owner.objects.create(name='Ali Khan', email='ali@example.com')
        self.location = Location.objects.create(site='Main', name='Top Field')
        self.grass = RateType.objects.create(name='Grass', daily_rate=Decimal('5.00'))
        self.ridden = RateType.objects.create(name='Ridden', daily_rate=Decimal('30.00'), includes_exercise=True)
        self.schooling = WorkType.objects.create(name='Schooling', kind='ridden', effort=2, price=Decimal('25.00'))
        self.hack = WorkType.objects.create(name='Hack', kind='ridden', effort=1)
        self.user = make_user_with_access('boss', training='full', charges='full', horses='view')
        self.client.force_login(self.user)

    def horse(self, name, rate=None, co_owner=None, since=None):
        horse = Horse.objects.create(name=name)
        Placement.objects.create(
            horse=horse, owner=self.jo, location=self.location, rate_type=rate or self.grass,
            start_date=since or self.start - timedelta(days=30),
        )
        share = Decimal('50') if co_owner else Decimal('100')
        OwnershipShare.objects.create(horse=horse, owner=self.jo, share_percentage=share, is_primary_contact=True)
        if co_owner:
            OwnershipShare.objects.create(horse=horse, owner=co_owner, share_percentage=share)
        return horse

    def done(self, horse, work=None, day=None):
        activity = Activity.objects.create(
            horse=horse, date=day or self.end, work_type=work or self.schooling, status=Activity.Status.PLANNED,
        )
        services.mark_done([activity], user=self.user, today=self.today)
        activity.refresh_from_db()
        return activity


class RuleTests(Base):
    def test_done_priced_session_makes_one_charge(self):
        activity = self.done(self.horse('Bramble'))
        charge = activity.extra_charge
        self.assertEqual((charge.amount, charge.owner, charge.charge_type), (Decimal('25.00'), self.jo, 'training'))
        self.assertEqual(charge.description, 'Training - Schooling')
        self.assertEqual(ExtraCharge.objects.count(), 1)
        charges.sync([activity])
        self.assertEqual(ExtraCharge.objects.count(), 1)

    def test_livery_that_includes_exercise_is_never_charged(self):
        activity = self.done(self.horse('Echo', rate=self.ridden))
        self.assertIsNone(activity.extra_charge)
        self.assertFalse(ExtraCharge.objects.exists())

    def test_rate_on_the_date_of_the_work_decides(self):
        horse = self.horse('Moved', rate=self.ridden)
        placement = horse.placements.get()
        placement.end_date = self.end - timedelta(days=5)
        placement.save()
        Placement.objects.create(horse=horse, owner=self.jo, location=self.location, rate_type=self.grass,
                                 start_date=self.end - timedelta(days=4))
        before = self.done(horse, day=self.end - timedelta(days=6))
        after = self.done(horse, day=self.end)
        self.assertIsNone(before.extra_charge)
        self.assertIsNotNone(after.extra_charge)

    def test_unpriced_work_makes_no_charge_and_no_query_cost(self):
        activity = self.done(self.horse('Bramble'), work=self.hack)
        self.assertIsNone(activity.extra_charge)
        self.assertIsNone(activity.cost)

    def test_price_change_does_not_reprice_done_work(self):
        activity = self.done(self.horse('Bramble'))
        self.schooling.price = Decimal('30.00')
        self.schooling.save()
        charges.sync([activity])
        activity.refresh_from_db()
        self.assertEqual(activity.extra_charge.amount, Decimal('25.00'))

    def test_deleted_charge_is_not_made_again(self):
        activity = self.done(self.horse('Bramble'))
        activity.extra_charge.soft_delete()   # as the Charges page does
        charges.sync([activity])
        self.assertEqual(ExtraCharge.objects.count(), 0)
        self.assertEqual(ExtraCharge.all_objects.count(), 1)

    def test_no_owner_makes_no_charge(self):
        horse = Horse.objects.create(name='Stray')
        activity = Activity.objects.create(horse=horse, date=self.end, work_type=self.schooling, status='planned')
        result = services.mark_done([activity], user=self.user, today=self.today)
        self.assertEqual(result.charges['no_owner'], 1)
        self.assertFalse(ExtraCharge.objects.exists())

    def test_charge_edit_on_charges_page_is_kept(self):
        activity = self.done(self.horse('Bramble'))
        charge = activity.extra_charge
        self.assertEqual(charge.linked_health_record(), activity)
        response = self.client.post(reverse('charge_update', args=[charge.pk]), {
            'horse': charge.horse_id, 'owner': charge.owner_id, 'charge_type': 'training',
            'date': charge.date.isoformat(), 'description': charge.description, 'amount': '20.00',
            'split_by_ownership': 'on', 'notes': '',
        })
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['form'].errors)
        activity.refresh_from_db()
        self.assertEqual(activity.cost, Decimal('20.00'))
        charges.sync([activity])
        self.assertEqual(ExtraCharge.objects.get().amount, Decimal('20.00'))


class BoardTests(Base):
    def setUp(self):
        super().setUp()
        self.lot = Lot.objects.create(name='1st lot', usual_work_type=self.schooling)
        self.priced = [self.horse(f'Grass {i}') for i in range(6)]
        self.livery = [self.horse(f'Ridden {i}', rate=self.ridden) for i in range(4)]
        services.add_to_lot(self.lot, self.priced + self.livery)
        board.start_board(self.today, user=self.user, source='lots')
        self.lot_day = self.lot.days.get(date=self.today)

    def test_mark_lot_done_charges_only_horses_without_exercise_livery(self):
        result = board.mark_lot_done(self.lot_day, user=self.user, today=self.today)
        self.assertEqual(result.changed, 10)
        self.assertEqual(result.charges['created'], 6)
        self.assertEqual(
            set(ExtraCharge.objects.values_list('horse__name', flat=True)),
            {h.name for h in self.priced},
        )

    def test_reopen_removes_unbilled_charges(self):
        board.mark_lot_done(self.lot_day, user=self.user, today=self.today)
        board.reopen_lot(self.lot_day, user=self.user)
        self.assertFalse(ExtraCharge.objects.exists())
        self.assertFalse(Activity.objects.filter(cost__isnull=False).exists())

    def test_reopen_is_refused_when_a_charge_is_invoiced(self):
        board.mark_lot_done(self.lot_day, user=self.user, today=self.today)
        charge = ExtraCharge.objects.first()
        charge.invoiced = True
        charge.save()
        with self.assertRaises(board.BoardError) as error:
            board.reopen_lot(self.lot_day, user=self.user)
        self.assertIn('invoice', str(error.exception))
        self.assertEqual(ExtraCharge.objects.count(), 6)

    def test_trainer_without_charges_confirms_and_sees_no_price(self):
        # D11: a Trainer marks the lot done; the charges are still made.
        trainer = make_user_with_access('trainer', training='full', horses='view')
        self.client.force_login(trainer)
        self.client.post(reverse('training_lot_day_action', args=[self.lot_day.pk, 'done']))
        self.assertEqual(ExtraCharge.objects.count(), 6)
        response = self.client.get(reverse('training_setup'))
        self.assertNotContains(response, '25.00')
        self.assertNotContains(response, '£')
        response = self.client.get(reverse('training_work_type_update', args=[self.schooling.pk]))
        self.assertNotIn('price', response.context['form'].fields)


class ViewTests(Base):
    def test_lot_done_warns_about_horses_with_no_owner(self):
        lot = Lot.objects.create(name='1st lot', usual_work_type=self.schooling)
        services.add_to_lot(lot, [Horse.objects.create(name='Stray')])
        board.start_board(self.today, user=self.user, source='lots')
        response = self.client.post(
            reverse('training_lot_day_action', args=[lot.days.get().pk, 'done']), follow=True,
        )
        self.assertContains(response, 'No training charge made for 1 horse')

    def test_price_shows_and_is_set_with_full_charges(self):
        response = self.client.get(reverse('training_setup'))
        self.assertContains(response, '£25.00')
        self.client.post(reverse('training_work_type_update', args=[self.hack.pk]), {
            'name': 'Hack', 'kind': 'ridden', 'effort': 1, 'default_minutes': '', 'price': '12.50', 'position': 0,
        })
        self.hack.refresh_from_db()
        self.assertEqual(self.hack.price, Decimal('12.50'))

    def test_save_without_price_field_keeps_the_price(self):
        self.client.force_login(make_user_with_access('trainer', training='full'))
        self.client.post(reverse('training_work_type_update', args=[self.schooling.pk]), {
            'name': 'Schooling', 'kind': 'ridden', 'effort': 2, 'default_minutes': '', 'position': 0,
        })
        self.schooling.refresh_from_db()
        self.assertEqual(self.schooling.price, Decimal('25.00'))

    def test_record_form_makes_and_moves_the_charge(self):
        horse = self.horse('Bramble')
        self.client.post(reverse('training_activity_create'), {
            'horse': horse.pk, 'date': self.end.isoformat(), 'work_type': self.schooling.pk, 'status': 'done',
        }, **POPUP)
        activity = Activity.objects.get()
        self.assertEqual(activity.extra_charge.amount, Decimal('25.00'))
        # A new work type takes its price: Hack has none, so the charge goes.
        self.client.post(reverse('training_activity_update', args=[activity.pk]), {
            'horse': horse.pk, 'date': self.end.isoformat(), 'work_type': self.hack.pk, 'status': 'done',
        }, **POPUP)
        self.assertFalse(ExtraCharge.objects.exists())

    def test_change_and_delete_are_refused_when_invoiced(self):
        activity = self.done(self.horse('Bramble'))
        ExtraCharge.objects.filter(pk=activity.extra_charge_id).update(invoiced=True)
        response = self.client.post(reverse('training_activity_update', args=[activity.pk]), {
            'horse': activity.horse_id, 'date': self.end.isoformat(), 'work_type': self.hack.pk, 'status': 'done',
        }, **POPUP)
        self.assertContains(response, 'The training charge for Bramble is on'.replace('The', 'the'))
        activity.refresh_from_db()
        self.assertEqual(activity.work_type, self.schooling)
        self.client.post(reverse('training_activity_delete', args=[activity.pk]))
        self.assertTrue(Activity.objects.filter(pk=activity.pk).exists())

    def test_delete_removes_the_unbilled_charge(self):
        activity = self.done(self.horse('Bramble'))
        self.client.post(reverse('training_activity_delete', args=[activity.pk]))
        self.assertFalse(ExtraCharge.objects.exists())

    def test_bulk_done_makes_charges(self):
        horses = [self.horse('A'), self.horse('B', rate=self.ridden)]
        self.client.post(reverse('bulk_health_apply'), {
            'action_type': 'training', 'horse_ids': [h.pk for h in horses], 'date': self.end.isoformat(),
            'work_type': self.schooling.pk, 'status': 'done',
        }, **POPUP)
        self.assertEqual(list(ExtraCharge.objects.values_list('horse__name', flat=True)), ['A'])

    def test_rate_form_has_the_exercise_tick(self):
        user = make_user_with_access('admin2', settings='full')
        self.client.force_login(user)
        response = self.client.get(reverse('rate_type_update', args=[self.grass.pk]))
        self.assertContains(response, 'Includes exercise')


class InvoiceTests(Base):
    def test_reopen_refusal_names_the_invoice(self):
        horse = self.horse('Bramble')
        lot = Lot.objects.create(name='1st lot', usual_work_type=self.schooling)
        services.add_to_lot(lot, [horse])
        board.start_board(self.end, user=self.user, source='lots')
        lot_day = lot.days.get(date=self.end)
        board.mark_lot_done(lot_day, user=self.user, today=self.today)
        invoice = InvoiceService.create_invoice(self.jo, self.start, self.end)
        with self.assertRaises(board.BoardError) as error:
            board.reopen_lot(lot_day, user=self.user)
        self.assertIn(invoice.invoice_number, str(error.exception))

    def test_invoice_line_says_training_and_splits_for_co_owners(self):
        activity = self.done(self.horse('Echo', co_owner=self.ali))
        invoice = InvoiceService.create_invoice(self.jo, self.start, self.end)
        line = invoice.line_items.get(charge=activity.extra_charge)
        self.assertEqual(line.line_type, InvoiceLineItem.LineType.TRAINING)
        self.assertEqual(line.description.split(' - ')[0].strip().split()[-1], 'Training')
        self.assertEqual(line.line_total, Decimal('12.50'))

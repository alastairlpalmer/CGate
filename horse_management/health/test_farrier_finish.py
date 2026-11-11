"""Farrier page, step 4: Finish visit (docs/FARRIER_PAGE_PLAN.md, sections 2 and 8).

Finish bills every line through sync_record_charge, once, and sends the
farrier a short summary (so they can move on) and the yard a summary
with who each line bills.
"""

import importlib
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import ExtraCharge, ServiceItem, ServiceProvider
from core.models import BusinessSettings, Horse, Location, Owner, OwnershipShare, Placement, RateType, Role
from core.roles_testutils import assign_role
from health import farrier
from health.models import ProviderVisit
from invoicing.services import InvoiceService

User = get_user_model()
seed_migration = importlib.import_module('core.migrations.0053_seed_farrier_role')


def make_farrier(username='farrier', name='Sam Shoe', email='sam@example.com', user_email=''):
    seed_migration.seed_farrier_role(django_apps, None)
    user = User.objects.create_user(username=username, password='pw', email=user_email)
    assign_role(user, Role.objects.get(name='Farrier'))
    provider = ServiceProvider.objects.create(name=name, provider_type='farrier', user=user, email=email)
    return user, provider


class FinishBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        business = BusinessSettings.get_settings()
        business.email = 'yard@example.com'
        business.save()
        self.user, self.provider = make_farrier()
        self.full_set = ServiceItem.objects.create(
            name='Full set', category='farrier', price=Decimal('95.00'),
            farrier_work='full_set', provider=self.provider,
        )
        self.trim = ServiceItem.objects.create(
            name='Trim', category='farrier', price=Decimal('35.00'),
            farrier_work='trim', provider=self.provider,
        )
        self.field = Location.objects.create(name='Top Field', site='Main')
        self.rate = RateType.objects.create(name='Grass', daily_rate=5)
        self.jo = Owner.objects.create(name='Jo Bloggs', email='jo@example.com')
        self.amy = Owner.objects.create(name='Amy Smith', email='amy@example.com')
        self.bramble = self._horse('Bramble', [(self.jo, 100)])
        self.juno = self._horse('Juno', [(self.jo, 50), (self.amy, 50)])
        self.client.force_login(self.user)

    def _horse(self, name, shares):
        horse = Horse.objects.create(name=name)
        for i, (owner, pct) in enumerate(shares):
            OwnershipShare.objects.create(horse=horse, owner=owner, share_percentage=pct, is_primary_contact=(i == 0))
        Placement.objects.create(
            horse=horse, owner=shares[0][0], location=self.field, rate_type=self.rate,
            start_date=self.today - timedelta(days=60),
        )
        return horse

    def add(self, horse, item, **extra):
        return self.client.post(
            reverse('farrier:horse_add', args=[horse.pk]), {'item': item.pk, **extra}, HTTP_HX_REQUEST='true',
        )

    def finish(self):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse('farrier:visit_finish'))


class FinishBillingTests(FinishBase):
    def test_finish_bills_each_line_and_closes_the_visit(self):
        self.add(self.bramble, self.full_set)
        self.add(self.juno, self.trim)
        response = self.finish()
        visit = ProviderVisit.objects.get(provider=self.provider)
        self.assertRedirects(response, f"{reverse('farrier:visit_detail', args=[visit.pk])}?done=1",
                             fetch_redirect_response=False)
        self.assertEqual(visit.status, 'finished')
        self.assertIsNotNone(visit.finished_at)
        charges = ExtraCharge.objects.order_by('horse__name')
        self.assertEqual(
            [(c.horse.name, c.amount, c.charge_type, c.service_provider) for c in charges],
            [('Bramble', Decimal('95.00'), 'farrier', self.provider),
             ('Juno', Decimal('35.00'), 'farrier', self.provider)],
        )
        for line in visit.farrier_visits.all():
            self.assertIsNotNone(line.extra_charge)

    def test_co_owned_horse_is_split_on_the_invoices(self):
        self.add(self.juno, self.trim)
        self.finish()
        charge = ExtraCharge.objects.get(horse=self.juno)
        self.assertTrue(charge.split_by_ownership)
        start = self.today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        amounts = {}
        for owner in (self.jo, self.amy):
            invoice = InvoiceService.create_invoice(owner, start, end)
            amounts[owner.name] = sum(
                (item.line_total for item in invoice.line_items.all() if item.charge_id == charge.pk),
                Decimal('0.00'),
            )
        self.assertEqual(amounts, {'Jo Bloggs': Decimal('17.50'), 'Amy Smith': Decimal('17.50')})

    def test_horse_with_no_owner_is_kept_not_billed(self):
        lonely = Horse.objects.create(name='Stray')
        self.add(lonely, self.trim)
        self.add(self.bramble, self.full_set)
        self.finish()
        visit = ProviderVisit.objects.get(provider=self.provider)
        self.assertEqual(visit.status, 'finished')
        self.assertTrue(visit.farrier_visits.filter(horse=lonely, extra_charge__isnull=True).exists())
        self.assertEqual(ExtraCharge.objects.count(), 1)

    def test_a_free_line_makes_no_charge(self):
        self.add(self.bramble, self.full_set, price='0')
        self.finish()
        self.assertFalse(ExtraCharge.objects.exists())

    def test_second_finish_bills_nothing_more(self):
        self.add(self.bramble, self.full_set)
        self.finish()
        response = self.finish()
        visit = ProviderVisit.objects.get(provider=self.provider)
        self.assertRedirects(response, f"{reverse('farrier:visit_detail', args=[visit.pk])}?done=1",
                             fetch_redirect_response=False)
        self.assertEqual(ExtraCharge.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_empty_visit_cannot_be_finished(self):
        self.add(self.bramble, self.full_set)
        self.client.post(reverse('farrier:horse_remove', args=[self.bramble.pk]), HTTP_HX_REQUEST='true')
        response = self.finish()
        self.assertRedirects(response, reverse('farrier:today'), fetch_redirect_response=False)
        self.assertTrue(ProviderVisit.objects.get(provider=self.provider).is_open)
        self.assertEqual(len(mail.outbox), 0)

    def test_finish_with_no_visit_goes_back_to_today(self):
        response = self.finish()
        self.assertRedirects(response, reverse('farrier:today'), fetch_redirect_response=False)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse('farrier:visit_finish')).status_code, 405)

    def test_a_finished_visit_is_closed_to_changes(self):
        self.add(self.bramble, self.full_set)
        self.finish()
        self.client.post(reverse('farrier:horse_remove', args=[self.bramble.pk]), HTTP_HX_REQUEST='true')
        self.assertTrue(ExtraCharge.objects.filter(horse=self.bramble).exists())
        self.add(self.bramble, self.trim)
        self.assertEqual(ProviderVisit.objects.filter(provider=self.provider).count(), 2)
        self.assertEqual(ExtraCharge.objects.get(horse=self.bramble).amount, Decimal('95.00'))

    def test_finishing_does_not_touch_another_farriers_visit(self):
        rival_user, rival = make_farrier(username='rival', name='Rival Forge', email='rival@example.com')
        rival_trim = ServiceItem.objects.create(name='Trim', category='farrier', price=Decimal('30.00'),
                                                farrier_work='trim', provider=rival)
        self.add(self.bramble, self.full_set)
        self.client.force_login(rival_user)
        self.add(self.juno, rival_trim)
        self.client.force_login(self.user)
        self.finish()
        self.assertTrue(ProviderVisit.objects.get(provider=rival).is_open)
        self.assertFalse(ExtraCharge.objects.filter(horse=self.juno).exists())


class FinishMessageTests(FinishBase):
    def _finish_two(self):
        self.add(self.bramble, self.full_set, note='Loose clench LF')
        self.add(self.juno, self.trim)
        return self.finish()

    def test_farrier_gets_a_summary_to_move_on(self):
        self._finish_two()
        to_farrier = [m for m in mail.outbox if m.to == ['sam@example.com']]
        self.assertEqual(len(to_farrier), 1)
        msg = to_farrier[0]
        self.assertEqual(msg.subject, 'Visit sent: 2 horses, £130.00')
        for text in ('Bramble', 'Full Set', 'Juno', 'Trim Only', '£95.00', '£130.00', 'Loose clench LF',
                     'You do not need to send a bill'):
            self.assertIn(text, msg.body)
        due = (self.today + timedelta(weeks=6)).strftime('%-d %b')
        self.assertIn(due, msg.body)
        self.assertEqual(len(msg.alternatives), 1)

    def test_farrier_summary_never_names_owners(self):
        self._finish_two()
        msg = next(m for m in mail.outbox if m.to == ['sam@example.com'])
        for text in ('Jo Bloggs', 'Amy Smith', 'jo@example.com'):
            self.assertNotIn(text, msg.body)
            self.assertNotIn(text, msg.alternatives[0][0])

    def test_yard_gets_a_summary_with_who_pays(self):
        stray = Horse.objects.create(name='Stray')
        self.add(stray, self.trim)
        self._finish_two()
        msg = next(m for m in mail.outbox if m.to == ['yard@example.com'])
        self.assertIn('Sam Shoe', msg.subject)
        self.assertIn('Jo Bloggs', msg.body)
        self.assertIn('No owner: not billed', msg.body)
        self.assertIn('could not be billed', msg.body)

    def test_login_email_is_used_when_the_provider_has_none(self):
        self.provider.email = ''
        self.provider.save()
        self.user.email = 'sam.login@example.com'
        self.user.save()
        self._finish_two()
        self.assertTrue(any(m.to == ['sam.login@example.com'] for m in mail.outbox))

    def test_no_farrier_address_still_finishes(self):
        self.provider.email = ''
        self.provider.save()
        self._finish_two()
        self.assertEqual([m.to for m in mail.outbox], [['yard@example.com']])
        visit = ProviderVisit.objects.get(provider=self.provider)
        response = self.client.get(f"{reverse('farrier:visit_detail', args=[visit.pk])}?done=1")
        self.assertContains(response, 'Ask the yard to add your email address')

    def test_no_yard_address_still_sends_the_farrier_summary(self):
        business = BusinessSettings.get_settings()
        business.email = ''
        business.save()
        self._finish_two()
        self.assertEqual([m.to for m in mail.outbox], [['sam@example.com']])

    def test_a_failed_email_does_not_undo_the_visit(self):
        self.add(self.bramble, self.full_set)
        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('smtp down')):
            response = self.finish()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ProviderVisit.objects.get(provider=self.provider).status, 'finished')
        self.assertEqual(ExtraCharge.objects.count(), 1)

    def test_done_screen_confirms_and_names_the_address(self):
        response = self._finish_two()
        page = self.client.get(response['Location'])
        self.assertContains(page, 'Sent to the yard')
        self.assertContains(page, 'A summary is on its way to sam@example.com')
        self.assertContains(page, '2 horses')
        self.assertNotContains(page, 'Jo Bloggs')

    def test_review_screen_offers_finish_with_the_total(self):
        self.add(self.bramble, self.full_set)
        visit = ProviderVisit.objects.get(provider=self.provider)
        page = self.client.get(reverse('farrier:visit_detail', args=[visit.pk]))
        self.assertContains(page, 'Finish visit · send 1 line (£95.00) to the yard')

    def test_send_visit_emails_reports_both(self):
        self.add(self.bramble, self.full_set)
        with self.captureOnCommitCallbacks(execute=False):
            visit, _ = farrier.finish_visit(self.provider, self.user)
        self.assertEqual(farrier.send_visit_emails(visit.pk), (True, True))

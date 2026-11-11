"""Farrier page, step 6: the yard's side (docs/FARRIER_PAGE_PLAN.md, section 6).

The visit page with who each line bills, Mark as checked, the monthly
statement with Record payment to farrier, the QR code sheet, and linking
a farrier's login to their provider record.
"""

import importlib
from datetime import timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.forms import ServiceProviderForm
from billing.models import ServiceItem, ServiceProvider, YardCost
from core.models import BusinessSettings, Horse, Location, Owner, OwnershipShare, Placement, RateType, Role
from core.roles_testutils import assign_role, make_admin, make_user_with_access
from health import farrier
from health.models import ProviderVisit
from invoicing.services import InvoiceService

User = get_user_model()
seed_migration = importlib.import_module('core.migrations.0053_seed_farrier_role')


def farrier_role():
    seed_migration.seed_farrier_role(django_apps, None)
    return Role.objects.get(name='Farrier')


def make_farrier(username='farrier', name='Sam Shoe'):
    user = User.objects.create_user(username=username, password='pw')
    assign_role(user, farrier_role())
    provider = ServiceProvider.objects.create(name=name, provider_type='farrier', user=user, email='sam@example.com')
    return user, provider


class YardBase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        business = BusinessSettings.get_settings()
        business.email = 'yard@example.com'
        business.save()
        self.farrier_user, self.provider = make_farrier()
        self.full_set = ServiceItem.objects.create(
            name='Full set', category='farrier', price=Decimal('95.00'), farrier_work='full_set', provider=self.provider,
        )
        self.trim = ServiceItem.objects.create(
            name='Trim', category='farrier', price=Decimal('35.00'), farrier_work='trim', provider=self.provider,
        )
        field = Location.objects.create(name='Top Field', site='Main')
        rate = RateType.objects.create(name='Grass', daily_rate=5)
        self.jo = Owner.objects.create(name='Jo Bloggs')
        self.amy = Owner.objects.create(name='Amy Smith')
        self.bramble = Horse.objects.create(name='Bramble')
        OwnershipShare.objects.create(horse=self.bramble, owner=self.jo, share_percentage=100, is_primary_contact=True)
        self.juno = Horse.objects.create(name='Juno')
        OwnershipShare.objects.create(horse=self.juno, owner=self.jo, share_percentage=50, is_primary_contact=True)
        OwnershipShare.objects.create(horse=self.juno, owner=self.amy, share_percentage=50)
        self.stray = Horse.objects.create(name='Stray')
        for horse, owner in ((self.bramble, self.jo), (self.juno, self.jo)):
            Placement.objects.create(horse=horse, owner=owner, location=field, rate_type=rate,
                                     start_date=self.today - timedelta(days=60))
        self.admin = make_admin(username='boss')

    def record_visit(self, lines, finish=True):
        for horse, item in lines:
            farrier.record_line(self.provider, self.farrier_user, horse, item=item)
        if finish:
            with self.captureOnCommitCallbacks(execute=True):
                visit, _ = farrier.finish_visit(self.provider, self.farrier_user)
            return visit
        return farrier.open_visit(self.provider)


class VisitPageTests(YardBase):
    def test_lines_show_who_pays(self):
        visit = self.record_visit([(self.bramble, self.full_set), (self.juno, self.trim), (self.stray, self.trim)])
        self.client.force_login(self.admin)
        page = self.client.get(reverse('provider_visit_detail', args=[visit.pk]))
        self.assertContains(page, 'On the next invoice: Jo Bloggs')
        self.assertContains(page, 'Jo Bloggs (50%), Amy Smith (50%)')
        self.assertContains(page, 'No owner: not billed')
        self.assertContains(page, 'Mark as checked')
        self.assertContains(page, '£165.00')

    def test_invoiced_line_names_the_invoice(self):
        visit = self.record_visit([(self.bramble, self.full_set)])
        start = self.today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        invoice = InvoiceService.create_invoice(self.jo, start, end)
        self.client.force_login(self.admin)
        page = self.client.get(reverse('provider_visit_detail', args=[visit.pk]))
        self.assertContains(page, f'On invoice {invoice.invoice_number}')

    def test_open_visit_is_shown_as_not_sent(self):
        visit = self.record_visit([(self.bramble, self.full_set)], finish=False)
        self.client.force_login(self.admin)
        page = self.client.get(reverse('provider_visit_detail', args=[visit.pk]))
        self.assertContains(page, 'Not sent yet')
        self.assertNotContains(page, 'Mark as checked')

    def test_list_and_filters(self):
        checked = self.record_visit([(self.bramble, self.full_set)])
        farrier_yard_check = reverse('provider_visit_check', args=[checked.pk])
        self.client.force_login(self.admin)
        self.client.post(farrier_yard_check)
        unchecked = self.record_visit([(self.juno, self.trim)])
        open_visit = self.record_visit([(self.bramble, self.trim)], finish=False)
        url = reverse('provider_visit_list')
        self.assertEqual(list(self.client.get(url, {'status': 'unchecked'}).context['visits']), [unchecked])
        self.assertEqual(list(self.client.get(url, {'status': 'open'}).context['visits']), [open_visit])
        self.assertEqual(len(self.client.get(url, {'status': 'unpaid'}).context['visits']), 2)
        self.assertEqual(len(self.client.get(url, {'provider': self.provider.pk}).context['visits']), 3)

    def test_staff_without_charges_cannot_see_visits(self):
        visit = self.record_visit([(self.bramble, self.full_set)])
        groom = make_user_with_access(username='groom', dashboard='full', health='full')
        self.client.force_login(groom)
        self.assertEqual(self.client.get(reverse('provider_visit_list')).status_code, 302)
        self.assertEqual(self.client.get(reverse('provider_visit_detail', args=[visit.pk])).status_code, 302)

    def test_farrier_login_cannot_reach_the_yard_pages(self):
        visit = self.record_visit([(self.bramble, self.full_set)])
        self.client.force_login(self.farrier_user)
        for url in (reverse('provider_visit_list'), reverse('provider_visit_detail', args=[visit.pk]),
                    reverse('provider_statement', args=[self.provider.pk, self.today.year, self.today.month]),
                    reverse('farrier_qr')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url)['Location'], '/farrier/')

    def test_yard_email_links_to_the_visit_page(self):
        self.record_visit([(self.bramble, self.full_set)])
        visit = ProviderVisit.objects.get()
        msg = next(m for m in mail.outbox if m.to == ['yard@example.com'])
        self.assertIn(reverse('provider_visit_detail', args=[visit.pk]), msg.alternatives[0][0])


class CheckTests(YardBase):
    def test_check_stops_farrier_changes(self):
        visit = self.record_visit([(self.bramble, self.full_set)])
        self.assertTrue(farrier.can_change_finished(visit))
        self.client.force_login(self.admin)
        response = self.client.post(reverse('provider_visit_check', args=[visit.pk]))
        self.assertRedirects(response, reverse('provider_visit_detail', args=[visit.pk]), fetch_redirect_response=False)
        visit.refresh_from_db()
        self.assertIsNotNone(visit.checked_at)
        self.assertEqual(visit.checked_by, self.admin)
        self.assertFalse(farrier.can_change_finished(visit))

    def test_checking_twice_keeps_the_first_check(self):
        visit = self.record_visit([(self.bramble, self.full_set)])
        self.client.force_login(self.admin)
        self.client.post(reverse('provider_visit_check', args=[visit.pk]))
        first = ProviderVisit.objects.get(pk=visit.pk).checked_at
        self.client.post(reverse('provider_visit_check', args=[visit.pk]))
        self.assertEqual(ProviderVisit.objects.get(pk=visit.pk).checked_at, first)

    def test_open_visit_cannot_be_checked(self):
        visit = self.record_visit([(self.bramble, self.full_set)], finish=False)
        self.client.force_login(self.admin)
        self.client.post(reverse('provider_visit_check', args=[visit.pk]))
        self.assertIsNone(ProviderVisit.objects.get(pk=visit.pk).checked_at)

    def test_view_only_staff_cannot_check(self):
        visit = self.record_visit([(self.bramble, self.full_set)])
        viewer = make_user_with_access(username='viewer', dashboard='full', charges='view')
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(reverse('provider_visit_check', args=[visit.pk])).status_code, 403)
        self.assertIsNone(ProviderVisit.objects.get(pk=visit.pk).checked_at)

    def test_check_needs_post(self):
        visit = self.record_visit([(self.bramble, self.full_set)])
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('provider_visit_check', args=[visit.pk])).status_code, 405)


class StatementTests(YardBase):
    def statement_url(self, name='provider_statement', when=None):
        when = when or self.today
        return reverse(name, args=[self.provider.pk, when.year, when.month])

    def test_statement_totals(self):
        self.record_visit([(self.bramble, self.full_set)])
        self.record_visit([(self.juno, self.trim)])
        self.record_visit([(self.bramble, self.trim)], finish=False)
        self.client.force_login(self.admin)
        page = self.client.get(self.statement_url())
        stmt = page.context['statement']
        self.assertEqual((stmt.total, stmt.paid_total, stmt.unpaid_total), (Decimal('130.00'), 0, Decimal('130.00')))
        self.assertEqual(len(stmt.open_visits), 1)
        self.assertContains(page, 'Record payment to farrier')

    def test_record_payment_makes_one_yard_cost(self):
        first = self.record_visit([(self.bramble, self.full_set)])
        second = self.record_visit([(self.juno, self.trim)])
        self.client.force_login(self.admin)
        self.client.post(self.statement_url('provider_statement_pay'))
        cost = YardCost.objects.get()
        self.assertEqual(
            (cost.category, cost.amount, cost.vat_amount, cost.supplier, cost.date),
            ('professional', Decimal('130.00'), Decimal('0.00'), 'Sam Shoe', self.today),
        )
        self.assertIn('2 visits', cost.description)
        for visit in (first, second):
            visit.refresh_from_db()
            self.assertEqual((visit.paid_on, visit.yard_cost), (self.today, cost))

    def test_second_payment_finds_nothing(self):
        self.record_visit([(self.bramble, self.full_set)])
        self.client.force_login(self.admin)
        self.client.post(self.statement_url('provider_statement_pay'))
        self.client.post(self.statement_url('provider_statement_pay'))
        self.assertEqual(YardCost.objects.count(), 1)

    def test_paid_visit_shows_paid_to_the_farrier(self):
        visit = self.record_visit([(self.bramble, self.full_set)])
        self.client.force_login(self.admin)
        self.client.post(self.statement_url('provider_statement_pay'))
        self.client.force_login(self.farrier_user)
        page = self.client.get(reverse('farrier:visit_detail', args=[visit.pk]))
        self.assertContains(page, 'Paid by the yard on')

    def test_other_months_and_open_visits_are_not_paid(self):
        last_month = self.record_visit([(self.bramble, self.full_set)])
        ProviderVisit.objects.filter(pk=last_month.pk).update(date=self.today.replace(day=1) - timedelta(days=5))
        open_visit = self.record_visit([(self.juno, self.trim)], finish=False)
        self.record_visit([(self.bramble, self.trim)], finish=False)
        self.client.force_login(self.admin)
        self.client.post(self.statement_url('provider_statement_pay'))
        self.assertFalse(YardCost.objects.exists())
        last_month.refresh_from_db()
        open_visit.refresh_from_db()
        self.assertIsNone(last_month.paid_on)
        self.assertIsNone(open_visit.paid_on)

    def test_view_only_costs_cannot_pay(self):
        self.record_visit([(self.bramble, self.full_set)])
        viewer = make_user_with_access(username='viewer', dashboard='full', costs='view')
        self.client.force_login(viewer)
        page = self.client.get(self.statement_url())
        self.assertNotContains(page, 'Record payment to farrier')
        self.assertEqual(self.client.post(self.statement_url('provider_statement_pay')).status_code, 403)
        self.assertFalse(YardCost.objects.exists())

    def test_bad_month_goes_back_to_the_list(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('provider_statement', args=[self.provider.pk, 2026, 13]))
        self.assertRedirects(response, reverse('provider_visit_list'), fetch_redirect_response=False)


class QrAndLoginLinkTests(YardBase):
    def test_qr_sheet(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse('farrier_qr'))
        self.assertContains(page, '<svg')
        self.assertContains(page, 'http://testserver/farrier/')

    def test_qr_needs_settings(self):
        user = make_user_with_access(username='groom', dashboard='full', charges='full')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('farrier_qr')).status_code, 302)

    def test_provider_form_offers_only_free_farrier_logins(self):
        new_login = User.objects.create_user(username='newfarrier', password='pw')
        assign_role(new_login, farrier_role())
        form = ServiceProviderForm()
        offered = set(form.fields['user'].queryset.values_list('username', flat=True))
        self.assertEqual(offered, {'newfarrier'})
        self.assertNotIn('boss', offered)
        own = ServiceProviderForm(instance=self.provider)
        self.assertIn('farrier', set(own.fields['user'].queryset.values_list('username', flat=True)))

    def test_link_a_login_on_the_provider_form(self):
        new_login = User.objects.create_user(username='newfarrier', password='pw')
        assign_role(new_login, farrier_role())
        self.client.force_login(self.admin)
        self.client.post(reverse('provider_create'), {
            'name': 'New Forge', 'provider_type': 'farrier', 'email': 'new@example.com',
            'is_active': 'on', 'user': new_login.pk,
        })
        provider = ServiceProvider.objects.get(name='New Forge')
        self.assertEqual(provider.user, new_login)
        self.assertEqual(ServiceProvider.for_user(new_login), provider)

    def test_a_staff_login_cannot_be_linked(self):
        form = ServiceProviderForm(data={
            'name': 'Sneaky', 'provider_type': 'farrier', 'is_active': 'on', 'user': self.admin.pk,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('user', form.errors)

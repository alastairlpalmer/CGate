"""The audit trail: record history, account events and the pages that show
them. docs/LAUNCH_READINESS_PLAN.md, P1 work package 2."""

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core import audit, mfa
from core.models import AuditEvent, BusinessSettings, Owner, Role, UserRole
from core.roles_testutils import make_admin, make_user_with_access
from invoicing.models import Invoice, Payment

User = get_user_model()


class RecordHistoryTests(TestCase):
    def setUp(self):
        self.admin = make_admin('auditor', email='auditor@example.com')
        self.client.force_login(self.admin)
        self.owner = Owner.objects.create(name="Jo O'Brien", email='jo@example.com', phone='0111')

    def test_owner_edit_records_who_and_what(self):
        self.client.post(reverse('owner_update', args=[self.owner.pk]), {
            'name': "Jo O'Brien", 'email': 'jo@example.com', 'phone': '0222',
            'address': '', 'account_code': '', 'notes': '',
        })
        latest = self.owner.history.first()
        self.assertEqual(latest.history_user, self.admin)
        entries = audit.history_for(self.owner)
        self.assertEqual(entries[0].who, 'auditor@example.com')
        changes = {c.field: (c.old, c.new) for c in entries[0].changes}
        self.assertEqual(changes['Phone'], ('0111', '0222'))

    def test_owner_page_shows_history(self):
        self.owner.phone = '0999'
        self.owner.save()
        response = self.client.get(reverse('owner_detail', args=[self.owner.pk]))
        self.assertContains(response, 'History')
        self.assertContains(response, '0999')

    def test_deleted_payment_is_still_on_the_invoice_history(self):
        invoice = Invoice.objects.create(
            owner=self.owner, invoice_number='AUD-1',
            period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
            subtotal=Decimal('100'), total=Decimal('100'), status=Invoice.Status.SENT,
        )
        payment = Payment.objects.create(invoice=invoice, date=date(2026, 10, 1), amount=Decimal('40'))
        self.client.post(reverse('payment_delete', args=[payment.pk]))
        self.assertFalse(Payment.objects.filter(pk=payment.pk).exists())
        deleted = Payment.history.filter(id=payment.pk, history_type='-').get()
        self.assertEqual(deleted.history_user, self.admin)
        response = self.client.get(reverse('invoice_detail', args=[invoice.pk]))
        self.assertContains(response, 'Deleted payment')

    def test_invoice_numbers_do_not_flood_business_settings_history(self):
        settings_row = BusinessSettings.get_settings()
        before = settings_row.history.count()
        for _ in range(3):
            settings_row.get_next_invoice_number()
        self.assertEqual(settings_row.history.count(), before)

    def test_bank_details_change_is_recorded(self):
        settings_row = BusinessSettings.get_settings()
        settings_row.bank_details = 'Sort 00-00-00'
        settings_row.save()
        entry = audit.history_for(settings_row)[0]
        self.assertIn('Bank details', [c.field for c in entry.changes])

    def test_role_assignment_change_names_the_roles(self):
        groom = Role.objects.create(name='Groom')
        office = Role.objects.create(name='Office')
        user = User.objects.create_user(username='staff', email='staff@example.com')
        UserRole.objects.create(user=user, role=groom)
        self.client.post(reverse('user_update', args=[user.pk]), {
            'save_details': '1', 'first_name': '', 'last_name': '',
            'email': 'staff@example.com', 'role': office.pk,
        })
        response = self.client.get(reverse('user_update', args=[user.pk]))
        self.assertContains(response, 'Groom')
        self.assertContains(response, 'Office')


class BaselineTests(TestCase):
    def test_start_audit_trail_runs_once_and_stays_out_of_the_log(self):
        owner = Owner.objects.create(name='Before')
        Owner.history.all().delete()  # As if the row predates the audit trail.
        call_command('start_audit_trail', stdout=StringIO())
        call_command('start_audit_trail', stdout=StringIO())
        self.assertEqual(owner.history.count(), 1)
        self.assertEqual(audit.history_for(owner), [])
        entries, _ = audit.timeline(category='owners')
        self.assertEqual(entries, [])

        owner.name = 'After'
        owner.save()
        [entry] = audit.history_for(owner)
        self.assertEqual([(c.old, c.new) for c in entry.changes], [('Before', 'After')])


class AccountEventTests(TestCase):
    def setUp(self):
        self.admin = make_admin('evadmin', email='evadmin@example.com')
        self.target = make_user_with_access('evuser', dashboard='full')
        self.target.email = 'evuser@example.com'
        self.target.save()
        self.client.force_login(self.admin)

    def _actions(self, user):
        return list(AuditEvent.objects.filter(target_user=user).values_list('action', flat=True))

    def test_sign_in_is_recorded_with_ip(self):
        Client(REMOTE_ADDR='203.0.113.9').post(
            reverse('login'), {'username': 'evuser@example.com', 'password': 'pw'},
        )
        event = AuditEvent.objects.filter(action=AuditEvent.Action.SIGNED_IN, target_user=self.target).get()
        self.assertEqual(event.ip_address, '203.0.113.9')

    def test_admin_actions_on_a_user_are_recorded(self):
        url = reverse('user_update', args=[self.target.pk])
        self.client.post(url, {'set_password': '1', 'password1': 'a-long-new-password', 'password2': 'a-long-new-password'})
        self.client.post(url, {'sign_out_everywhere': '1'})
        self.client.post(url, {'toggle_active': '1'})
        actions = self._actions(self.target)
        self.assertIn(AuditEvent.Action.PASSWORD_SET_BY_ADMIN, actions)
        self.assertIn(AuditEvent.Action.SIGNED_OUT_EVERYWHERE, actions)
        self.assertIn(AuditEvent.Action.USER_DEACTIVATED, actions)
        self.assertTrue(all(
            e.actor == self.admin for e in AuditEvent.objects.filter(
                target_user=self.target, action=AuditEvent.Action.PASSWORD_SET_BY_ADMIN,
            )
        ))

    def test_detail_change_lists_what_changed(self):
        role = self.target.role_assignment.role
        self.client.post(reverse('user_update', args=[self.target.pk]), {
            'save_details': '1', 'first_name': 'Eve', 'last_name': '',
            'email': 'evuser@example.com', 'role': role.pk,
        })
        event = AuditEvent.objects.get(action=AuditEvent.Action.USER_UPDATED, target_user=self.target)
        self.assertIn('First name: — → Eve', event.detail)

    def test_mfa_reset_by_admin_is_recorded(self):
        device = mfa.start_setup(self.target)
        from core.tests.test_account_security import totp_code
        mfa.confirm_setup(self.target, device, totp_code(device))
        self.client.post(reverse('user_update', args=[self.target.pk]), {'mfa_reset': '1'})
        self.assertIn(AuditEvent.Action.MFA_RESET, self._actions(self.target))

    def test_invited_user_is_recorded(self):
        role = Role.objects.create(name='Groom')
        self.client.post(reverse('user_create'), {
            'first_name': 'New', 'last_name': '', 'email': 'new@example.com',
            'role': role.pk, 'password1': '', 'password2': '',
        })
        new = User.objects.get(email='new@example.com')
        event = AuditEvent.objects.get(target_user=new)
        self.assertEqual(event.action, AuditEvent.Action.USER_INVITED)
        self.assertEqual(event.actor, self.admin)

    def test_user_page_shows_account_events(self):
        self.client.post(reverse('user_update', args=[self.target.pk]), {'sign_out_everywhere': '1'})
        response = self.client.get(reverse('user_update', args=[self.target.pk]))
        self.assertContains(response, 'Signed out everywhere')

    def test_a_failed_write_does_not_stop_the_action(self):
        with mock.patch.object(AuditEvent.objects, 'create', side_effect=RuntimeError('db')):
            response = self.client.post(
                reverse('user_update', args=[self.target.pk]), {'sign_out_everywhere': '1'},
            )
        self.assertEqual(response.status_code, 302)

    def test_label_survives_the_user_being_deleted(self):
        audit.record(AuditEvent.Action.LOCKOUT_CLEARED, actor=self.admin, target=self.target)
        self.target.delete()
        event = AuditEvent.objects.get(action=AuditEvent.Action.LOCKOUT_CLEARED)
        self.assertIsNone(event.target_user)
        self.assertEqual(event.target_label, 'evuser@example.com')


class AuditLogPageTests(TestCase):
    def setUp(self):
        self.admin = make_admin('logadmin', email='logadmin@example.com')

    def test_administrator_sees_the_log(self):
        self.client.force_login(self.admin)
        Owner.objects.create(name='Logged Owner')
        response = self.client.get(reverse('audit_log'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Created owner')
        self.assertContains(response, 'Logged Owner')
        self.assertContains(response, 'Signed in')

    def test_settings_links_to_it_for_administrators(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('app_settings')), reverse('audit_log'))

    def test_user_manager_who_is_not_an_administrator_is_refused(self):
        manager = make_user_with_access('mgr', users='full', settings='full')
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse('audit_log')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('app_settings')), reverse('audit_log'))

    def test_category_filter(self):
        self.client.force_login(self.admin)
        Owner.objects.create(name='Filter Owner')
        response = self.client.get(reverse('audit_log'), {'category': 'account'})
        self.assertNotContains(response, 'Filter Owner')
        self.assertContains(response, 'Signed in')

    def test_older_pages(self):
        now = timezone.now()
        AuditEvent.objects.bulk_create([
            AuditEvent(action=AuditEvent.Action.SIGNED_IN, actor_label=f'user{i}@example.com',
                       created_at=now - timedelta(minutes=i + 10))
            for i in range(60)
        ])
        first, cursor = audit.timeline(category='account')
        self.assertEqual(len(first), 50)
        self.assertIsNotNone(cursor)
        second, cursor2 = audit.timeline(category='account', before=cursor)
        self.assertEqual(len(second), 10)
        self.assertIsNone(cursor2)
        self.assertFalse({e.who for e in first} & {e.who for e in second})

        self.client.force_login(self.admin)
        response = self.client.get(reverse('audit_log'), {'category': 'account'})
        self.assertContains(response, 'Older')

    def test_bad_cursor_is_ignored(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('audit_log'), {'before': 'not-a-date', 'category': 'nope'})
        self.assertEqual(response.status_code, 200)

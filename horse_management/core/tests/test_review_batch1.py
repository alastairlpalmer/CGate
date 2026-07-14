"""Regression tests for review batch 1: access-control fixes.

1. Bulk invoice push to Xero must honour the Xero feature gate (it was
   bypassable by any invoices=full role).
2. Editing a legacy email-less account must not overwrite its username
   with the new email (locked the createsuperuser 'admin' out).
3. The seeded Viewer role keeps the read access non-staff users had before
   the Role Suite: charge list and the read-only Xero status endpoint.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.forms import UserUpdateForm
from core.models import Role
from core.roles_testutils import make_user_with_access, make_viewer


class BulkXeroPushGateTests(TestCase):
    def test_xero_hidden_role_gets_403_on_bulk_push(self):
        user = make_user_with_access(username='books', invoices='full')
        self.client.force_login(user)
        response = self.client.post(
            reverse('invoice_bulk_action'),
            {'action': 'push_xero', 'invoice_ids': []},
        )
        self.assertEqual(response.status_code, 403)

    def test_xero_full_role_passes_the_gate(self):
        user = make_user_with_access(
            username='books2', invoices='full', xero='full',
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse('invoice_bulk_action'),
            {'action': 'push_xero', 'invoice_ids': []},
        )
        # Past the gate: empty selection redirects with a message, not 403
        self.assertEqual(response.status_code, 302)

    def test_other_bulk_actions_unaffected_by_xero_gate(self):
        user = make_user_with_access(username='books3', invoices='full')
        self.client.force_login(user)
        response = self.client.post(
            reverse('invoice_bulk_action'),
            {'action': 'mark_paid', 'invoice_ids': []},
        )
        self.assertEqual(response.status_code, 302)


class UserUpdateFormUsernameTests(TestCase):
    def _role(self):
        role, _ = Role.objects.get_or_create(name='Anything')
        return role

    def test_legacy_email_less_account_keeps_username(self):
        admin = get_user_model().objects.create_user(
            username='admin', email='', password='pw',
        )
        form = UserUpdateForm(
            {
                'first_name': 'Al',
                'last_name': '',
                'email': 'admin@yard.com',
                'role': self._role().pk,
            },
            instance=admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        admin.refresh_from_db()
        self.assertEqual(admin.username, 'admin')
        self.assertEqual(admin.email, 'admin@yard.com')

    def test_email_based_account_username_follows_email(self):
        user = get_user_model().objects.create_user(
            username='old@yard.com', email='old@yard.com', password='pw',
        )
        form = UserUpdateForm(
            {
                'first_name': 'Jo',
                'last_name': '',
                'email': 'new@yard.com',
                'role': self._role().pk,
            },
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertEqual(user.username, 'new@yard.com')
        self.assertEqual(user.email, 'new@yard.com')


class ViewerRestoredReadAccessTests(TestCase):
    def test_viewer_can_read_charge_list(self):
        self.client.force_login(make_viewer())
        response = self.client.get(reverse('charge_list'))
        self.assertEqual(response.status_code, 200)

    def test_viewer_passes_xero_status_gate(self):
        # 404 (no sync record) proves the feature gate admitted the request;
        # a denial would have been a redirect or 403.
        self.client.force_login(make_viewer())
        response = self.client.get(reverse('xero_invoice_status', args=[999]))
        self.assertEqual(response.status_code, 404)

    def test_xero_hidden_role_still_denied_status(self):
        user = make_user_with_access(username='nox', dashboard='full')
        self.client.force_login(user)
        response = self.client.get(reverse('xero_invoice_status', args=[999]))
        self.assertEqual(response.status_code, 302)

    def test_viewer_still_cannot_push_to_xero(self):
        self.client.force_login(make_viewer())
        response = self.client.post(
            reverse('invoice_bulk_action'),
            {'action': 'push_xero', 'invoice_ids': []},
        )
        self.assertEqual(response.status_code, 403)

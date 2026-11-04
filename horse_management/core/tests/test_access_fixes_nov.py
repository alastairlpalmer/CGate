"""Access holes from the November 2026 bug hunt.

- A ".." in a /media/ path passed the check for one folder and served a
  file from another (passports to a user with no access).
- Owner documents followed Horses access, not Owners access.
- A user manager could make themselves Administrator by deleting a role
  and moving its members into the Administrator role.
- The only Administrator could step down (or be deactivated), and then
  nobody could give the role again.
"""

import importlib
import os
import shutil
import tempfile

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import clear_url_caches, reverse

from core import mfa
from core.models import Document, Owner, Role, UserRole
from core.roles_testutils import administrator_role, make_admin, make_user_with_access


class MediaPathTests(TestCase):

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        for folder, name in (('documents', 'passport.pdf'), ('receipts/yard', 'r.pdf')):
            os.makedirs(os.path.join(self.media, folder), exist_ok=True)
            with open(os.path.join(self.media, folder, name), 'wb') as fh:
                fh.write(b'%PDF-1.4 secret')

    def _get(self, *paths):
        import horse_management.urls as urls
        with override_settings(SERVE_MEDIA=True, DEBUG=False, MEDIA_ROOT=self.media):
            importlib.reload(urls)
            clear_url_caches()
            try:
                return [self.client.get(path).status_code for path in paths]
            finally:
                importlib.reload(urls)
                clear_url_caches()

    def test_dot_dot_cannot_reach_another_folder(self):
        self.client.force_login(make_user_with_access('nobody'))
        statuses = self._get(
            '/media/documents/passport.pdf',
            '/media/business/%2e%2e/documents/passport.pdf',
            '/media/business/../documents/passport.pdf',
        )
        self.assertEqual(statuses[0], 403)
        self.assertNotIn(200, statuses)

    def test_horses_access_does_not_reach_yard_receipts(self):
        self.client.force_login(make_user_with_access('groom', horses='view'))
        statuses = self._get(
            '/media/documents/%2e%2e/receipts/yard/r.pdf',
            '/media/receipts/yard/r.pdf',
        )
        self.assertNotIn(200, statuses)

    def test_a_normal_path_still_works(self):
        self.client.force_login(make_user_with_access('groom', horses='view'))
        self.assertEqual(self._get('/media/documents/passport.pdf'), [200])


class OwnerDocumentAccessTests(TestCase):

    def setUp(self):
        self.owner = Owner.objects.create(name='Jo')
        self.document = Document.objects.create(
            owner=self.owner, doc_type='other', title='Loan agreement',
            file=ContentFile(b'%PDF-1.4', name='loan.pdf'),
        )
        self.addCleanup(self.document.file.delete, save=False)

    def test_horses_access_cannot_delete_an_owner_document(self):
        self.client.force_login(make_user_with_access('groom', horses='full'))
        self.client.post(reverse('document_delete', args=[self.document.pk]))
        self.document.refresh_from_db()
        self.assertFalse(self.document.is_deleted)

    def test_owners_access_can_delete_an_owner_document(self):
        self.client.force_login(make_user_with_access('office', owners='full'))
        self.client.post(reverse('document_delete', args=[self.document.pk]))
        self.document.refresh_from_db()
        self.assertTrue(self.document.is_deleted)


class AdministratorRoleTests(TestCase):

    def test_deleting_a_role_cannot_move_its_members_into_administrator(self):
        admin = make_admin()  # someone else stays Administrator
        manager = make_user_with_access('manager', users='full')
        temp = Role.objects.create(name='Temp', access={'users': 'full'})
        UserRole.objects.filter(user=manager).update(role=temp)
        self.client.force_login(manager)

        self.client.post(reverse('role_delete', args=[temp.pk]), {
            'reassign_to': administrator_role().pk,
        })

        manager.refresh_from_db()
        self.assertFalse(mfa.is_administrator(manager))
        self.assertTrue(Role.objects.filter(pk=temp.pk).exists())
        self.assertTrue(mfa.is_administrator(admin))

    def test_moved_members_are_in_the_role_history(self):
        self.client.force_login(make_admin())
        member = make_user_with_access('member', horses='view')
        old_role = member.role_assignment.role
        target = Role.objects.create(name='Groom', access={'horses': 'view'})
        before = UserRole.history.filter(user_id=member.pk).count()
        self.client.post(reverse('role_delete', args=[old_role.pk]), {'reassign_to': target.pk})
        self.assertGreater(UserRole.history.filter(user_id=member.pk).count(), before)

    def test_the_only_administrator_cannot_step_down(self):
        admin = make_admin()
        manager_role = Role.objects.create(name='Manager', access={'users': 'full'})
        self.client.force_login(admin)
        self.client.post(reverse('user_update', args=[admin.pk]), {
            'save_details': '1', 'first_name': 'A', 'last_name': 'B',
            'email': 'admin@example.com', 'role': manager_role.pk,
        })
        admin.refresh_from_db()
        self.assertTrue(mfa.is_administrator(admin))

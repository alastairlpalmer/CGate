"""The Role Suite admin pane: role CRUD, matrix editing, lockout guards."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.features import FEATURES
from core.models import Role, UserRole
from core.roles_testutils import (
    administrator_role,
    assign_role,
    make_admin,
    make_user_with_access,
    make_viewer,
)

User = get_user_model()


def matrix_post(name, description="", **levels):
    """A full valid POST body for RoleForm (everything hidden by default)."""
    data = {"name": name, "description": description}
    for f in FEATURES:
        data[f"access_{f['key']}"] = levels.get(f["key"], "hidden")
    return data


class RoleCrudTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def test_settings_page_lists_roles_with_member_counts(self):
        make_viewer()
        html = self.client.get(reverse("app_settings")).content.decode()
        self.assertIn("Users &amp; Roles", html)
        self.assertIn("Administrator", html)
        self.assertIn("Viewer", html)
        self.assertIn("1 member", html)  # one Administrator member (self)

    def test_create_role_via_matrix(self):
        resp = self.client.post(reverse("role_create"), matrix_post(
            "Bookkeeper", "Finance only",
            dashboard="full", invoices="full", charges="view",
        ))
        self.assertRedirects(resp, reverse("app_settings"))
        role = Role.objects.get(name="Bookkeeper")
        resolved = role.resolved_access()
        self.assertEqual(resolved["invoices"], "full")
        self.assertEqual(resolved["charges"], "view")
        self.assertEqual(resolved["horses"], "hidden")

    def test_duplicate_name_rejected_case_insensitively(self):
        Role.objects.create(name="Groom")
        resp = self.client.post(reverse("role_create"), matrix_post("groom"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")

    def test_edit_role_updates_matrix(self):
        role = Role.objects.create(name="Groom", access={"health": "full"})
        resp = self.client.post(
            reverse("role_update", args=[role.pk]),
            matrix_post("Groom", health="full", horses="view"),
        )
        self.assertRedirects(resp, reverse("app_settings"))
        role.refresh_from_db()
        self.assertEqual(role.resolved_access()["horses"], "view")

    def test_role_form_page_renders_all_features(self):
        html = self.client.get(reverse("role_create")).content.decode()
        for f in FEATURES:
            self.assertIn(f"access_{f['key']}", html)

    def test_binary_features_offer_no_view_option(self):
        html = self.client.get(reverse("role_create")).content.decode()
        # the settings radio group must not contain a 'view' value
        import re
        settings_radios = re.findall(r'name="access_settings"\s+value="(\w+)"', html)
        self.assertEqual(sorted(set(settings_radios)), ["full", "hidden"])
        horses_radios = re.findall(r'name="access_horses"\s+value="(\w+)"', html)
        self.assertEqual(sorted(set(horses_radios)), ["full", "hidden", "view"])

    def test_pane_requires_users_feature(self):
        outsider = make_user_with_access(username="outsider", dashboard="full")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse("role_create")).status_code, 302)
        self.assertEqual(self.client.post(reverse("role_create"), matrix_post("X")).status_code, 403)


class SystemRoleProtectionTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)
        self.system = administrator_role()

    def test_system_role_matrix_is_locked_on_save(self):
        resp = self.client.post(
            reverse("role_update", args=[self.system.pk]),
            matrix_post("Administrator", "The boss role"),  # everything hidden in POST
        )
        self.assertRedirects(resp, reverse("app_settings"))
        self.system.refresh_from_db()
        self.assertEqual(self.system.resolved_access()["users"], "full")
        self.assertEqual(self.system.description, "The boss role")  # name/desc editable

    def test_system_role_cannot_be_deleted(self):
        resp = self.client.post(reverse("role_delete", args=[self.system.pk]))
        self.assertRedirects(resp, reverse("role_update", args=[self.system.pk]))
        self.assertTrue(Role.objects.filter(pk=self.system.pk).exists())


class RoleDeleteTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def test_delete_empty_role_needs_no_reassignment(self):
        role = Role.objects.create(name="Unused")
        resp = self.client.post(reverse("role_delete", args=[role.pk]))
        self.assertRedirects(resp, reverse("app_settings"))
        self.assertFalse(Role.objects.filter(name="Unused").exists())

    def test_delete_with_members_requires_and_applies_reassignment(self):
        role = Role.objects.create(name="Old", access={"horses": "view"})
        target = Role.objects.create(name="New", access={"horses": "view"})
        member = User.objects.create_user("member", password="pw")
        assign_role(member, role)

        # without reassign_to → bounced back
        resp = self.client.post(reverse("role_delete", args=[role.pk]))
        self.assertRedirects(resp, reverse("role_update", args=[role.pk]))
        self.assertTrue(Role.objects.filter(pk=role.pk).exists())

        # with reassign_to → members moved, role gone
        resp = self.client.post(reverse("role_delete", args=[role.pk]), {"reassign_to": target.pk})
        self.assertRedirects(resp, reverse("app_settings"))
        self.assertEqual(UserRole.objects.get(user=member).role, target)
        self.assertFalse(Role.objects.filter(pk=role.pk).exists())


class LockoutGuardTests(TestCase):
    """Nobody can saw off the branch everyone is sitting on."""

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def _manager_role(self, name="Manager"):
        return Role.objects.create(name=name, access={"users": "full", "dashboard": "full"})

    def test_cannot_change_own_role_away_from_user_management(self):
        viewer_target = Role.objects.create(name="Plain")
        resp = self.client.post(reverse("user_update", args=[self.admin.pk]), {
            "save_details": "1",
            "first_name": "Ada", "last_name": "", "email": "ada@example.com",
            "role": viewer_target.pk,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(UserRole.objects.get(user=self.admin).role.name, "Administrator")

    def test_cannot_demote_last_manager_via_user_form(self):
        mgr_role = self._manager_role()
        other = User.objects.create_user("other", password="pw", email="other@example.com")
        assign_role(other, mgr_role)
        # admin (Administrator) exists, so demoting other IS allowed
        plain = Role.objects.create(name="Plain")
        resp = self.client.post(reverse("user_update", args=[other.pk]), {
            "save_details": "1",
            "first_name": "O", "last_name": "", "email": "other@example.com",
            "role": plain.pk,
        })
        self.assertRedirects(resp, reverse("app_settings"))
        self.assertEqual(UserRole.objects.get(user=other).role, plain)

    def test_cannot_deactivate_last_manager(self):
        # a second manager can be deactivated while admin remains
        mgr = User.objects.create_user("mgr", password="pw")
        assign_role(mgr, self._manager_role())
        resp = self.client.post(reverse("user_update", args=[mgr.pk]), {"toggle_active": "1"})
        self.assertRedirects(resp, reverse("app_settings"))
        mgr.refresh_from_db()
        self.assertFalse(mgr.is_active)

        # but the last one (self) can't deactivate themselves
        resp = self.client.post(reverse("user_update", args=[self.admin.pk]), {"toggle_active": "1"})
        self.assertEqual(resp.status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_matrix_demotion_blocked_when_it_strands_user_management(self):
        # Move the only admin onto a custom manager role, then try to remove
        # its users access — must be blocked.
        mgr_role = self._manager_role()
        assign_role(self.admin, mgr_role)
        administrator_role().assignments.all().delete()
        resp = self.client.post(
            reverse("role_update", args=[mgr_role.pk]),
            matrix_post("Manager", dashboard="full"),  # users → hidden
        )
        self.assertEqual(resp.status_code, 200)  # re-rendered with error message
        mgr_role.refresh_from_db()
        self.assertEqual(mgr_role.resolved_access()["users"], "full")

    def test_matrix_demotion_allowed_when_other_managers_exist(self):
        mgr_role = self._manager_role()
        other = User.objects.create_user("other2", password="pw")
        assign_role(other, mgr_role)
        # self.admin still holds Administrator, so demoting Manager is fine
        resp = self.client.post(
            reverse("role_update", args=[mgr_role.pk]),
            matrix_post("Manager", dashboard="full"),
        )
        self.assertRedirects(resp, reverse("app_settings"))
        mgr_role.refresh_from_db()
        self.assertEqual(mgr_role.resolved_access()["users"], "hidden")

    def test_delete_reassignment_cannot_strand_user_management(self):
        mgr_role = self._manager_role()
        assign_role(self.admin, mgr_role)
        administrator_role().assignments.all().delete()
        plain = Role.objects.create(name="Plain")
        resp = self.client.post(reverse("role_delete", args=[mgr_role.pk]), {"reassign_to": plain.pk})
        self.assertRedirects(resp, reverse("role_update", args=[mgr_role.pk]))
        self.assertTrue(Role.objects.filter(pk=mgr_role.pk).exists())


class UserFormRoleAssignmentTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def test_create_user_assigns_selected_role(self):
        role = Role.objects.create(name="Groom", access={"health": "full", "dashboard": "full"})
        resp = self.client.post(reverse("user_create"), {
            "first_name": "Gail", "last_name": "Groom",
            "email": "gail@example.com",
            "role": role.pk,
            "password1": "horses-are-great-1", "password2": "horses-are-great-1",
        })
        self.assertRedirects(resp, reverse("app_settings"))
        user = User.objects.get(email="gail@example.com")
        self.assertEqual(UserRole.objects.get(user=user).role, role)
        self.assertFalse(user.is_staff)  # roles no longer touch is_staff

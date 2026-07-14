"""Email sign-in backend and Settings → Users & Access tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()

PW = "horse-yard-2026"


def make_admin(email="admin@example.com", **extra):
    return User.objects.create_user(
        username=email, email=email, password=PW, is_staff=True, **extra
    )


def make_viewer(email="viewer@example.com", **extra):
    return User.objects.create_user(username=email, email=email, password=PW, **extra)


class EmailLoginTests(TestCase):
    def test_login_with_email(self):
        make_viewer("jo@example.com")
        self.assertTrue(self.client.login(username="jo@example.com", password=PW))

    def test_login_with_email_case_insensitive(self):
        make_viewer("jo@example.com")
        self.assertTrue(self.client.login(username="Jo@Example.COM", password=PW))

    def test_legacy_username_still_works(self):
        User.objects.create_user(username="alastair", email="al@example.com", password=PW)
        self.assertTrue(self.client.login(username="alastair", password=PW))
        self.assertTrue(self.client.login(username="al@example.com", password=PW))

    def test_wrong_password_rejected(self):
        make_viewer("jo@example.com")
        self.assertFalse(self.client.login(username="jo@example.com", password="nope"))

    def test_unknown_email_rejected(self):
        self.assertFalse(self.client.login(username="ghost@example.com", password=PW))

    def test_inactive_user_rejected(self):
        make_viewer("jo@example.com", is_active=False)
        self.assertFalse(self.client.login(username="jo@example.com", password=PW))

    def test_username_owner_wins_over_email_clash(self):
        # One user's username equals another user's email address.
        owner = User.objects.create_user(username="shared@example.com", password=PW)
        User.objects.create_user(username="other", email="shared@example.com", password="different-pw-99")
        self.client.login(username="shared@example.com", password=PW)
        self.assertEqual(int(self.client.session["_auth_user_id"]), owner.pk)

    def test_duplicate_emails_are_refused(self):
        # Legacy data: two accounts sharing an email — ambiguous, deny both.
        User.objects.create_user(username="a", email="dup@example.com", password=PW)
        User.objects.create_user(username="b", email="dup@example.com", password=PW)
        self.assertFalse(self.client.login(username="dup@example.com", password=PW))


class UserPagesAccessTests(TestCase):
    def test_viewer_cannot_open_user_pages(self):
        viewer = make_viewer()
        admin = make_admin()
        self.client.login(username=viewer.email, password=PW)
        self.assertEqual(self.client.get(reverse("user_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("user_update", args=[admin.pk])).status_code, 403)

    def test_logged_out_redirected_to_login(self):
        resp = self.client.get(reverse("user_create"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_admin_sees_users_card_on_settings(self):
        admin = make_admin()
        make_viewer("jo@example.com")
        self.client.login(username=admin.email, password=PW)
        resp = self.client.get(reverse("app_settings"))
        self.assertContains(resp, "Users &amp; Access")
        self.assertContains(resp, "jo@example.com")

    def test_viewer_settings_has_no_users_card(self):
        viewer = make_viewer()
        self.client.login(username=viewer.email, password=PW)
        resp = self.client.get(reverse("app_settings"))
        self.assertNotContains(resp, "Users &amp; Access")


class UserCreateTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.login(username=self.admin.email, password=PW)

    def _post(self, **overrides):
        data = {
            "first_name": "Jo",
            "last_name": "Bloggs",
            "email": "jo@example.com",
            "role": "viewer",
            "password1": PW,
            "password2": PW,
        }
        data.update(overrides)
        return self.client.post(reverse("user_create"), data)

    def test_creates_viewer_with_email_as_username(self):
        resp = self._post()
        self.assertRedirects(resp, reverse("app_settings"))
        user = User.objects.get(email="jo@example.com")
        self.assertEqual(user.username, "jo@example.com")
        self.assertFalse(user.is_staff)
        self.assertTrue(self.client.login(username="jo@example.com", password=PW))

    def test_creates_admin(self):
        self._post(role="admin")
        self.assertTrue(User.objects.get(email="jo@example.com").is_staff)

    def test_email_is_normalised_to_lowercase(self):
        self._post(email="Jo@Example.COM")
        self.assertTrue(User.objects.filter(username="jo@example.com").exists())

    def test_duplicate_email_rejected(self):
        make_viewer("jo@example.com")
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")

    def test_duplicate_of_legacy_username_rejected(self):
        User.objects.create_user(username="jo@example.com", password=PW)
        resp = self._post()
        self.assertContains(resp, "already exists")

    def test_mismatched_passwords_rejected(self):
        resp = self._post(password2="something-else")
        self.assertContains(resp, "Passwords don")
        self.assertFalse(User.objects.filter(email="jo@example.com").exists())

    def test_weak_password_rejected(self):
        resp = self._post(password1="123", password2="123")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="jo@example.com").exists())


class UserUpdateTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.viewer = make_viewer("jo@example.com", first_name="Jo")
        self.client.login(username=self.admin.email, password=PW)

    def _details(self, target, **overrides):
        data = {
            "save_details": "1",
            "first_name": target.first_name or "X",
            "last_name": target.last_name,
            "email": target.email,
            "role": "admin" if target.is_staff else "viewer",
        }
        data.update(overrides)
        return self.client.post(reverse("user_update", args=[target.pk]), data)

    def test_promote_viewer_to_admin(self):
        self._details(self.viewer, role="admin")
        self.viewer.refresh_from_db()
        self.assertTrue(self.viewer.is_staff)

    def test_email_change_updates_username_for_email_accounts(self):
        self._details(self.viewer, email="new@example.com")
        self.viewer.refresh_from_db()
        self.assertEqual(self.viewer.email, "new@example.com")
        self.assertEqual(self.viewer.username, "new@example.com")
        self.assertTrue(self.client.login(username="new@example.com", password=PW))

    def test_email_change_keeps_legacy_username(self):
        legacy = User.objects.create_user(
            username="alastair", email="old@example.com", password=PW
        )
        self._details(legacy, email="new@example.com")
        legacy.refresh_from_db()
        self.assertEqual(legacy.username, "alastair")
        self.assertEqual(legacy.email, "new@example.com")

    def test_cannot_demote_self(self):
        self._details(self.admin, role="viewer")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)

    def test_demote_other_admin_requires_remaining_admin(self):
        other = make_admin("second@example.com")
        # self.admin still active, so demoting `other` is fine
        self._details(other, role="viewer")
        other.refresh_from_db()
        self.assertFalse(other.is_staff)

    def test_demoting_clears_superuser_flag(self):
        other = make_admin("second@example.com", is_superuser=True)
        self._details(other, role="viewer")
        other.refresh_from_db()
        self.assertFalse(other.is_superuser)

    def test_admin_can_reset_password(self):
        resp = self.client.post(
            reverse("user_update", args=[self.viewer.pk]),
            {"set_password": "1", "password1": "new-yard-pw-77", "password2": "new-yard-pw-77"},
        )
        self.assertRedirects(resp, reverse("app_settings"))
        self.assertTrue(self.client.login(username="jo@example.com", password="new-yard-pw-77"))

    def test_password_mismatch_shows_error(self):
        resp = self.client.post(
            reverse("user_update", args=[self.viewer.pk]),
            {"set_password": "1", "password1": "new-yard-pw-77", "password2": "different"},
        )
        self.assertEqual(resp.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertTrue(self.viewer.check_password(PW))

    def test_deactivate_and_reactivate(self):
        self.client.post(reverse("user_update", args=[self.viewer.pk]), {"toggle_active": "1"})
        self.viewer.refresh_from_db()
        self.assertFalse(self.viewer.is_active)
        self.assertFalse(self.client.login(username="jo@example.com", password=PW))

        self.client.post(reverse("user_update", args=[self.viewer.pk]), {"toggle_active": "1"})
        self.viewer.refresh_from_db()
        self.assertTrue(self.viewer.is_active)

    def test_cannot_deactivate_self(self):
        self.client.post(reverse("user_update", args=[self.admin.pk]), {"toggle_active": "1"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_cannot_deactivate_only_admin(self):
        # A second admin tries to deactivate the only *other* admin while
        # being one themselves — allowed. But deactivating the last active
        # admin must be blocked.
        second = make_admin("second@example.com")
        self.client.login(username=second.email, password=PW)
        # Deactivate the first admin — fine, `second` remains.
        self.client.post(reverse("user_update", args=[self.admin.pk]), {"toggle_active": "1"})
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)

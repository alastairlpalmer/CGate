"""Role model resolution and the access_map/has_feature_access core."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from core.features import ALL_FULL, DEFAULT_LEVELS, FEATURES, LEVEL_FULL, LEVEL_HIDDEN, LEVEL_VIEW
from core.models import Role, UserRole
from core.permissions import access_map, has_feature_access, role_name_for
from core.roles_testutils import assign_role, make_admin, make_user_with_access

User = get_user_model()


class ResolvedAccessTests(TestCase):
    def test_empty_access_resolves_to_all_hidden(self):
        role = Role.objects.create(name="Empty")
        self.assertEqual(role.resolved_access(), DEFAULT_LEVELS)

    def test_stored_levels_survive_and_missing_keys_default_hidden(self):
        role = Role.objects.create(name="Partial", access={"horses": "view", "invoices": "full"})
        resolved = role.resolved_access()
        self.assertEqual(resolved["horses"], LEVEL_VIEW)
        self.assertEqual(resolved["invoices"], LEVEL_FULL)
        self.assertEqual(resolved["health"], LEVEL_HIDDEN)

    def test_unknown_keys_are_ignored(self):
        role = Role.objects.create(name="Stale", access={"retired_feature": "full"})
        self.assertNotIn("retired_feature", role.resolved_access())

    def test_view_clamps_to_hidden_on_binary_features(self):
        # settings/users/dashboard/finances/xero don't support "view only"
        role = Role.objects.create(name="Clamped", access={"settings": "view", "horses": "view"})
        resolved = role.resolved_access()
        self.assertEqual(resolved["settings"], LEVEL_HIDDEN)
        self.assertEqual(resolved["horses"], LEVEL_VIEW)

    def test_garbage_levels_fall_back_to_hidden(self):
        role = Role.objects.create(name="Garbage", access={"horses": "superduper", "owners": 7})
        resolved = role.resolved_access()
        self.assertEqual(resolved["horses"], LEVEL_HIDDEN)
        self.assertEqual(resolved["owners"], LEVEL_HIDDEN)

    def test_system_role_always_resolves_full_regardless_of_stored_map(self):
        role = Role.objects.create(name="Admin-ish", is_system=True, access={"horses": "hidden"})
        self.assertEqual(role.resolved_access(), ALL_FULL)

    def test_every_registry_feature_is_covered(self):
        role = Role.objects.create(name="Coverage")
        self.assertEqual(set(role.resolved_access()), {f["key"] for f in FEATURES})


class AccessMapTests(TestCase):
    def test_superuser_gets_full_access_without_any_role(self):
        boss = User.objects.create_superuser("boss", password="pw")
        self.assertEqual(access_map(boss), ALL_FULL)
        self.assertEqual(role_name_for(boss), "Superuser")

    def test_user_without_assignment_sees_nothing(self):
        nobody = User.objects.create_user("nobody", password="pw")
        self.assertEqual(access_map(nobody), DEFAULT_LEVELS)
        self.assertEqual(role_name_for(nobody), "No role")

    def test_anonymous_sees_nothing(self):
        self.assertEqual(access_map(AnonymousUser()), DEFAULT_LEVELS)

    def test_assigned_role_drives_the_map(self):
        user = make_user_with_access(username="bk", invoices="full", horses="view")
        levels = access_map(user)
        self.assertEqual(levels["invoices"], LEVEL_FULL)
        self.assertEqual(levels["horses"], LEVEL_VIEW)
        self.assertEqual(levels["health"], LEVEL_HIDDEN)

    def test_map_is_memoized_single_query(self):
        user = make_user_with_access(username="memo", horses="view")
        user = User.objects.get(pk=user.pk)  # fresh instance, no memo
        with self.assertNumQueries(1):
            access_map(user)
            access_map(user)
            role_name_for(user)

    def test_reassignment_visible_on_fresh_instance(self):
        user = make_user_with_access(username="mover", horses="view")
        assign_role(user, Role.objects.create(name="Nothing"))
        fresh = User.objects.get(pk=user.pk)
        self.assertEqual(access_map(fresh)["horses"], LEVEL_HIDDEN)


class HasFeatureAccessTests(TestCase):
    def test_level_ladder(self):
        user = make_user_with_access(username="ladder", horses="view", invoices="full")
        self.assertTrue(has_feature_access(user, "horses", LEVEL_VIEW))
        self.assertFalse(has_feature_access(user, "horses", LEVEL_FULL))
        self.assertTrue(has_feature_access(user, "invoices", LEVEL_VIEW))
        self.assertTrue(has_feature_access(user, "invoices", LEVEL_FULL))
        self.assertFalse(has_feature_access(user, "health", LEVEL_VIEW))

    def test_unknown_feature_fails_loudly(self):
        user = make_admin(username="loud")
        with self.assertRaises(KeyError):
            has_feature_access(user, "not_a_feature")

    def test_testutils_rejects_unknown_feature_keys(self):
        with self.assertRaises(ValueError):
            make_user_with_access(username="typo", horsies="full")

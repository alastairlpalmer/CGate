"""Role Suite helpers for tests.

Tests run with migrations disabled (see ``horse_management.test_settings``),
so the seed data migration never executes — tests create the roles they
need through these helpers instead.
"""

from django.contrib.auth import get_user_model

from .features import DEFAULT_LEVELS
from .models import Role, UserRole

# Mirrors core/migrations/0026_seed_roles.py
VIEWER_ACCESS = {
    "dashboard": "full",
    "horses": "view",
    "owners": "view",
    "locations": "view",
    "health": "full",
    "breeding": "full",
    "feed": "view",
    "finances": "full",
    "invoices": "view",
    "costs": "hidden",
    "charges": "hidden",
    "xero": "hidden",
    "settings": "hidden",
    "users": "hidden",
}


def administrator_role():
    role, _ = Role.objects.get_or_create(
        name="Administrator", defaults={"is_system": True}
    )
    return role


def viewer_role():
    role, _ = Role.objects.get_or_create(
        name="Viewer", defaults={"access": VIEWER_ACCESS}
    )
    return role


def assign_role(user, role):
    UserRole.objects.update_or_create(user=user, defaults={"role": role})
    # Bust the per-request memo in case the same user object is reused.
    if hasattr(user, "_feature_access"):
        del user._feature_access
    return user


def make_admin(username="admin", password="pw", **kwargs):
    """A user with the Administrator role (full access everywhere)."""
    user = get_user_model().objects.create_user(
        username=username, password=password, **kwargs
    )
    return assign_role(user, administrator_role())


def make_viewer(username="viewer", password="pw", **kwargs):
    """A user with the seeded Viewer role (view + health/breeding writes)."""
    user = get_user_model().objects.create_user(
        username=username, password=password, **kwargs
    )
    return assign_role(user, viewer_role())


def make_user_with_access(username="user", password="pw", **levels):
    """A user with a throwaway role granting exactly ``levels``.

    Usage: ``make_user_with_access(invoices='full', horses='view')`` —
    unnamed features default to hidden.
    """
    unknown = set(levels) - set(DEFAULT_LEVELS)
    if unknown:
        raise ValueError(f"Unknown feature keys: {sorted(unknown)}")
    role = Role.objects.create(name=f"test-role-{username}", access=levels)
    user = get_user_model().objects.create_user(username=username, password=password)
    return assign_role(user, role)

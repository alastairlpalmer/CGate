"""Helpers for C-vet tests.

Tests run with migrations off (``horse_management.test_settings``), so
the seeded Vet role does not exist: ``vet_role`` makes it from the same
access map the migration uses.
"""

import importlib

from django.contrib.auth import get_user_model

from core.models import Role
from core.roles_testutils import assign_role

from .models import Practice, PracticeMember, PracticeYardLink

VET_ACCESS = importlib.import_module('cvet.migrations.0002_seed_vet_role').VET_ACCESS


def vet_role():
    role, _ = Role.objects.get_or_create(name='Vet', defaults={'access': VET_ACCESS})
    return role


def make_practice(name='Hill Vets', linked=True):
    practice = Practice.objects.create(name=name)
    if linked:
        PracticeYardLink.objects.create(practice=practice)
    return practice


def make_vet(username='vet', practice=None, **user_fields):
    """A Vet-role login that is a member of ``practice`` (a new, linked one by default)."""
    user = get_user_model().objects.create_user(username=username, password='pw', **user_fields)
    assign_role(user, vet_role())
    if practice is None:
        practice = Practice.objects.filter(name='Hill Vets').first() or make_practice()
    from django.utils import timezone

    # Approved: the tests of the approval itself make waiting members.
    PracticeMember.objects.create(practice=practice, user=user, approved_at=timezone.now())
    return user

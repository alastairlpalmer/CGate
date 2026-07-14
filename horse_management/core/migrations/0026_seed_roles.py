"""Seed the Role Suite and grandfather existing users.

Mirrors the old is_staff scheme exactly so nothing changes on deploy:
  - is_staff users  → Administrator (system role, full access everywhere)
  - everyone else   → Viewer (view everywhere it could see before, plus
    full Health/Breeding — viewers have always been able to record those)

The access maps are written out literally (not imported from core.features)
so this migration stays frozen as the registry evolves.
"""

from django.db import migrations

ADMINISTRATOR_ACCESS = {}  # is_system roles resolve to all-full regardless

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


def seed_roles(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    UserRole = apps.get_model('core', 'UserRole')
    User = apps.get_model('auth', 'User')

    administrator = Role.objects.create(
        name="Administrator",
        description="Full access to everything, including user and role management.",
        is_system=True,
        access=ADMINISTRATOR_ACCESS,
    )
    viewer = Role.objects.create(
        name="Viewer",
        description="Read-only across the yard, plus recording health and breeding.",
        access=VIEWER_ACCESS,
    )

    UserRole.objects.bulk_create([
        UserRole(user=user, role=administrator if user.is_staff else viewer)
        for user in User.objects.all()
    ])


def unseed_roles(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    UserRole = apps.get_model('core', 'UserRole')
    UserRole.objects.all().delete()
    Role.objects.filter(name__in=["Administrator", "Viewer"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0025_role_userrole'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(seed_roles, unseed_roles),
    ]

"""Restore read access the Role Suite cutover accidentally revoked.

Before the Role Suite, the charge list and the read-only Xero status
endpoints were readable by any logged-in user, but 0026 seeded the Viewer
role with charges/xero hidden — so existing non-staff users lost pages they
could previously see, despite that migration's stated goal of mirroring the
old scheme exactly.

Only flips hidden → view on the seeded Viewer role; values an administrator
has since customised to anything else are left alone.
"""

from django.db import migrations

RESTORED_FEATURES = ("charges", "xero")


def restore_viewer_read_access(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    for role in Role.objects.filter(name="Viewer", is_system=False):
        access = dict(role.access or {})
        changed = False
        for feature in RESTORED_FEATURES:
            if access.get(feature) == "hidden":
                access[feature] = "view"
                changed = True
        if changed:
            role.access = access
            role.save(update_fields=["access"])


def revoke_viewer_read_access(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    for role in Role.objects.filter(name="Viewer", is_system=False):
        access = dict(role.access or {})
        changed = False
        for feature in RESTORED_FEATURES:
            if access.get(feature) == "view":
                access[feature] = "hidden"
                changed = True
        if changed:
            role.access = access
            role.save(update_fields=["access"])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_seed_roles'),
    ]

    operations = [
        migrations.RunPython(restore_viewer_read_access, revoke_viewer_read_access),
    ]

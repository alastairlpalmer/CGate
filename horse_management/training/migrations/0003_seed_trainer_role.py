"""Seed the Trainer role, and give Viewer read access to Training.

docs/TRAINING_MODULE_PLAN.md, section 4.3: the person who runs the horses
day to day has full access to every yard area and no access to owners,
money or administration. The farrier's and the vet's own pages stay with
their own roles.

The access map is written out literally (not imported from core.features)
so this migration stays frozen as the registry changes. A yard that has
already made a role called "Trainer" keeps its own.

Viewer sees every other area read-only, so it gets Training at view, but
only while its value is still the default (absent or "hidden"); a value an
administrator chose is left alone. This follows
core/migrations/0027_viewer_restore_read_access.py.
"""

from django.db import migrations

TRAINER_ACCESS = {
    "dashboard": "full",
    "horses": "full",
    "owners": "hidden",
    "locations": "full",
    "health": "full",
    "breeding": "full",
    "feed": "full",
    "training": "full",
    "farrier_portal": "hidden",
    "cvet": "hidden",
    "finances": "hidden",
    "invoices": "hidden",
    "costs": "hidden",
    "charges": "hidden",
    "xero": "hidden",
    "settings": "hidden",
    "users": "hidden",
}

TRAINER_DESCRIPTION = (
    "Runs the horses day to day: horses, locations, health, breeding, feed "
    "and training. No owners, money or settings."
)


def seed_trainer_role(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Role.objects.get_or_create(
        name="Trainer",
        defaults={"description": TRAINER_DESCRIPTION, "access": TRAINER_ACCESS},
    )
    for role in Role.objects.filter(name="Viewer", is_system=False):
        access = dict(role.access or {})
        if access.get("training", "hidden") == "hidden":
            access["training"] = "view"
            role.access = access
            role.save(update_fields=["access"])


def unseed_trainer_role(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    # Only the role this migration made, and only if nobody holds it.
    Role.objects.filter(
        name="Trainer", access=TRAINER_ACCESS, assignments__isnull=True,
    ).delete()
    for role in Role.objects.filter(name="Viewer", is_system=False):
        access = dict(role.access or {})
        if access.get("training") == "view":
            access["training"] = "hidden"
            role.access = access
            role.save(update_fields=["access"])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0065_calendar_entry_repeat'),
        ('training', '0002_seed_work_types'),
    ]

    operations = [
        migrations.RunPython(seed_trainer_role, unseed_trainer_role),
    ]

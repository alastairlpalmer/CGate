"""Seed the Vet role for C-vet.

A vet works for the practice, not for the yard: the role opens C-vet and
nothing else (docs/C_VET_PLAN.md, section 3). The login must also be made
a member of the practice (Django admin, C-vet, Practices).

The access map is written out literally (not imported from core.features)
so this migration stays frozen as the registry changes. A yard that has
already made a role called "Vet" keeps its own.
"""

from django.db import migrations

VET_ACCESS = {
    "dashboard": "hidden",
    "horses": "hidden",
    "owners": "hidden",
    "locations": "hidden",
    "health": "hidden",
    "breeding": "hidden",
    "feed": "hidden",
    "farrier_portal": "hidden",
    "cvet": "full",
    "finances": "hidden",
    "invoices": "hidden",
    "costs": "hidden",
    "charges": "hidden",
    "xero": "hidden",
    "settings": "hidden",
    "users": "hidden",
}


def seed_vet_role(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Role.objects.get_or_create(
        name="Vet",
        defaults={
            "description": "A vet from the practice: C-vet only.",
            "access": VET_ACCESS,
        },
    )


def unseed_vet_role(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    # Only the role this migration made, and only if nobody holds it.
    Role.objects.filter(name="Vet", access=VET_ACCESS, assignments__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cvet', '0002_ownership_seam'),
        ('core', '0061_attention_dismissal'),
    ]

    operations = [
        migrations.RunPython(seed_vet_role, unseed_vet_role),
    ]

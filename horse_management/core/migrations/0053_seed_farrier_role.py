"""Seed the Farrier role for the farrier visit page.

A farrier from outside the yard may open their own visit page and
nothing else (docs/FARRIER_PAGE_PLAN.md, section 4). Every other feature
is hidden, so the role cannot reach owners, money, vet notes or settings.

The access map is written out literally (not imported from core.features)
so this migration stays frozen as the registry evolves. A yard that has
already made a role called "Farrier" keeps its own.
"""

from django.db import migrations

FARRIER_ACCESS = {
    "dashboard": "hidden",
    "horses": "hidden",
    "owners": "hidden",
    "locations": "hidden",
    "health": "hidden",
    "breeding": "hidden",
    "feed": "hidden",
    "farrier_portal": "full",
    "finances": "hidden",
    "invoices": "hidden",
    "costs": "hidden",
    "charges": "hidden",
    "xero": "hidden",
    "settings": "hidden",
    "users": "hidden",
}


def seed_farrier_role(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Role.objects.get_or_create(
        name="Farrier",
        defaults={
            "description": "A visiting farrier: their own visit page only.",
            "access": FARRIER_ACCESS,
        },
    )


def unseed_farrier_role(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    # Only the role this migration made, and only if nobody holds it.
    Role.objects.filter(
        name="Farrier", access=FARRIER_ACCESS, assignments__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0052_speed_indexes'),
    ]

    operations = [
        migrations.RunPython(seed_farrier_role, unseed_farrier_role),
    ]

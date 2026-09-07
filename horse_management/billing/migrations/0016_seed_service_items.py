from django.db import migrations

from billing.service_directory import seed_default_services


def seed(apps, schema_editor):
    seed_default_services(
        apps.get_model('billing', 'ServiceItem'),
        apps.get_model('health', 'VaccinationType'),
    )


def unseed(apps, schema_editor):
    # Only the seeded rows go; anything the user added or renamed stays.
    from billing.service_directory import DEFAULT_SERVICE_ITEMS
    ServiceItem = apps.get_model('billing', 'ServiceItem')
    for category, name, price, _extras in DEFAULT_SERVICE_ITEMS:
        ServiceItem.objects.filter(category=category, name=name, price=price).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0015_service_items'),
        ('health', '0007_service_items'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

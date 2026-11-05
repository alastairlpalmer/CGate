from django.db import migrations
from django.db.models import F


def date_archived_owners(apps, schema_editor):
    """Owners archived through Django admin got no archived_at, so the data
    retention job (which counts from it) never anonymised them. Their last
    change is the best date there is."""
    Owner = apps.get_model('core', 'Owner')
    Owner.objects.filter(is_archived=True, archived_at__isnull=True).update(
        archived_at=F('updated_at'),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0050_rate_price_history"),
    ]

    operations = [
        migrations.RunPython(date_archived_owners, migrations.RunPython.noop),
    ]

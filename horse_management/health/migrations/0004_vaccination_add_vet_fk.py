"""
Replace Vaccination.vet_name (CharField) with Vaccination.vet (FK to ServiceProvider).
"""

import django.db.models.deletion
from django.db import migrations, models


def migrate_vet_names(apps, schema_editor):
    """Match existing vet_name text to ServiceProvider records."""
    Vaccination = apps.get_model('health', 'Vaccination')
    ServiceProvider = apps.get_model('billing', 'ServiceProvider')

    for vax in Vaccination.objects.filter(vet_name__gt='').exclude(vet_name=''):
        provider = ServiceProvider.objects.filter(
            name__iexact=vax.vet_name.strip(),
            provider_type='vet',
        ).first()
        if provider:
            vax.vet_id = provider.id
            vax.save(update_fields=['vet_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('health', '0003_farriervisit_reminder_sent_and_more'),
        ('billing', '0001_initial'),
    ]

    operations = [
        # 1. Add the new FK field
        migrations.AddField(
            model_name='vaccination',
            name='vet',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='vaccinations',
                limit_choices_to={'provider_type': 'vet'},
                to='billing.serviceprovider',
            ),
        ),
        # 2. Copy matching vet_name values to the FK
        migrations.RunPython(migrate_vet_names, migrations.RunPython.noop),
        # 3. Remove the old text field
        migrations.RemoveField(
            model_name='vaccination',
            name='vet_name',
        ),
    ]

from datetime import timedelta

from django.db import migrations

GESTATION_DAYS = 340


def forwards(apps, schema_editor):
    BreedingRecord = apps.get_model('health', 'BreedingRecord')
    Covering = apps.get_model('health', 'Covering')
    PregnancyScan = apps.get_model('health', 'PregnancyScan')

    for record in BreedingRecord.objects.all():
        if record.date_covered and not Covering.objects.filter(record=record).exists():
            Covering.objects.create(record=record, date=record.date_covered)
        if record.date_scanned_14_days and not PregnancyScan.objects.filter(
            record=record, scan_type='14_day',
        ).exists():
            PregnancyScan.objects.create(
                record=record, date=record.date_scanned_14_days,
                scan_type='14_day', result='in_foal',
            )
        if record.date_scanned_heartbeat and not PregnancyScan.objects.filter(
            record=record, scan_type='heartbeat',
        ).exists():
            PregnancyScan.objects.create(
                record=record, date=record.date_scanned_heartbeat,
                scan_type='heartbeat', result='in_foal',
            )
        # A due date that is not 340 days from covering was typed in by hand;
        # keep it that way rather than letting the sync overwrite it.
        if record.date_covered and record.date_foal_due and (
            record.date_foal_due != record.date_covered + timedelta(days=GESTATION_DAYS)
        ):
            record.due_date_is_manual = True
            record.save(update_fields=['due_date_is_manual'])


class Migration(migrations.Migration):

    dependencies = [
        ('health', '0008_covering_scan_history'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

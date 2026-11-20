"""A horse with a passport number or a passport document has a passport.

The "No passport" badge read only the has_passport tick box, which nothing
kept in step: the horse page could say "No passport" beside a passport
number and a passport scan (docs/HORSE_PAGE_UX_REVIEW.md A4). From now on
Horse.save and Document.save tick it; this fixes the rows already there.
"""

from django.db import migrations


def tick_where_known(apps, schema_editor):
    Horse = apps.get_model('core', 'Horse')
    Document = apps.get_model('core', 'Document')
    Horse.objects.filter(has_passport=False).exclude(passport_number='').update(has_passport=True)
    with_scan = Document.objects.filter(
        doc_type='passport', deleted_at__isnull=True, horse__isnull=False,
    ).values('horse_id')
    Horse.objects.filter(has_passport=False, pk__in=with_scan).update(has_passport=True)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0068_audit_records_imported'),
    ]

    operations = [
        migrations.RunPython(tick_where_known, migrations.RunPython.noop),
    ]

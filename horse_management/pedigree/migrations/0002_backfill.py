"""Back-fill (docs/HORSE_IDENTITY_AND_PEDIGREE_PLAN.md §6.3).

* One record for each horse on the system, with its registered name (or
  yard name), suffix, year, sex and colour.
* One record for each distinct sire name and each distinct dam name, so a
  sire name typed on five horses gives one record. Names that differ in
  any way stay apart: joining two records is a person's decision (the
  Pedigree records list, phase 1c).
* A horse's dam link (Horse.dam) points at the dam's own record.
* "Unknown" as a sire or dam name marks that parent as known to be
  unknown.
"""

from django.db import migrations

FEMALE_SEXES = {'mare', 'filly'}
MALE_SEXES = {'stallion', 'colt', 'gelding'}


def backfill(apps, schema_editor):
    from pedigree.names import is_unknown, name_key, parse

    Horse = apps.get_model('core', 'Horse')
    Record = apps.get_model('pedigree', 'PedigreeHorse')
    colours = dict(Horse._meta.get_field('color').choices)

    own = {r.horse_id: r for r in Record.objects.filter(horse__isnull=False)}
    horses = list(Horse.objects.all().order_by('pk'))
    for h in horses:
        if h.pk in own:
            continue
        name = (h.registered_name or h.name or '').strip()
        own[h.pk] = Record.objects.create(
            horse_id=h.pk, name=name, name_key=name_key(name),
            country=(h.country_of_birth or '').upper(),
            year_of_birth=h.date_of_birth.year if h.date_of_birth else None,
            sex='F' if h.sex in FEMALE_SEXES else 'M' if h.sex in MALE_SEXES else '',
            colour=colours.get(h.color, '') if h.color else '',
            source='typed',
        )

    by_text = {}

    def from_text(text, sex):
        key = (sex, text)
        if key not in by_text:
            parsed = parse(text)
            by_text[key] = Record.objects.create(
                name=parsed.name, name_key=name_key(parsed.name), country=parsed.country,
                year_of_birth=parsed.year, sex=sex, source='backfill',
            )
        return by_text[key]

    for h in horses:
        rec = own[h.pk]
        fields = []
        sire_text = (h.sire_name or '').strip()
        if rec.sire_id is None and not rec.sire_unknown and sire_text:
            if is_unknown(sire_text):
                rec.sire_unknown = True
                fields.append('sire_unknown')
            else:
                rec.sire_id = from_text(sire_text, 'M').pk
                fields.append('sire')
        dam_text = (h.dam_name or '').strip()
        if rec.dam_id is None and not rec.dam_unknown:
            if h.dam_id and h.dam_id in own and h.dam_id != h.pk:
                rec.dam_id = own[h.dam_id].pk
                fields.append('dam')
            elif dam_text and is_unknown(dam_text):
                rec.dam_unknown = True
                fields.append('dam_unknown')
            elif dam_text:
                rec.dam_id = from_text(dam_text, 'F').pk
                fields.append('dam')
        if fields:
            rec.save(update_fields=fields)


def remove_all(apps, schema_editor):
    apps.get_model('pedigree', 'PedigreeHorse').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('pedigree', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill, remove_all),
    ]

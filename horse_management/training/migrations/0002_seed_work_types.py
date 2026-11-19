"""Seed the default work types (docs/TRAINING_MODULE_PLAN.md, section 7.1).

Only when the table is empty, so a yard that already set up its own keeps
them. The yard can rename, reorder and archive these. No usual minutes are
set: the yard knows its own.
"""

from django.db import migrations

# (name, kind, effort) with effort 1 easy, 2 steady, 3 fast, 0 rest.
WORK_TYPES = [
    ('Walk', 'ridden', 1),
    ('Walk and trot', 'ridden', 1),
    ('Hack', 'ridden', 1),
    ('Canter', 'ridden', 2),
    ('Schooling', 'ridden', 2),
    ('Jumping', 'ridden', 2),
    ('Fast work', 'ridden', 3),
    ('Interval training', 'ridden', 3),
    ('Horse walker', 'not_ridden', 1),
    ('Walk in hand', 'not_ridden', 1),
    ('Lunge', 'not_ridden', 2),
    ('Long reining', 'not_ridden', 2),
    ('Loose school', 'not_ridden', 2),
    ('Treadmill', 'not_ridden', 2),
    ('Water walker', 'not_ridden', 2),
    ('Swim', 'not_ridden', 2),
    ('Rest day', 'rest', 0),
    ('Box rest', 'rest', 0),
]


def seed_work_types(apps, schema_editor):
    WorkType = apps.get_model('training', 'WorkType')
    if WorkType.objects.exists():
        return
    WorkType.objects.bulk_create([
        WorkType(name=name, kind=kind, effort=effort, position=(i + 1) * 10)
        for i, (name, kind, effort) in enumerate(WORK_TYPES)
    ])


def unseed_work_types(apps, schema_editor):
    WorkType = apps.get_model('training', 'WorkType')
    # Only the seeded ones that no record uses.
    WorkType.objects.filter(
        name__in=[name for name, _, _ in WORK_TYPES], activities__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('training', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_work_types, unseed_work_types),
    ]

"""Give the yard's own "Stable Staff" role full access to Training.

docs/TRAINING_DASHBOARD_PLAN.md, section 3.1 (T7). "Stable Staff" is not a
seeded role: a yard made it in Settings, Users & Roles. Stable staff ride
the lots, so they mark lots done and record training. Training shows no
prices, so this gives no access to money.

- A role matches when its name, trimmed and in lower case, is "stable staff",
  and it is not a system role.
- Training is set to full only while it is absent or "hidden". A value an
  administrator chose stays.
- No other feature changes. No match: nothing changes.
- The reverse does nothing: it cannot know the value before.
"""

from django.db import migrations

ROLE_NAME = "stable staff"


def matching_roles(Role):
    return [r for r in Role.objects.filter(is_system=False) if (r.name or "").strip().lower() == ROLE_NAME]


def give_training(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    for role in matching_roles(Role):
        access = dict(role.access or {})
        if access.get("training", "hidden") == "hidden":
            access["training"] = "full"
            role.access = access
            role.save(update_fields=["access"])


class Migration(migrations.Migration):

    dependencies = [
        ("training", "0008_horsetraining_review_on"),
    ]

    operations = [
        migrations.RunPython(give_training, migrations.RunPython.noop),
    ]

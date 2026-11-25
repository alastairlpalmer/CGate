"""Give each practice the starter price list (cvet/starter_prices.py).

Only the items a practice does not have are added: no price is changed.
Going back removes nothing: a practice may use the items by then.
"""

from django.db import migrations

from cvet.starter_prices import add_starter_prices


def forwards(apps, schema_editor):
    price_model = apps.get_model('cvet', 'PriceItem')
    for practice in apps.get_model('cvet', 'Practice').objects.all():
        add_starter_prices(practice, price_model)


class Migration(migrations.Migration):

    dependencies = [
        ("cvet", "0009_horse_about"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

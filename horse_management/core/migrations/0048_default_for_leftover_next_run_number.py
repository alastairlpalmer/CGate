from django.db import migrations

# Some databases have core_businesssettings.next_run_number, NOT NULL and
# with no default, left by an April 2026 branch that never reached main.
# No code sets it, so creating the settings row (BusinessSettings.
# get_settings, load_csv_data) failed there. Give it a default where it
# exists. Nothing is dropped: removing the leftovers is a separate decision.


def default_leftover_column(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, 'core_businesssettings'
            )
        }
    if 'next_run_number' in columns:
        schema_editor.execute(
            'ALTER TABLE core_businesssettings ALTER COLUMN next_run_number SET DEFAULT 1'
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0047_invoice_per_horse"),
    ]

    operations = [
        migrations.RunPython(default_leftover_column, migrations.RunPython.noop),
    ]

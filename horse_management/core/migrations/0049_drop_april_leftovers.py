from django.db import migrations

# Remove what an April 2026 branch left in the production database. That
# branch never reached main, and no code reads these columns or tables.
# Settings → Database Check (core/schema_drift.py) listed them in
# November 2026. Each item is dropped only where it exists, so this also
# runs on databases that never had them (new installs, tests).
#
# Not reversible: the dropped values are gone. The reverse does nothing.

LEFTOVER_COLUMNS = [
    # Drop run_id before invoicing_invoicerun: it may reference that table.
    ('core_invoice', 'run_id'),
    ('core_businesssettings', 'currency_symbol'),
    ('core_businesssettings', 'farrier_revisit_weeks'),
    ('core_businesssettings', 'invoice_due_warning_days'),
    ('core_businesssettings', 'next_run_number'),
    ('core_businesssettings', 'worm_egg_threshold'),
    ('core_businesssettings', 'xero_invoice_status'),
]

LEFTOVER_TABLES = [
    'core_settingschangelog',
    'invoicing_invoicerun',
]

# Migration records for the April branch. No migration file has these
# names, so deleting the records changes no migration that the code runs.
LEFTOVER_MIGRATION_RECORDS = [
    ('core', '0016_expand_business_settings'),
    ('core', '0017_businesssettings_next_run_number'),
    ('core', '0017_farrier_revisit_weeks_min_validator'),
    ('invoicing', '0003_invoice_horse_invoicerun_invoice_run'),
]


def drop_leftovers(apps, schema_editor):
    connection = schema_editor.connection
    quote = schema_editor.quote_name
    cascade = ' CASCADE' if connection.vendor == 'postgresql' else ''

    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))

        for table, column in LEFTOVER_COLUMNS:
            if table not in tables:
                continue
            columns = {
                c.name for c in connection.introspection.get_table_description(cursor, table)
            }
            if column in columns:
                schema_editor.execute(
                    f'ALTER TABLE {quote(table)} DROP COLUMN {quote(column)}{cascade}'
                )

        for table in LEFTOVER_TABLES:
            if table in tables:
                schema_editor.execute(f'DROP TABLE {quote(table)}{cascade}')

        if 'django_migrations' not in tables:
            return
        for app, name in LEFTOVER_MIGRATION_RECORDS:
            cursor.execute(
                'DELETE FROM django_migrations WHERE app = %s AND name = %s', [app, name]
            )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0048_default_for_leftover_next_run_number"),
    ]

    operations = [
        migrations.RunPython(drop_leftovers, migrations.RunPython.noop),
    ]

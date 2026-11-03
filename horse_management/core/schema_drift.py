"""Where the database and the code disagree about the schema.

Migrations only record what was applied, not what the tables hold. A
database that once ran a branch which never reached main keeps that
branch's columns and tables, and the next migration that adds the same
column fails the deploy (core_invoice.horse_id, November 2026). This
reads the live schema and lists every difference, without changing
anything, so a drift is found before a deploy trips on it.
"""

from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder


def _model_tables():
    """{table: {column: nullable}} for every table the models expect."""
    tables = {}
    for model in apps.get_models(include_auto_created=True):
        meta = model._meta
        if not meta.managed or meta.proxy:
            continue
        columns = tables.setdefault(meta.db_table, {})
        for field in meta.local_fields:
            if field.column:
                columns[field.column] = field.null
    return tables


def _database_tables(cursor):
    """{table: {column: nullable}} for every table in the database."""
    introspection = connection.introspection
    tables = {}
    for table in introspection.table_names(cursor):
        tables[table] = {
            column.name: bool(column.null_ok)
            for column in introspection.get_table_description(cursor, table)
        }
    return tables


def column_differences():
    """Lines describing tables and columns that differ from the models.

    Extra columns that allow NULL are harmless leftovers; an extra column
    that is NOT NULL without a default breaks every insert the code makes,
    so the line says which kind it is.
    """
    expected = _model_tables()
    with connection.cursor() as cursor:
        actual = _database_tables(cursor)

    lines = []
    for table in sorted(expected):
        if table not in actual:
            lines.append(f'Missing table: {table}')
            continue
        want, have = expected[table], actual[table]
        for column in sorted(set(want) - set(have)):
            lines.append(f'Missing column: {table}.{column}')
        for column in sorted(set(have) - set(want)):
            kind = 'allows NULL' if have[column] else 'NOT NULL: inserts may fail'
            lines.append(f'Extra column: {table}.{column} ({kind})')
        for column in sorted(set(want) & set(have)):
            if want[column] and not have[column]:
                lines.append(f'Column is NOT NULL but the model allows empty: {table}.{column}')
            elif not want[column] and have[column]:
                lines.append(f'Column allows NULL but the model requires a value: {table}.{column}')

    ignored = {MigrationRecorder.Migration._meta.db_table}
    for table in sorted(set(actual) - set(expected) - ignored):
        lines.append(f'Extra table: {table}')
    return lines


def migration_differences():
    """Lines for migrations not yet applied, and applied ones no code has."""
    executor = MigrationExecutor(connection)
    lines = [
        f'Not applied: {migration.app_label}.{migration.name}'
        for migration, _backwards in executor.migration_plan(
            executor.loader.graph.leaf_nodes()
        )
    ]
    known = set(executor.loader.disk_migrations)
    for app_label, name in sorted(executor.loader.applied_migrations):
        if (app_label, name) not in known:
            lines.append(f'Applied but not in the code: {app_label}.{name}')
    return lines

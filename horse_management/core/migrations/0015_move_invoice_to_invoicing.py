"""
Remove Invoice and InvoiceLineItem from core app's state.

The models have been moved to the invoicing app. This migration only
updates Django's state — no database changes occur (tables keep their
core_* names via db_table in Meta).
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_add_performance_indexes'),
        ('invoicing', '0001_move_invoice_models'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='InvoiceLineItem'),
                migrations.DeleteModel(name='Invoice'),
            ],
            database_operations=[],
        ),
    ]

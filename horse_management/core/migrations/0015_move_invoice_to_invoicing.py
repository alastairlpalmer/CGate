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
        # XeroInvoiceSync.invoice points at core.Invoice until xero_integration
        # 0002 re-points it to invoicing.Invoice. Removing Invoice from core's
        # state before that re-point leaves a dangling FK, so on a fresh
        # database this migration must run after the xero FK has moved.
        ('xero_integration', '0002_alter_xeroinvoicesync_invoice'),
        # The same for ExtraCharge.invoice, which billing 0013 re-points.
        # The order used to be right by chance; billing 0024 (which depends
        # on invoicing) changed it, and billing 0002 then failed.
        ('billing', '0013_alter_extracharge_invoice'),
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

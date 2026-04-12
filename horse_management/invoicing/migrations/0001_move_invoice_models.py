"""
Move Invoice and InvoiceLineItem from core to invoicing app.

This uses SeparateDatabaseAndState to update Django's internal model registry
without touching the actual database tables (which keep their core_* names
via db_table in Meta).
"""

from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal
from django.core.validators import MinValueValidator


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0014_add_performance_indexes'),
        ('billing', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='Invoice',
                    fields=[
                        ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('invoice_number', models.CharField(max_length=50, unique=True)),
                        ('period_start', models.DateField()),
                        ('period_end', models.DateField()),
                        ('subtotal', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                        ('total', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                        ('status', models.CharField(choices=[('draft', 'Draft'), ('sent', 'Sent'), ('paid', 'Paid'), ('overdue', 'Overdue'), ('cancelled', 'Cancelled')], default='draft', max_length=20)),
                        ('payment_terms_days', models.PositiveIntegerField(default=30)),
                        ('due_date', models.DateField(blank=True, null=True)),
                        ('notes', models.TextField(blank=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('sent_at', models.DateTimeField(blank=True, null=True)),
                        ('paid_at', models.DateTimeField(blank=True, null=True)),
                        ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoices', to='core.owner')),
                    ],
                    options={
                        'ordering': ['-created_at'],
                        'db_table': 'core_invoice',
                        'indexes': [
                            models.Index(fields=['owner', 'status'], name='invoice_owner_status'),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name='InvoiceLineItem',
                    fields=[
                        ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('line_type', models.CharField(choices=[('livery', 'Livery'), ('vet', 'Veterinary'), ('farrier', 'Farrier'), ('vaccination', 'Vaccination'), ('feed', 'Feed'), ('other', 'Other')], default='livery', max_length=20)),
                        ('description', models.CharField(max_length=500)),
                        ('quantity', models.DecimalField(decimal_places=2, default=Decimal('1.00'), max_digits=10, validators=[MinValueValidator(Decimal('0.00'))])),
                        ('unit_price', models.DecimalField(decimal_places=2, max_digits=10, validators=[MinValueValidator(Decimal('0.00'))])),
                        ('line_total', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                        ('share_percentage', models.DecimalField(decimal_places=2, default=Decimal('100.00'), help_text='Ownership share % at time of invoicing', max_digits=5)),
                        ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='line_items', to='invoicing.invoice')),
                        ('horse', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='invoice_items', to='core.horse')),
                        ('placement', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoice_items', to='core.placement')),
                        ('charge', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoice_items', to='billing.extracharge')),
                    ],
                    options={
                        'ordering': ['line_type', 'description'],
                        'db_table': 'core_invoicelineitem',
                    },
                ),
            ],
            database_operations=[],
        ),
    ]

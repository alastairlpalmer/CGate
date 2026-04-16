from decimal import Decimal

import core.models
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0015_move_invoice_to_invoicing'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── New fields on BusinessSettings ────────────────────────────────────
        migrations.AddField(
            model_name='businesssettings',
            name='currency_symbol',
            field=models.CharField(default='£', help_text='Symbol shown on invoices and throughout the UI', max_length=5),
        ),
        migrations.AddField(
            model_name='businesssettings',
            name='vat_rate',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='VAT percentage applied to invoices (0 = no VAT)',
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(Decimal('0.00')),
                    django.core.validators.MaxValueValidator(Decimal('100.00')),
                ],
            ),
        ),
        migrations.AddField(
            model_name='businesssettings',
            name='invoice_due_warning_days',
            field=models.PositiveSmallIntegerField(
                default=7,
                help_text="Flag invoices as 'due soon' this many days before the due date",
            ),
        ),
        migrations.AddField(
            model_name='businesssettings',
            name='farrier_revisit_weeks',
            field=models.PositiveSmallIntegerField(
                default=6,
                help_text='Default weeks between farrier visits when auto-calculating next due date',
            ),
        ),
        migrations.AddField(
            model_name='businesssettings',
            name='worm_egg_threshold',
            field=models.PositiveIntegerField(
                default=200,
                help_text='Worm egg count (EPG) above which a result is flagged as high',
            ),
        ),
        migrations.AddField(
            model_name='businesssettings',
            name='xero_invoice_status',
            field=models.CharField(
                choices=[('DRAFT', 'Draft'), ('SUBMITTED', 'Submitted'), ('AUTHORISED', 'Authorised')],
                default='DRAFT',
                help_text='Status invoices are created with when pushed to Xero',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='businesssettings',
            name='logo',
            field=models.ImageField(
                blank=True,
                help_text='PNG, JPEG, WebP or SVG. Max 5MB.',
                null=True,
                upload_to='business/',
                validators=[
                    django.core.validators.FileExtensionValidator(
                        allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'svg']
                    ),
                    core.models.validate_file_size,
                ],
            ),
        ),
        # ── SettingsChangeLog ─────────────────────────────────────────────────
        migrations.CreateModel(
            name='SettingsChangeLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('changed_at', models.DateTimeField(auto_now_add=True)),
                ('field_name', models.CharField(max_length=100)),
                ('old_value', models.TextField(blank=True)),
                ('new_value', models.TextField(blank=True)),
                (
                    'changed_by',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='settings_changes',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'verbose_name': 'Settings Change',
                'verbose_name_plural': 'Settings Changes',
                'ordering': ['-changed_at'],
            },
        ),
    ]

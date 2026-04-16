import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_expand_business_settings'),
    ]

    operations = [
        migrations.AlterField(
            model_name='businesssettings',
            name='farrier_revisit_weeks',
            field=models.PositiveSmallIntegerField(
                default=6,
                validators=[django.core.validators.MinValueValidator(1)],
                help_text='Default weeks between farrier visits when auto-calculating next due date',
            ),
        ),
    ]

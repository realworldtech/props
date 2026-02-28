"""Make Location.created_at non-nullable.

Safe to run after 0037_backfill_location_created_at which
ensures all existing rows have a value.
"""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0037_backfill_location_created_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="location",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
    ]

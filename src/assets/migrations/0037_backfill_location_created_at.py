"""Backfill Location.created_at for existing rows.

Spaces timestamps across locations ordered by pk so relative
creation order is preserved.
"""

from datetime import datetime, timedelta, timezone

from django.db import migrations


def backfill_created_at(apps, schema_editor):
    Location = apps.get_model("assets", "Location")
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    locations = Location.objects.filter(created_at__isnull=True).order_by("pk")
    for idx, loc in enumerate(locations):
        loc.created_at = base_time + timedelta(minutes=idx)
        loc.save(update_fields=["created_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0036_location_created_at"),
    ]

    operations = [
        migrations.RunPython(
            backfill_created_at,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

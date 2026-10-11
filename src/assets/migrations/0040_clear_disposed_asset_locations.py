"""S2.2.3-13: record and clear the location of assets already disposed."""

from django.db import migrations


def clear_disposed_locations(apps, schema_editor):
    Asset = apps.get_model("assets", "Asset")
    Transaction = apps.get_model("assets", "Transaction")
    disposed = Asset.objects.filter(
        status="disposed", current_location__isnull=False
    ).select_related("current_location")
    for asset in disposed:
        location = asset.current_location
        Transaction.objects.create(
            asset=asset,
            action="note",
            from_location=location,
            timestamp=asset.updated_at,
            notes=(
                f"Status: Unknown → Disposed. Last location: "
                f"{location.name}. A data update cleared the location."
            ),
        )
    disposed.update(current_location=None)


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0039_asset_search_vector"),
    ]

    operations = [
        migrations.RunPython(
            clear_disposed_locations, migrations.RunPython.noop
        ),
    ]

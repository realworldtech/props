"""Bulk operations service for assets."""

from django.contrib.auth import get_user_model
from django.db.models import F

from ..models import Asset, Category, Location

User = get_user_model()


def bulk_transfer(asset_ids: list[int], location_id: int, user) -> dict:
    """Transfer multiple assets to a location.

    Returns a dict with 'transferred' count and 'skipped' list of
    asset names that were skipped (e.g. checked-out assets).
    """
    location = Location.objects.get(pk=location_id, is_active=True)
    eligible_assets = Asset.objects.filter(
        pk__in=asset_ids,
        status="active",
        checked_out_to__isnull=True,
    )
    all_requested = Asset.objects.filter(pk__in=asset_ids)
    skipped_assets = all_requested.filter(checked_out_to__isnull=False)
    skipped_names = list(skipped_assets.values_list("name", flat=True))

    from .transactions import create_transfer

    count = 0
    for asset in eligible_assets:
        create_transfer(asset, location, user, notes="Bulk transfer")
        count += 1
    return {"transferred": count, "skipped": skipped_names}


def bulk_status_change(
    asset_ids: list[int], new_status: str, user
) -> tuple[int, list[str]]:
    """Change the status of multiple assets.

    Validates each transition individually, then applies all valid
    status changes in a single bulk_update query.

    Returns a tuple of (success_count, list of failure messages).
    """
    from .state import validate_transition

    assets = list(Asset.objects.filter(pk__in=asset_ids))
    valid_assets: list[Asset] = []
    failures: list[str] = []
    for asset in assets:
        try:
            validate_transition(asset, new_status)
            asset.status = new_status
            valid_assets.append(asset)
        except Exception as exc:
            failures.append(f"{asset.name}: {exc}")

    if valid_assets:
        Asset.objects.bulk_update(valid_assets, ["status"])

    return len(valid_assets), failures


def bulk_edit(
    asset_ids: list[int],
    category_id: int | None = None,
    location_id: int | None = None,
) -> int:
    """Bulk edit category and/or location for multiple assets.

    Uses a single queryset.update() call instead of per-object saves.

    Returns the number of assets updated.
    """
    assets = Asset.objects.filter(pk__in=asset_ids)

    update_kwargs: dict = {}

    if category_id:
        category = Category.objects.get(pk=category_id)
        update_kwargs["category"] = category

    if location_id:
        location = Location.objects.get(pk=location_id, is_active=True)
        update_kwargs["current_location"] = location

    if not update_kwargs:
        return 0

    return assets.update(**update_kwargs)


def bulk_checkout(
    asset_ids: list[int],
    borrower_id: int,
    performed_by,
    notes: str = "",
    timestamp=None,
) -> dict:
    """Check out multiple assets to a single borrower.

    Batches the home_location pre-assignment into a single query,
    then performs per-object checkout transactions.

    Returns a dict with 'checked_out' count and 'skipped' list.
    """
    from .transactions import create_checkout

    borrower = User.objects.get(pk=borrower_id)
    assets = list(
        Asset.objects.filter(pk__in=asset_ids, status__in=["active", "draft"])
    )

    # Separate checked-out assets (skip) from eligible ones
    skipped: list[str] = []
    eligible: list[Asset] = []
    for asset in assets:
        if asset.is_checked_out:
            skipped.append(asset.name)
        else:
            eligible.append(asset)

    # Batch-set home_location for eligible assets that don't have one
    needs_home = [a for a in eligible if not a.home_location]
    if needs_home:
        Asset.objects.filter(
            pk__in=[a.pk for a in needs_home],
            home_location__isnull=True,
        ).update(home_location=F("current_location"))
        # Refresh in-memory objects so create_checkout sees
        # the updated home_location
        for asset in needs_home:
            asset.home_location = asset.current_location

    count = 0
    for asset in eligible:
        create_checkout(
            asset,
            borrower,
            performed_by,
            notes=notes,
            timestamp=timestamp,
        )
        count += 1
    return {"checked_out": count, "skipped": skipped}


def bulk_checkin(
    asset_ids: list[int],
    location_id: int,
    performed_by,
    notes: str = "",
    timestamp=None,
) -> dict:
    """Check in multiple assets to a location.

    Returns a dict with 'checked_in' count and 'skipped' list.
    """
    from .transactions import create_checkin

    location = Location.objects.get(pk=location_id, is_active=True)
    assets = Asset.objects.filter(pk__in=asset_ids)
    count = 0
    skipped: list[str] = []
    for asset in assets:
        if not asset.is_checked_out:
            skipped.append(asset.name)
            continue
        create_checkin(
            asset, location, performed_by, notes=notes, timestamp=timestamp
        )
        count += 1
    return {"checked_in": count, "skipped": skipped}

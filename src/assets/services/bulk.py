"""Bulk operations service for assets."""

from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from django.db.models import F, Q

from ..models import Asset, Category, Location, Transaction

User = get_user_model()

# V267: Explicit whitelist of allowed filter field names
ALLOWED_FILTER_FIELDS = {
    "status",
    "q",
    "department",
    "category",
    "location",
    "tag",
    "condition",
    "is_kit",
}


def validate_filter_params(params: dict) -> dict:
    """V267: Validate and sanitize filter parameters.

    Strips unknown keys and empty values. Returns a clean dict
    containing only whitelisted, non-empty filter parameters.
    """
    return {
        k: v for k, v in params.items() if k in ALLOWED_FILTER_FIELDS and v
    }


def build_asset_filter_queryset(filters: dict):
    """V271: Shared queryset builder for asset filtering.

    Used by asset_list, bulk_actions, and print_all_filtered_labels
    to ensure consistent filter logic.

    Args:
        filters: Dict with keys from ALLOWED_FILTER_FIELDS.

    Returns:
        Queryset of Asset objects (not materialised).
    """
    queryset = Asset.objects.all()

    status = filters.get("status", "")
    if status:
        queryset = queryset.filter(status=status)

    q = filters.get("q", "")
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q)
            | Q(description__icontains=q)
            | Q(barcode__icontains=q)
            | Q(tags__name__icontains=q)
            | Q(
                nfc_tags__tag_id__icontains=q,
                nfc_tags__removed_at__isnull=True,
            )
        ).distinct()

    department = filters.get("department", "")
    if department:
        queryset = queryset.filter(category__department_id=department)

    category = filters.get("category", "")
    if category:
        queryset = queryset.filter(category_id=category)

    location = filters.get("location", "")
    if location:
        if location == "checked_out":
            queryset = queryset.filter(checked_out_to__isnull=False)
        else:
            queryset = queryset.filter(current_location_id=location)

    tag = filters.get("tag", "")
    if tag:
        queryset = queryset.filter(tags__id=tag)

    condition = filters.get("condition", "")
    if condition:
        queryset = queryset.filter(condition=condition)

    is_kit = filters.get("is_kit", "")
    if is_kit == "1":
        queryset = queryset.filter(is_kit=True)
    elif is_kit == "0":
        queryset = queryset.filter(is_kit=False)

    return queryset.distinct()


def build_bulk_queryset(
    asset_ids: list[int],
    select_all_matching: bool = False,
    filters: dict | None = None,
):
    """Build filtered queryset for bulk operations (L22).

    When select_all_matching is True and filters are provided,
    re-runs the filter query to find all matching assets.
    Otherwise filters by explicit asset_ids.

    Args:
        asset_ids: Explicit list of asset PKs.
        select_all_matching: If True, use filters instead of IDs.
        filters: Dict with keys like status, q, department,
            category, location, tag, condition.

    Returns:
        Queryset of Asset objects.
    """
    if select_all_matching and filters:
        queryset = Asset.objects.all()
        if filters.get("status"):
            queryset = queryset.filter(status=filters["status"])
        q = filters.get("q", "")
        if q:
            queryset = queryset.filter(
                Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(barcode__icontains=q)
                | Q(tags__name__icontains=q)
            ).distinct()
        if filters.get("department"):
            queryset = queryset.filter(
                category__department_id=filters["department"]
            )
        if filters.get("category"):
            queryset = queryset.filter(category_id=filters["category"])
        loc = filters.get("location", "")
        if loc:
            if loc == "checked_out":
                queryset = queryset.filter(checked_out_to__isnull=False)
            else:
                queryset = queryset.filter(current_location_id=loc)
        if filters.get("tag"):
            queryset = queryset.filter(tags__id=filters["tag"])
        if filters.get("condition"):
            queryset = queryset.filter(condition=filters["condition"])
        return queryset.distinct()
    return Asset.objects.filter(pk__in=asset_ids)


def bulk_transfer(asset_ids: list[int], location_id: int, user) -> dict:
    """Transfer multiple assets to a location.

    Uses bulk_create for transactions and filter().update() for
    asset location changes instead of per-object loops.

    S7.22.4: Enforces per-asset department permission checks for
    department managers.

    Returns a dict with 'transferred' count and 'skipped' list of
    asset names that were skipped (e.g. checked-out assets).
    """
    location = Location.objects.get(pk=location_id, is_active=True)
    eligible_assets = list(
        Asset.objects.filter(
            pk__in=asset_ids,
            status="active",
            checked_out_to__isnull=True,
        )
    )
    all_requested = Asset.objects.filter(pk__in=asset_ids)
    skipped_assets = all_requested.filter(checked_out_to__isnull=False)
    skipped_names = list(skipped_assets.values_list("name", flat=True))

    # S7.22.4: Per-asset department permission check for
    # department managers only. Only queries managed_departments
    # if the user is in the Department Manager group (avoids
    # extra queries for other roles).
    if (
        not user.is_superuser
        and user.groups.filter(name="Department Manager").exists()
    ):
        managed_dept_ids = set(
            user.managed_departments.values_list("pk", flat=True)
        )
        if managed_dept_ids:
            permitted = []
            for asset in eligible_assets:
                dept = asset.department
                if dept and dept.pk not in managed_dept_ids:
                    skipped_names.append(asset.name)
                else:
                    permitted.append(asset)
            eligible_assets = permitted

    if eligible_assets:
        transactions = [
            Transaction(
                asset=asset,
                user=user,
                action="transfer",
                from_location=asset.current_location,
                to_location=location,
                notes="Bulk transfer",
            )
            for asset in eligible_assets
        ]
        with db_transaction.atomic():
            Transaction.objects.bulk_create(transactions)
            Asset.objects.filter(
                pk__in=[a.pk for a in eligible_assets]
            ).update(current_location=location)

    return {
        "transferred": len(eligible_assets),
        "skipped": skipped_names,
    }


def bulk_status_change(
    asset_ids: list[int], new_status: str, user
) -> tuple[int, list[str]]:
    """Change the status of multiple assets.

    Validates each transition individually, then applies all valid
    status changes in a single bulk_update query.

    S7.17.5: Bulk transition to lost/stolen is explicitly blocked.

    Returns a tuple of (success_count, list of failure messages).
    """
    # S7.17.5: Block bulk lost/stolen transitions
    if new_status in ("lost", "stolen"):
        assets = list(Asset.objects.filter(pk__in=asset_ids))
        failures = [
            f"{a.name}: Bulk transition to '{new_status}' is not "
            f"allowed. Lost/stolen transitions require individual "
            f"notes and must be performed individually."
            for a in assets
        ]
        return 0, failures

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

    V259: Category assignment is restricted to draft assets only.
    Location assignment applies to all assets.

    Returns the number of assets updated.
    """
    count = 0

    if category_id:
        category = Category.objects.get(pk=category_id)
        # V259: Only draft assets can have category bulk-assigned
        draft_qs = Asset.objects.filter(pk__in=asset_ids, status="draft")
        count = draft_qs.update(category=category)

    if location_id:
        location = Location.objects.get(pk=location_id, is_active=True)
        all_qs = Asset.objects.filter(pk__in=asset_ids)
        loc_count = all_qs.update(current_location=location)
        count = max(count, loc_count)

    return count


def bulk_checkout(
    asset_ids: list[int],
    borrower_id: int,
    performed_by,
    notes: str = "",
    timestamp=None,
) -> dict:
    """Check out multiple assets to a single borrower.

    Uses bulk_create for transactions and bulk update for asset
    state changes.

    Returns a dict with 'checked_out' count and 'skipped' list.
    """
    extra = {}
    if timestamp:
        extra = {"timestamp": timestamp, "is_backdated": True}

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

    if eligible:
        # Batch-set home_location for eligible assets that don't
        # have one
        needs_home = [a for a in eligible if not a.home_location]
        if needs_home:
            Asset.objects.filter(
                pk__in=[a.pk for a in needs_home],
                home_location__isnull=True,
            ).update(home_location=F("current_location"))

        transactions = [
            Transaction(
                asset=asset,
                user=performed_by,
                action="checkout",
                from_location=asset.current_location,
                borrower=borrower,
                notes=notes,
                **extra,
            )
            for asset in eligible
        ]
        with db_transaction.atomic():
            Transaction.objects.bulk_create(transactions)
            Asset.objects.filter(pk__in=[a.pk for a in eligible]).update(
                checked_out_to=borrower
            )

    return {"checked_out": len(eligible), "skipped": skipped}


def bulk_checkin(
    asset_ids: list[int],
    location_id: int,
    performed_by,
    notes: str = "",
    timestamp=None,
) -> dict:
    """Check in multiple assets to a location.

    Uses bulk_create for transactions and bulk update for asset
    state changes.

    Returns a dict with 'checked_in' count and 'skipped' list.
    """
    extra = {}
    if timestamp:
        extra = {"timestamp": timestamp, "is_backdated": True}

    location = Location.objects.get(pk=location_id, is_active=True)
    assets = list(Asset.objects.filter(pk__in=asset_ids))
    skipped: list[str] = []
    eligible: list[Asset] = []
    for asset in assets:
        if not asset.is_checked_out:
            skipped.append(asset.name)
        else:
            eligible.append(asset)

    if eligible:
        transactions = [
            Transaction(
                asset=asset,
                user=performed_by,
                action="checkin",
                from_location=asset.current_location,
                to_location=location,
                notes=notes,
                **extra,
            )
            for asset in eligible
        ]
        with db_transaction.atomic():
            Transaction.objects.bulk_create(transactions)
            Asset.objects.filter(pk__in=[a.pk for a in eligible]).update(
                checked_out_to=None,
                current_location=location,
            )

    return {"checked_in": len(eligible), "skipped": skipped}

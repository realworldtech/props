"""Hold list business logic services."""

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone


def get_default_status():
    """Return the default HoldListStatus."""
    from assets.models import HoldListStatus

    return HoldListStatus.objects.filter(is_default=True).first()


def create_hold_list(name, user, **kwargs):
    """Create a new hold list."""
    from assets.models import HoldList

    status = kwargs.pop("status", None) or get_default_status()
    if not status:
        raise ValidationError("No default hold list status configured.")
    hold_list = HoldList(
        name=name,
        created_by=user,
        status=status,
        **kwargs,
    )
    hold_list.full_clean()
    hold_list.save()
    return hold_list


def add_item(hold_list, asset, user, serial=None, quantity=1, notes=""):
    """Add an asset to a hold list."""
    from assets.models import HoldListItem

    if hold_list.is_locked:
        raise ValidationError("Hold list is locked.")
    item = HoldListItem(
        hold_list=hold_list,
        asset=asset,
        serial=serial,
        quantity=quantity,
        notes=notes,
        added_by=user,
    )
    item.full_clean()
    item.save()
    return item


def remove_item(hold_list, item_id, user):
    """Remove an item from a hold list."""
    if hold_list.is_locked:
        raise ValidationError("Hold list is locked.")
    from assets.models import HoldListItem

    HoldListItem.objects.filter(hold_list=hold_list, pk=item_id).delete()


def update_pull_status(item, status, user):
    """Update pull status of a hold list item."""
    item.pull_status = status
    if status == "pulled":
        item.pulled_at = timezone.now()
        item.pulled_by = user
    item.save()


def lock_hold_list(hold_list, user):
    """Lock a hold list."""
    hold_list.is_locked = True
    hold_list.save(update_fields=["is_locked"])


def unlock_hold_list(hold_list, user):
    """Unlock a hold list."""
    hold_list.is_locked = False
    hold_list.save(update_fields=["is_locked"])


def change_status(hold_list, new_status, user):
    """Change hold list status."""
    hold_list.status = new_status
    hold_list.save(update_fields=["status", "updated_at"])


def detect_overlaps(hold_list):
    """Find assets on this hold list that overlap with other hold lists.

    Treats null end_date as extending indefinitely (S7.15.4).
    """
    from django.db.models import Q

    from assets.models import HoldListItem

    if not hold_list.start_date:
        return []

    asset_ids = hold_list.items.values_list("asset_id", flat=True)

    # Build date overlap filter that handles null end_date
    # A hold list with null end_date extends indefinitely.
    # Overlap occurs when:
    #   other.start <= self.end (or self.end is null)
    #   AND other.end >= self.start (or other.end is null)
    date_filter = Q()
    if hold_list.end_date:
        date_filter &= Q(hold_list__start_date__lte=hold_list.end_date)
    # else: no upper bound on other.start_date needed

    date_filter &= Q(hold_list__end_date__gte=hold_list.start_date) | Q(
        hold_list__end_date__isnull=True
    )

    overlapping = (
        HoldListItem.objects.filter(
            asset_id__in=asset_ids,
        )
        .filter(date_filter)
        .exclude(
            hold_list=hold_list,
        )
        .exclude(
            hold_list__status__is_terminal=True,
        )
        .select_related("asset", "hold_list")
    )

    results = list(overlapping)

    # S7.19.6: Check serial-level availability for serialised
    # assets
    for item in hold_list.items.select_related("asset"):
        asset = item.asset
        if asset.is_serialised and item.quantity > 0:
            available_serials = asset.serials.filter(
                status="active",
                checked_out_to__isnull=True,
                is_archived=False,
            ).count()
            if available_serials < item.quantity:
                results.append(
                    f"Insufficient serial availability for "
                    f"'{asset.name}': {available_serials} "
                    f"available, {item.quantity} requested."
                )

    return results


def check_asset_held(asset):
    """Check if asset is on any active (non-terminal) hold list."""
    from assets.models import HoldListItem

    return (
        HoldListItem.objects.filter(
            asset=asset,
        )
        .exclude(
            hold_list__status__is_terminal=True,
        )
        .exclude(
            pull_status="unavailable",
        )
        .exists()
    )


def get_active_hold_items(asset):
    """Return active (non-terminal) hold list items for an asset."""
    from assets.models import HoldListItem

    return (
        HoldListItem.objects.filter(
            asset=asset,
        )
        .exclude(
            hold_list__status__is_terminal=True,
        )
        .exclude(
            pull_status="unavailable",
        )
        .select_related("hold_list", "hold_list__project", "serial")
    )


def get_held_quantity(asset):
    """Return total quantity held across active hold lists.

    For non-serialised assets, sums the quantity field of all
    active (non-terminal, non-unavailable) hold list items.
    """
    from assets.models import HoldListItem

    result = (
        HoldListItem.objects.filter(
            asset=asset,
        )
        .exclude(
            hold_list__status__is_terminal=True,
        )
        .exclude(
            pull_status="unavailable",
        )
        .aggregate(total=Sum("quantity"))
    )
    return result["total"] or 0


def check_serial_held(serial):
    """Check if a specific serial is held on any active hold list."""
    from assets.models import HoldListItem

    return (
        HoldListItem.objects.filter(
            serial=serial,
        )
        .exclude(
            hold_list__status__is_terminal=True,
        )
        .exclude(
            pull_status="unavailable",
        )
        .exists()
    )


def fulfil_item(item, user):
    """Mark a hold list item as pulled/fulfilled.

    Sets pull_status to 'pulled', records pulled_at and pulled_by.
    """
    item.pull_status = "pulled"
    item.pulled_at = timezone.now()
    item.pulled_by = user
    item.save(update_fields=["pull_status", "pulled_at", "pulled_by"])
    return item


def resolve_due_date(hold_list, asset=None, transaction=None):
    """Resolve the effective due date for a hold list (L37, S2.16.1-03a).

    Per-asset cascading resolution order:
    1. Transaction.due_date (if transaction provided and has due_date).
    2. HoldListItem.due_date (if the item has a per-item due date).
    3. Hold list's own end_date (if set).
    4. ProjectDateRange matching dept+category (most specific).
    5. ProjectDateRange matching dept only.
    6. ProjectDateRange unscoped (project-wide).
    7. None if no date can be resolved.

    When an asset is provided, the function resolves per-asset using
    the asset's department and category to select the most specific
    ProjectDateRange.
    """
    # Step 1: Transaction.due_date takes highest priority
    if transaction is not None and transaction.due_date is not None:
        due = transaction.due_date
        # Return as date if it's a datetime
        if hasattr(due, "date"):
            return due.date()
        return due

    # Step 2: Hold list's own end_date
    if hold_list.end_date:
        return hold_list.end_date

    # Steps 3-6: Project date ranges with per-asset scoping
    if hold_list.project:
        asset_dept = None
        asset_cat = None
        if asset is not None:
            asset_dept = asset.department
            asset_cat = asset.category

        # Try dept+category scoped range (most specific)
        if asset_dept and asset_cat:
            ranges = hold_list.project.date_ranges.filter(
                department=asset_dept,
                category=asset_cat,
            ).order_by("-end_date")
            if ranges.exists():
                return ranges.first().end_date

        # Try dept-only scoped range
        dept = asset_dept or hold_list.department
        if dept:
            ranges = hold_list.project.date_ranges.filter(
                department=dept,
                category__isnull=True,
            ).order_by("-end_date")
            if ranges.exists():
                return ranges.first().end_date

        # Fall back to unscoped (project-wide) date range
        ranges = hold_list.project.date_ranges.filter(
            department__isnull=True,
            category__isnull=True,
        ).order_by("-end_date")
        if ranges.exists():
            return ranges.first().end_date

        # Ultimate fallback: any project date range
        ranges = hold_list.project.date_ranges.order_by("-end_date")
        if ranges.exists():
            return ranges.first().end_date

    return None


def get_effective_dates(hold_list):
    """Return the effective start and end dates for a hold list.

    Checks the hold list's own dates first, then falls back to
    the linked project's date ranges.
    """
    start = hold_list.start_date
    end = hold_list.end_date

    if not start and not end and hold_list.project:
        ranges = hold_list.project.date_ranges.order_by("start_date")
        if ranges.exists():
            start = ranges.first().start_date
            end = ranges.order_by("-end_date").first().end_date

    return start, end

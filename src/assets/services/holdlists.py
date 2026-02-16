"""Hold list business logic services."""

from django.core.exceptions import ValidationError
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
    """Find assets on this hold list that overlap with other hold lists."""
    from assets.models import HoldListItem

    if not hold_list.start_date or not hold_list.end_date:
        return []

    asset_ids = hold_list.items.values_list("asset_id", flat=True)

    overlapping = (
        HoldListItem.objects.filter(
            asset_id__in=asset_ids,
            hold_list__start_date__lte=hold_list.end_date,
            hold_list__end_date__gte=hold_list.start_date,
        )
        .exclude(
            hold_list=hold_list,
        )
        .exclude(
            hold_list__status__is_terminal=True,
        )
        .select_related("asset", "hold_list")
    )

    return list(overlapping)


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
        .exists()
    )

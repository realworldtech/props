"""Transaction creation service."""

from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction

from ..models import Asset, Location, Transaction

User = get_user_model()


def create_checkout(
    asset: Asset,
    borrower: User,
    performed_by: User,
    notes: str = "",
    timestamp=None,
) -> Transaction:
    """Check out an asset to a borrower. Returns the Transaction."""
    extra = {}
    if timestamp:
        extra = {"timestamp": timestamp, "is_backdated": True}
    txn = Transaction.objects.create(
        asset=asset,
        user=performed_by,
        action="checkout",
        from_location=asset.current_location,
        borrower=borrower,
        notes=notes,
        **extra,
    )
    asset.checked_out_to = borrower
    asset.save(update_fields=["checked_out_to"])
    return txn


def create_checkin(
    asset: Asset,
    to_location: Location,
    performed_by: User,
    notes: str = "",
    timestamp=None,
) -> Transaction:
    """Check in an asset to a location. Returns the Transaction."""
    extra = {}
    if timestamp:
        extra = {"timestamp": timestamp, "is_backdated": True}
    txn = Transaction.objects.create(
        asset=asset,
        user=performed_by,
        action="checkin",
        from_location=asset.current_location,
        to_location=to_location,
        notes=notes,
        **extra,
    )
    asset.checked_out_to = None
    asset.current_location = to_location
    asset.save(update_fields=["checked_out_to", "current_location"])
    return txn


def create_transfer(
    asset: Asset,
    to_location: Location,
    performed_by: User,
    notes: str = "",
    timestamp=None,
) -> Transaction:
    """Transfer an asset to a new location. Returns the Transaction."""
    # S7.22.1: Reject transfer to same location
    if (
        asset.current_location_id
        and to_location.pk == asset.current_location_id
    ):
        raise ValueError("Asset is already at this location.")

    extra = {}
    if timestamp:
        extra = {"timestamp": timestamp, "is_backdated": True}
    txn = Transaction.objects.create(
        asset=asset,
        user=performed_by,
        action="transfer",
        from_location=asset.current_location,
        to_location=to_location,
        notes=notes,
        **extra,
    )
    asset.current_location = to_location
    asset.save(update_fields=["current_location"])
    return txn


def create_handover(
    asset: Asset,
    new_borrower: User,
    performed_by: User,
    to_location: Location | None = None,
    notes: str = "",
    timestamp=None,
) -> Transaction:
    """Hand over a checked-out asset to a new borrower (atomically).

    Creates a single 'handover' transaction instead of a
    checkin + checkout pair.
    """
    # S7.20.2: Reject handover to the same borrower
    if asset.checked_out_to_id and asset.checked_out_to_id == new_borrower.pk:
        raise ValueError("Asset is already checked out to this person.")

    # S7.20.4: Reject handover on lost/stolen assets
    if asset.status in ("lost", "stolen"):
        raise ValueError(
            f"Cannot hand over a {asset.status} asset. "
            "Recover the asset first."
        )

    extra = {}
    if timestamp:
        extra = {"timestamp": timestamp, "is_backdated": True}

    with db_transaction.atomic():
        # S7.20.5: Use select_for_update to prevent concurrent
        # custody transfers
        locked = Asset.objects.select_for_update().get(pk=asset.pk)
        old_borrower = locked.checked_out_to
        loc = to_location or locked.current_location

        handover_notes = (
            f"Handover from {old_borrower} to "
            f"{new_borrower}. {notes}".strip()
        )
        txn = Transaction.objects.create(
            asset=locked,
            user=performed_by,
            action="handover",
            from_location=locked.current_location,
            to_location=loc,
            borrower=new_borrower,
            notes=handover_notes,
            **extra,
        )
        locked.checked_out_to = new_borrower
        if to_location:
            locked.current_location = to_location
        locked.save(update_fields=["checked_out_to", "current_location"])

        # Update the in-memory asset to match
        asset.checked_out_to = new_borrower
        if to_location:
            asset.current_location = to_location

    return txn

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
) -> tuple[Transaction, Transaction]:
    """Hand over a checked-out asset to a new borrower (atomically)."""
    extra = {}
    if timestamp:
        extra = {"timestamp": timestamp, "is_backdated": True}

    with db_transaction.atomic():
        old_borrower = asset.checked_out_to
        loc = to_location or asset.current_location

        checkin_txn = Transaction.objects.create(
            asset=asset,
            user=performed_by,
            action="checkin",
            from_location=asset.current_location,
            to_location=loc,
            notes=f"Handover: returned by {old_borrower}. {notes}".strip(),
            **extra,
        )
        checkout_txn = Transaction.objects.create(
            asset=asset,
            user=performed_by,
            action="checkout",
            from_location=loc,
            borrower=new_borrower,
            notes=f"Handover: issued to {new_borrower}. {notes}".strip(),
            **extra,
        )
        asset.checked_out_to = new_borrower
        if to_location:
            asset.current_location = to_location
        asset.save(update_fields=["checked_out_to", "current_location"])

    return checkin_txn, checkout_txn

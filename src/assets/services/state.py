"""Asset state machine and transition validation."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from ..models import Asset, Location, Transaction
from .permissions import can_change_status

RECOVERABLE_STATUSES = ("missing", "lost", "stolen")
MANUAL_STATUSES = ("missing", "lost", "stolen", "active")


def status_action_label(asset: Asset, new_status: str) -> str:
    if new_status == "active":
        return (
            "Mark as recovered"
            if asset.status == "stolen"
            else "Mark as found"
        )
    return f"Mark as {new_status}"


def status_actions(asset: Asset) -> list[tuple[str, str]]:
    """Return (status, label) for each manual change valid now."""
    return [
        (status, status_action_label(asset, status))
        for status in MANUAL_STATUSES
        if asset.can_transition_to(status)
        and (status != "active" or asset.status in RECOVERABLE_STATUSES)
    ]


def validate_transition(asset: Asset, new_status: str) -> None:
    """Validate and raise if the status transition is not allowed.

    Raises ValidationError if the transition is invalid.
    """
    if new_status == asset.status:
        return  # No-op transition is always fine

    if new_status not in dict(Asset.STATUS_CHOICES):
        raise ValidationError(f"'{new_status}' is not a valid status.")

    if not asset.can_transition_to(new_status):
        allowed = Asset.VALID_TRANSITIONS.get(asset.status, [])
        raise ValidationError(
            f"Cannot transition from '{asset.get_status_display()}' to "
            f"'{new_status}'. Allowed transitions: "
            f"{', '.join(allowed) or 'none'}."
        )

    # Cannot retire/dispose a checked-out asset (must check in first).
    # Lost/stolen ARE allowed on checked-out assets since the asset
    # is out of physical control.
    if new_status in ("retired", "disposed") and asset.is_checked_out:
        borrower = asset.checked_out_to
        borrower_name = (
            borrower.get_full_name() or borrower.username
            if borrower
            else "unknown"
        )
        raise ValidationError(
            f"Cannot change status to '{new_status}' while the asset "
            f"is checked out to {borrower_name}. Check it in first."
        )

    # S7.17.3: Recovering from lost/stolen requires a location
    if (
        asset.status in ("lost", "stolen")
        and new_status == "active"
        and not asset.current_location
    ):
        raise ValidationError(
            "A location must be set when recovering a lost/stolen "
            "asset. Please assign a location before changing status "
            "to active."
        )

    # Mandatory notes for lost/stolen transitions
    if new_status in ("lost", "stolen") and not asset.lost_stolen_notes:
        raise ValidationError(
            f"Notes are required when marking an asset as "
            f"'{new_status}'. Please provide details about "
            f"the circumstances."
        )


def transition_asset(asset: Asset, new_status: str) -> Asset:
    """Validate and perform a status transition.

    Returns the updated (saved) asset.
    Raises ValidationError if the transition is not allowed.
    """
    validate_transition(asset, new_status)
    asset.status = new_status
    asset.full_clean()
    asset.save(update_fields=["status"])
    return asset


def _append_note(existing: str, entry: str) -> str:
    return f"{existing}\n---\n{entry}" if existing else entry


def change_status(
    asset: Asset,
    new_status: str,
    user,
    *,
    notes: str = "",
    location: Location | None = None,
    confirm_checked_out: bool = False,
) -> Asset:
    """Mark an asset missing, lost or stolen, or recover it to active.

    Records the change as a note transaction (S7.17.1, S7.17.3).
    Raises PermissionDenied or ValidationError.
    """
    if not can_change_status(user, asset):
        raise PermissionDenied
    if not asset.can_transition_to(new_status):
        raise ValidationError(
            f"Cannot transition from '{asset.get_status_display()}' to "
            f"'{new_status}'."
        )

    notes = notes.strip()
    old_status = asset.status
    old_location = asset.current_location
    is_recovery = old_status in RECOVERABLE_STATUSES and new_status == "active"

    # S2.2.3-07: each loss needs its own notes, not old ones.
    if new_status in ("lost", "stolen") and not notes:
        raise ValidationError(
            f"Notes are required when marking an asset as "
            f"'{new_status}'. Please provide details about "
            f"the circumstances."
        )

    # S7.5.4: explicit confirmation for a checked-out asset.
    if (
        new_status == "missing"
        and asset.is_checked_out
        and not confirm_checked_out
    ):
        raise ValidationError(
            "This asset is checked out. Confirm that you want to mark "
            "it as missing."
        )

    # S2.2.3-04, S7.17.3: recovery places the asset somewhere.
    if is_recovery and (location is None or not location.is_active):
        raise ValidationError(
            "A location must be set when an asset is found or recovered."
        )

    with transaction.atomic():
        if new_status in ("lost", "stolen"):
            asset.lost_stolen_notes = _append_note(
                asset.lost_stolen_notes, notes
            )
        elif is_recovery and old_status in ("lost", "stolen") and notes:
            asset.lost_stolen_notes = _append_note(
                asset.lost_stolen_notes, f"Recovered: {notes}"
            )
        if is_recovery:
            asset.current_location = location
        validate_transition(asset, new_status)
        asset.status = new_status
        asset.save(
            update_fields=["status", "lost_stolen_notes", "current_location"]
        )

        labels = dict(Asset.STATUS_CHOICES)
        summary = f"Status: {labels[old_status]} → {labels[new_status]}."
        Transaction.objects.create(
            asset=asset,
            user=user,
            action="note",
            from_location=old_location,
            to_location=location if is_recovery else None,
            notes=f"{summary} {notes}".strip(),
        )
    return asset

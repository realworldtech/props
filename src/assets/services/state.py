"""Asset state machine and transition validation."""

from django.core.exceptions import ValidationError

from ..models import Asset


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
        raise ValidationError(
            f"Cannot change status to '{new_status}' while the asset "
            f"is checked out. Check it in first."
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

"""Serial unit CRUD services for serialised assets."""

from django.core.exceptions import ValidationError
from django.db.models import Count, QuerySet

from assets.models import AssetSerial

from .barcode import (
    generate_code128_image,
    generate_serial_barcode_string,
    validate_cross_table_barcode,
)


def create_serial(asset, serial_number: str, **kwargs) -> AssetSerial:
    """Create a new serial unit for a serialised asset.

    Generates a barcode automatically if not provided.
    """
    if not asset.is_serialised:
        raise ValidationError(
            "Cannot create serial units for a non-serialised asset."
        )

    barcode = kwargs.pop("barcode", None)
    if not barcode:
        # Determine next index
        existing_count = asset.serials.count()
        for i in range(existing_count + 1, existing_count + 100):
            candidate = generate_serial_barcode_string(asset.barcode, i)
            if validate_cross_table_barcode(candidate):
                barcode = candidate
                break
        if not barcode:
            raise ValidationError(
                "Could not generate a unique serial barcode."
            )
    else:
        if not validate_cross_table_barcode(barcode):
            raise ValidationError(
                {"barcode": "This barcode is already in use."}
            )

    serial = AssetSerial(
        asset=asset,
        serial_number=serial_number,
        barcode=barcode,
        **kwargs,
    )
    serial.full_clean()
    serial.save()

    # Generate barcode image
    if serial.barcode and not serial.barcode_image:
        image_content = generate_code128_image(serial.barcode)
        serial.barcode_image.save(
            f"{serial.barcode}.png",
            image_content,
            save=True,
        )

    return serial


def update_serial(serial: AssetSerial, **kwargs) -> AssetSerial:
    """Update fields on a serial unit."""
    for field, value in kwargs.items():
        setattr(serial, field, value)
    serial.full_clean()
    serial.save()
    return serial


def archive_serial(serial: AssetSerial) -> AssetSerial:
    """Archive a serial unit (soft delete)."""
    serial.is_archived = True
    serial.save(update_fields=["is_archived", "updated_at"])
    return serial


def restore_serial(serial: AssetSerial) -> AssetSerial:
    """Restore an archived serial unit."""
    # Re-validate barcode uniqueness
    if serial.barcode:
        if not validate_cross_table_barcode(
            serial.barcode, exclude_serial_pk=serial.pk
        ):
            raise ValidationError(
                {
                    "barcode": "Cannot restore: barcode is now "
                    "in use by another record."
                }
            )
    serial.is_archived = False
    serial.save(update_fields=["is_archived", "updated_at"])
    return serial


def get_available_serials(asset) -> QuerySet:
    """Return active, non-checked-out, non-archived serials."""
    return asset.serials.filter(
        status="active",
        checked_out_to__isnull=True,
        is_archived=False,
    )


def convert_to_serialised(asset, user) -> dict:
    """Convert a non-serialised asset to serialised.

    Returns an impact summary dict describing consequences.
    Does NOT auto-create serials.
    """
    if asset.is_serialised:
        raise ValidationError("Asset is already serialised.")

    impact = {
        "current_quantity": asset.quantity,
        "active_checkouts": 1 if asset.checked_out_to else 0,
        "warnings": [],
    }

    if asset.checked_out_to:
        impact["warnings"].append(
            "This asset is currently checked out. Existing checkout "
            "transactions will reference the parent without serial "
            "specificity."
        )

    from assets.models import AssetKit

    kit_memberships = AssetKit.objects.filter(component=asset).count()
    if kit_memberships:
        impact["warnings"].append(
            f"This asset is a component of {kit_memberships} kit(s). "
            "Quantity will be 0 until serials are added."
        )

    return impact


def apply_convert_to_serialised(asset, user) -> None:
    """Apply the conversion to serialised (after user confirms)."""
    if asset.is_serialised:
        raise ValidationError("Asset is already serialised.")

    asset.is_serialised = True
    asset.save(update_fields=["is_serialised"])

    from assets.models import Transaction

    Transaction.objects.create(
        asset=asset,
        user=user,
        action="note",
        notes="Converted from non-serialised to serialised.",
    )


def convert_to_non_serialised(asset, user) -> dict:
    """Build impact summary for converting serialised to non-serialised.

    Returns impact dict. Does NOT perform the conversion.
    """
    if not asset.is_serialised:
        raise ValidationError("Asset is already non-serialised.")

    serials = asset.serials.filter(is_archived=False)
    active = serials.filter(status="active").count()
    checked_out = serials.filter(checked_out_to__isnull=False).count()
    lost_stolen = serials.filter(status__in=["lost", "stolen"]).count()
    total = serials.count()

    from assets.models import AssetKit

    kit_pins = AssetKit.objects.filter(serial__in=serials).select_related(
        "kit"
    )
    affected_kits = [f"{kp.kit.name}" for kp in kit_pins]

    serial_barcodes = list(
        serials.exclude(barcode="")
        .exclude(barcode__isnull=True)
        .values_list("barcode", flat=True)
    )

    impact = {
        "total_serials": total,
        "active_serials": active,
        "checked_out_serials": checked_out,
        "lost_stolen_serials": lost_stolen,
        "suggested_quantity": active + checked_out,
        "affected_kits": affected_kits,
        "serial_barcodes_count": len(serial_barcodes),
        "warnings": [],
        "requires_double_confirm": checked_out > 0,
    }

    if checked_out:
        impact["warnings"].append(
            f"{checked_out} serial(s) are currently checked out. "
            "Conversion is blocked unless you explicitly override."
        )
    if affected_kits:
        impact["warnings"].append(
            "Kit component pins will be cleared for: "
            f"{', '.join(affected_kits)}"
        )
    if serial_barcodes:
        impact["warnings"].append(
            f"{len(serial_barcodes)} serial barcode(s) will become "
            "inactive."
        )

    return impact


def apply_convert_to_non_serialised(
    asset, user, adjusted_quantity=None, override_checkout=False
) -> None:
    """Apply conversion to non-serialised (after user confirms)."""
    if not asset.is_serialised:
        raise ValidationError("Asset is already non-serialised.")

    serials = asset.serials.filter(is_archived=False)
    checked_out = serials.filter(checked_out_to__isnull=False).count()

    if checked_out and not override_checkout:
        raise ValidationError(
            f"{checked_out} serial(s) are checked out. "
            "Use override_checkout=True to proceed."
        )

    active_count = serials.filter(status__in=["active"]).count() + checked_out
    quantity = (
        adjusted_quantity if adjusted_quantity is not None else active_count
    )

    from assets.models import AssetKit

    AssetKit.objects.filter(serial__in=serials).update(serial=None)

    serials.update(is_archived=True)

    asset.is_serialised = False
    asset.quantity = max(quantity, 0)
    asset.save(update_fields=["is_serialised", "quantity"])

    from assets.models import Transaction

    Transaction.objects.create(
        asset=asset,
        user=user,
        action="note",
        notes=(
            f"Converted from serialised to non-serialised. "
            f"Archived {serials.count()} serial(s). "
            f"Quantity set to {asset.quantity}."
        ),
    )


def get_archived_serials(asset):
    """Return archived serials for potential restoration."""
    return asset.serials.filter(is_archived=True)


def restore_archived_serials(asset, user) -> dict:
    """Attempt to restore all archived serials when converting back.

    Returns dict with restored count and any barcode conflicts.
    """
    archived = asset.serials.filter(is_archived=True)
    if not archived.exists():
        return {"restored": 0, "conflicts": []}

    conflicts = []
    restored = 0

    for serial in archived:
        if serial.barcode:
            if not validate_cross_table_barcode(
                serial.barcode, exclude_serial_pk=serial.pk
            ):
                conflicts.append(
                    {
                        "serial": serial.serial_number,
                        "barcode": serial.barcode,
                    }
                )
                serial.barcode = None
                serial.barcode_image = None

        serial.is_archived = False
        serial.save(
            update_fields=[
                "is_archived",
                "barcode",
                "barcode_image",
                "updated_at",
            ]
        )
        restored += 1

    return {"restored": restored, "conflicts": conflicts}


def get_serial_summary(asset) -> dict:
    """Return a summary breakdown of serial units."""
    serials = asset.serials.filter(is_archived=False)
    status_counts = dict(
        serials.values_list("status").annotate(count=Count("id"))
    )
    condition_counts = dict(
        serials.values_list("condition").annotate(count=Count("id"))
    )
    checked_out = serials.filter(checked_out_to__isnull=False).count()
    return {
        "total": serials.count(),
        "by_status": status_counts,
        "by_condition": condition_counts,
        "checked_out": checked_out,
        "available": serials.filter(
            status="active",
            checked_out_to__isnull=True,
        ).count(),
    }

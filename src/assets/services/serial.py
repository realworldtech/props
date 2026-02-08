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

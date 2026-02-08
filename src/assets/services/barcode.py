"""Barcode and QR code generation services."""

import uuid
from io import BytesIO

import barcode as python_barcode
from barcode.writer import ImageWriter

from django.conf import settings
from django.core.files.base import ContentFile


def generate_barcode_string():
    """Generate a unique barcode string: PREFIX-8HEXCHARS."""
    prefix = getattr(settings, "BARCODE_PREFIX", "ASSET")
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def generate_code128_image(barcode_text: str) -> ContentFile:
    """Generate a Code128 barcode image (PNG) for the given text.

    Returns a ContentFile suitable for saving to an ImageField.
    """
    code128 = python_barcode.get_barcode_class("code128")
    buffer = BytesIO()
    code = code128(barcode_text, writer=ImageWriter())
    code.write(
        buffer,
        options={
            "module_width": 0.4,
            "module_height": 15,
            "font_size": 10,
            "text_distance": 5,
            "quiet_zone": 6.5,
        },
    )
    return ContentFile(buffer.getvalue())


def generate_qr_image(
    data: str, box_size: int = 6, border: int = 2
) -> ContentFile:
    """Generate a QR code image (PNG) encoding the given data.

    Returns a ContentFile suitable for saving or serving.
    """
    try:
        import qrcode
    except ImportError:
        return None

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)

    buffer = BytesIO()
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(buffer, format="PNG")
    return ContentFile(buffer.getvalue())


def generate_serial_barcode_string(
    asset_barcode: str, serial_index: int
) -> str:
    """Generate a serial barcode: {ASSET_BARCODE}-S{NNN}."""
    return f"{asset_barcode}-S{serial_index:03d}"


def validate_cross_table_barcode(
    barcode_value: str,
    exclude_asset_pk=None,
    exclude_serial_pk=None,
) -> bool:
    """Check barcode is unique across Asset and AssetSerial tables.

    Returns True if the barcode is available (no collision).
    """
    from assets.models import Asset, AssetSerial

    asset_qs = Asset.objects.filter(barcode=barcode_value)
    if exclude_asset_pk:
        asset_qs = asset_qs.exclude(pk=exclude_asset_pk)
    if asset_qs.exists():
        return False

    serial_qs = AssetSerial.objects.filter(barcode=barcode_value)
    if exclude_serial_pk:
        serial_qs = serial_qs.exclude(pk=exclude_serial_pk)
    if serial_qs.exists():
        return False

    return True


def get_asset_url(barcode_text: str) -> str:
    """Build the public asset URL from a barcode.

    Returns the canonical /a/{barcode}/ path.
    """
    site_url = getattr(settings, "SITE_URL", "")
    return f"{site_url}/a/{barcode_text}/"

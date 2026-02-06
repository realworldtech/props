"""Zebra ZPL label generation and network printing."""

import logging
import socket

from django.conf import settings

logger = logging.getLogger(__name__)


def generate_zpl(
    barcode_text: str,
    asset_name: str,
    category_name: str = "",
) -> str:
    """Generate ZPL II markup for a 62mm x 29mm label.

    Includes Code128 barcode and human-readable text.
    """
    # Truncate name to fit label width (~30 chars at font size used)
    name_truncated = asset_name[:30]
    cat_truncated = category_name[:25] if category_name else ""

    zpl = "^XA\n"
    # Label size: 62mm x 29mm ≈ 492 x 232 dots at 203dpi
    zpl += "^PW492\n"
    zpl += "^LL232\n"

    # Asset name at top
    zpl += f"^FO20,20^A0N,28,28^FD{name_truncated}^FS\n"

    # Category below name
    if cat_truncated:
        zpl += f"^FO20,55^A0N,20,20^FD{cat_truncated}^FS\n"

    # Code128 barcode
    zpl += f"^FO20,85^BCN,80,Y,N,N^FD{barcode_text}^FS\n"

    # Human-readable barcode text below the barcode
    zpl += f"^FO20,195^A0N,22,22^FD{barcode_text}^FS\n"

    zpl += "^XZ\n"
    return zpl


def print_zpl(zpl: str) -> bool:
    """Send ZPL data to a Zebra network printer via TCP.

    Uses ZEBRA_PRINTER_HOST and ZEBRA_PRINTER_PORT from settings.
    Returns True on success, False on failure.
    """
    host = getattr(settings, "ZEBRA_PRINTER_HOST", "")
    port = getattr(settings, "ZEBRA_PRINTER_PORT", 9100)

    if not host:
        logger.error("ZEBRA_PRINTER_HOST not configured")
        return False

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect((host, port))
            sock.sendall(zpl.encode("utf-8"))
        logger.info("ZPL sent to %s:%s", host, port)
        return True
    except (OSError, socket.timeout) as e:
        logger.error("Failed to print to %s:%s: %s", host, port, e)
        return False

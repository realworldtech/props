"""Print job dispatch service (§4.3.3.5).

Dispatches print jobs to connected print clients via channel layer.
Checks connectivity before dispatching and fails jobs immediately
if the target client is disconnected.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from assets.models import PrintClient

logger = logging.getLogger(__name__)


def dispatch_print_job(print_request):
    """Dispatch a PrintRequest to its target print client.

    Checks if the client is connected before sending. If
    disconnected, fails the job immediately with an error.

    Args:
        print_request: A PrintRequest instance in 'pending' status.

    Returns:
        True if the job was dispatched, False if it failed.
    """
    pc = print_request.print_client
    if pc is None:
        print_request.transition_to(
            "failed", error_message="No print client assigned"
        )
        return False

    # Refresh from DB to get current connection state
    try:
        pc.refresh_from_db()
    except PrintClient.DoesNotExist:
        print_request.transition_to(
            "failed",
            error_message="Print client no longer exists",
        )
        return False

    if not pc.is_connected:
        print_request.transition_to(
            "failed",
            error_message="Client disconnected",
        )
        return False

    asset = print_request.asset
    asset_name = ""
    category_name = ""
    department_name = ""
    barcode_val = ""
    qr_content = ""

    if asset:
        asset_name = (asset.name or "")[:30]
        barcode_val = asset.barcode or ""
        if asset.category:
            category_name = asset.category.name or ""
            if asset.category.department:
                department_name = asset.category.department.name or ""
        qr_content = f"/a/{barcode_val}/"

    channel_layer = get_channel_layer()
    group_name = f"print_client_active_{pc.pk}"

    message = {
        "type": "print.job",
        "job_id": str(print_request.job_id),
        "printer_id": print_request.printer_id,
        "barcode": barcode_val,
        "asset_name": asset_name,
        "category_name": category_name,
        "department_name": department_name,
        "qr_content": qr_content,
        "quantity": print_request.quantity,
    }

    async_to_sync(channel_layer.group_send)(group_name, message)

    return True

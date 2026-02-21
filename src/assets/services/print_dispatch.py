"""Print job dispatch service (§4.3.3.5).

Dispatches print jobs to connected print clients via channel layer.
Checks connectivity before dispatching and fails jobs immediately
if the target client is disconnected.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from django.conf import settings
from django.utils import timezone

from assets.models import PrintClient, PrintRequest

logger = logging.getLogger(__name__)


def dispatch_print_job(print_request, site_url=None):
    """Dispatch a PrintRequest to its target print client.

    Checks if the client is connected before sending. If
    disconnected, fails the job immediately with an error.

    Args:
        print_request: A PrintRequest instance in 'pending' status.
        site_url: Optional base URL (e.g. "https://example.com").
            Overrides the SITE_URL setting for qr_content generation.

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

    # V28: Validate printer_id against client's printers list
    printer_ids = {
        p.get("id") for p in (pc.printers or []) if isinstance(p, dict)
    }
    if print_request.printer_id not in printer_ids:
        print_request.transition_to(
            "failed",
            error_message=(
                f"Printer '{print_request.printer_id}' not found "
                f"on client '{pc.name}'"
            ),
        )
        return False

    label_type = getattr(print_request, "label_type", "asset") or "asset"

    # v2 gate: location labels require protocol v2+
    if label_type == "location" and pc.protocol_version < "2":
        print_request.transition_to(
            "failed",
            error_message=(
                "Location labels require protocol v2+. "
                f"Client '{pc.name}' is v{pc.protocol_version}."
            ),
        )
        return False

    channel_layer = get_channel_layer()
    group_name = f"print_client_active_{pc.pk}"

    if label_type == "location":
        location = print_request.location
        location_name = ""
        location_description = ""
        location_categories = ""
        location_departments = ""
        qr_content = ""
        if location:
            location_name = location.name or ""
            location_description = location.description or ""
            # Derive categories/departments from assets at location
            from assets.models import Asset

            loc_assets = Asset.objects.filter(
                current_location=location, status="active"
            ).select_related("category__department")
            cats = set()
            depts = set()
            for a in loc_assets:
                if a.category:
                    cats.add(a.category.name)
                    if a.category.department:
                        depts.add(a.category.department.name)
            location_categories = ", ".join(sorted(cats))
            location_departments = ", ".join(sorted(depts))
            base_url = site_url or getattr(settings, "SITE_URL", "")
            if base_url:
                qr_content = (
                    f"{base_url.rstrip('/')}/locations/" f"{location.pk}/"
                )
            else:
                qr_content = f"/locations/{location.pk}/"

        message = {
            "type": "print.job",
            "job_id": str(print_request.job_id),
            "printer_id": print_request.printer_id,
            "label_type": "location",
            "location_name": location_name,
            "location_description": location_description,
            "location_categories": location_categories,
            "location_departments": location_departments,
            "qr_content": qr_content,
            "quantity": print_request.quantity,
        }
    else:
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
            # V30/V31: qr_content must be full URL
            base_url = site_url or getattr(settings, "SITE_URL", "")
            if base_url:
                qr_content = f"{base_url.rstrip('/')}/a/{barcode_val}/"
            else:
                qr_content = f"/a/{barcode_val}/"

        message = {
            "type": "print.job",
            "job_id": str(print_request.job_id),
            "printer_id": print_request.printer_id,
            "label_type": "asset",
            "barcode": barcode_val,
            "asset_name": asset_name,
            "category_name": category_name,
            "department_name": department_name,
            "qr_content": qr_content,
            "quantity": print_request.quantity,
        }

    # V40: Catch send failures and transition to failed
    try:
        async_to_sync(channel_layer.group_send)(group_name, message)
    except Exception as exc:
        logger.exception(
            "Failed to send print job %s: %s",
            print_request.job_id,
            exc,
        )
        print_request.transition_to(
            "failed",
            error_message=f"Send failed: {exc}",
        )
        return False

    return True


def cleanup_stale_print_jobs(timeout_seconds=300):
    """Transition timed-out print jobs to failed status.

    Jobs in 'sent' or 'acked' status that have exceeded the
    timeout are marked as failed.

    Args:
        timeout_seconds: Number of seconds after which a job
            is considered stale. Default 300 (5 minutes).

    Returns:
        Number of jobs that were marked as failed.
    """
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(seconds=timeout_seconds)
    stale_jobs = PrintRequest.objects.filter(
        status__in=["sent", "acked"],
        sent_at__lt=cutoff,
    )

    count = 0
    for pr in stale_jobs:
        pr.transition_to(
            "failed",
            error_message=(
                f"Timeout: client did not respond "
                f"within {timeout_seconds}s"
            ),
        )
        count += 1

    return count

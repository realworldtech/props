"""Kit checkout/check-in cascade services."""

from django.core.exceptions import ValidationError
from django.utils import timezone

from assets.models import AssetKit, AssetSerial, Transaction


def kit_checkout(
    kit_asset,
    borrower,
    user,
    *,
    destination=None,
    selected_optionals=None,
):
    """Checkout a kit with all required + selected optional components.

    Creates a Transaction per component sharing the same timestamp.
    Returns list of transactions created.
    """
    if not kit_asset.is_kit:
        raise ValidationError("Asset is not a kit.")

    components = AssetKit.objects.filter(kit=kit_asset).select_related(
        "component", "serial"
    )

    # Separate required and optional
    required = [c for c in components if c.is_required]
    optional = [c for c in components if not c.is_required]

    if selected_optionals is None:
        selected_optionals = []

    # Check availability of required components
    unavailable = []
    for comp in required:
        if not _is_component_available(comp):
            unavailable.append(comp)

    if unavailable:
        names = ", ".join(c.component.name for c in unavailable)
        raise ValidationError(f"Required components unavailable: {names}")

    now = timezone.now()
    kit_ref = f"Kit checkout: {kit_asset.name} ({kit_asset.barcode})"
    transactions = []

    # Checkout required components
    for comp in required:
        txns = _checkout_component(
            comp, borrower, user, now, kit_ref, destination
        )
        transactions.extend(txns)

    # Checkout selected optional components
    for comp in optional:
        if comp.pk in selected_optionals:
            txns = _checkout_component(
                comp, borrower, user, now, kit_ref, destination
            )
            transactions.extend(txns)

    # Mark kit itself as checked out
    kit_asset.checked_out_to = borrower
    if destination:
        kit_asset.current_location = destination
    kit_asset.save(update_fields=["checked_out_to", "current_location"])

    Transaction.objects.create(
        asset=kit_asset,
        user=user,
        action="checkout",
        to_location=destination,
        borrower=borrower,
        notes=f"Kit checked out to {borrower}",
        timestamp=now,
    )

    return transactions


def _is_component_available(kit_component):
    """Check if a kit component is available for checkout."""
    asset = kit_component.component
    if kit_component.serial:
        serial = kit_component.serial
        return (
            serial.status == "active"
            and serial.checked_out_to is None
            and not serial.is_archived
        )
    if asset.is_serialised:
        return asset.available_count >= kit_component.quantity
    return asset.checked_out_to is None


def _checkout_component(comp, borrower, user, timestamp, kit_ref, destination):
    """Checkout a single component (handles serialised and
    non-serialised)."""
    asset = comp.component
    transactions = []

    if comp.serial:
        # Specific serial pinned
        serial = comp.serial
        serial.checked_out_to = borrower
        if destination:
            serial.current_location = destination
        serial.save(update_fields=["checked_out_to", "current_location"])
        txn = Transaction.objects.create(
            asset=asset,
            serial=serial,
            user=user,
            action="checkout",
            to_location=destination,
            borrower=borrower,
            notes=kit_ref,
            timestamp=timestamp,
        )
        transactions.append(txn)
    elif asset.is_serialised:
        # Pick available serials
        available = AssetSerial.objects.filter(
            asset=asset,
            status="active",
            checked_out_to__isnull=True,
            is_archived=False,
        )[: comp.quantity]
        for serial in available:
            serial.checked_out_to = borrower
            if destination:
                serial.current_location = destination
            serial.save(update_fields=["checked_out_to", "current_location"])
            txn = Transaction.objects.create(
                asset=asset,
                serial=serial,
                user=user,
                action="checkout",
                to_location=destination,
                borrower=borrower,
                notes=kit_ref,
                timestamp=timestamp,
            )
            transactions.append(txn)
    else:
        # Non-serialised
        asset.checked_out_to = borrower
        if destination:
            asset.current_location = destination
        asset.save(update_fields=["checked_out_to", "current_location"])
        txn = Transaction.objects.create(
            asset=asset,
            user=user,
            action="checkout",
            to_location=destination,
            borrower=borrower,
            notes=kit_ref,
            quantity=comp.quantity,
            timestamp=timestamp,
        )
        transactions.append(txn)

    # Recursive: if component is also a kit, checkout its components
    if asset.is_kit:
        sub_components = AssetKit.objects.filter(kit=asset)
        for sub_comp in sub_components:
            if sub_comp.is_required and _is_component_available(sub_comp):
                sub_txns = _checkout_component(
                    sub_comp,
                    borrower,
                    user,
                    timestamp,
                    kit_ref,
                    destination,
                )
                transactions.extend(sub_txns)

    return transactions


def kit_checkin(kit_asset, user, to_location=None):
    """Check in all components of a kit.

    Returns list of transactions created.
    """
    if not kit_asset.is_kit:
        raise ValidationError("Asset is not a kit.")

    now = timezone.now()
    kit_ref = f"Kit check-in: {kit_asset.name} ({kit_asset.barcode})"
    transactions = []

    components = AssetKit.objects.filter(kit=kit_asset).select_related(
        "component", "serial"
    )

    for comp in components:
        txns = _checkin_component(comp, user, now, kit_ref, to_location)
        transactions.extend(txns)

    # Check in kit itself
    kit_asset.checked_out_to = None
    if to_location:
        kit_asset.current_location = to_location
    kit_asset.save(update_fields=["checked_out_to", "current_location"])

    Transaction.objects.create(
        asset=kit_asset,
        user=user,
        action="checkin",
        to_location=to_location,
        notes="Kit checked in",
        timestamp=now,
    )

    return transactions


def _checkin_component(comp, user, timestamp, kit_ref, to_location):
    """Check in a single component."""
    asset = comp.component
    transactions = []

    if comp.serial:
        serial = comp.serial
        if serial.checked_out_to:
            serial.checked_out_to = None
            if to_location:
                serial.current_location = to_location
            serial.save(update_fields=["checked_out_to", "current_location"])
            txn = Transaction.objects.create(
                asset=asset,
                serial=serial,
                user=user,
                action="checkin",
                to_location=to_location,
                notes=kit_ref,
                timestamp=timestamp,
            )
            transactions.append(txn)
    elif asset.is_serialised:
        # Check in all checked-out serials for this asset
        checked_out = asset.serials.filter(
            checked_out_to__isnull=False,
            is_archived=False,
        )
        for serial in checked_out:
            serial.checked_out_to = None
            if to_location:
                serial.current_location = to_location
            serial.save(update_fields=["checked_out_to", "current_location"])
            txn = Transaction.objects.create(
                asset=asset,
                serial=serial,
                user=user,
                action="checkin",
                to_location=to_location,
                notes=kit_ref,
                timestamp=timestamp,
            )
            transactions.append(txn)
    else:
        if asset.checked_out_to:
            asset.checked_out_to = None
            if to_location:
                asset.current_location = to_location
            asset.save(update_fields=["checked_out_to", "current_location"])
            txn = Transaction.objects.create(
                asset=asset,
                user=user,
                action="checkin",
                to_location=to_location,
                notes=kit_ref,
                timestamp=timestamp,
            )
            transactions.append(txn)

    # Recursive for nested kits
    if asset.is_kit:
        sub_components = AssetKit.objects.filter(kit=asset)
        for sub_comp in sub_components:
            sub_txns = _checkin_component(
                sub_comp, user, timestamp, kit_ref, to_location
            )
            transactions.extend(sub_txns)

    return transactions


def check_serial_kit_restriction(serial):
    """Check if a serial is restricted from independent checkout.

    Returns (blocked: bool, reason: str).
    A serial in a non-checked-out kit cannot be independently
    checked out.
    """
    memberships = AssetKit.objects.filter(serial=serial).select_related("kit")

    for membership in memberships:
        kit = membership.kit
        if kit.checked_out_to is None:
            return True, (
                f"Serial is part of kit '{kit.name}' which is not "
                f"checked out. Check out the kit instead."
            )

    return False, ""

"""Asset merge service for combining duplicate records."""

from ..models import Asset, AssetImage, AssetSerial, NFCTag, Transaction
from .permissions import get_user_role


def merge_assets(primary: Asset, duplicates: list[Asset], user) -> Asset:
    """Merge duplicate assets into the primary asset.

    Moves images, NFC tags, and transactions from duplicates to primary.
    Disposes the duplicate records.
    Returns the updated primary asset.

    Raises ValueError if any asset is checked out (§7.1.4).
    Raises ValueError if dept_manager tries to merge across departments.
    """
    # V32: restrict cross-department merge for department managers
    role = get_user_role(user)
    if role == "department_manager":
        managed_dept_ids = set(
            user.managed_departments.values_list("pk", flat=True)
        )
        all_assets = [primary] + list(duplicates)
        for asset in all_assets:
            if (
                asset.department
                and asset.department.pk not in managed_dept_ids
            ):
                raise ValueError(
                    f"You do not manage the department for asset "
                    f"'{asset.name}'. Department managers can only "
                    f"merge assets within their departments."
                )

    if primary.is_checked_out:
        raise ValueError(
            f"Primary asset '{primary.name}' is checked out. "
            "Check it in before merging."
        )
    for dup in duplicates:
        if dup.pk == primary.pk:
            continue
        if dup.is_checked_out:
            raise ValueError(
                f"Asset '{dup.name}' is checked out. "
                "Check it in before merging."
            )

    for dup in duplicates:
        if dup.pk == primary.pk:
            continue

        # Move images
        AssetImage.objects.filter(asset=dup).update(asset=primary)

        # Move active NFC tags
        NFCTag.objects.filter(asset=dup, removed_at__isnull=True).update(
            asset=primary
        )

        # V33: Deactivate duplicate NFC tags (same tag_id on primary)
        from django.utils import timezone

        primary_active_tag_ids = set(
            NFCTag.objects.filter(
                asset=primary, removed_at__isnull=True
            ).values_list("tag_id", flat=True)
        )
        dupes = NFCTag.objects.filter(
            asset=primary,
            removed_at__isnull=True,
            tag_id__in=primary_active_tag_ids,
        ).order_by("tag_id", "assigned_at")
        # For each tag_id, keep only the earliest assignment, deactivate rest
        seen_tag_ids = set()
        for nfc in dupes:
            if nfc.tag_id in seen_tag_ids:
                nfc.removed_at = timezone.now()
                nfc.notes = (
                    f"{nfc.notes}\nDeactivated: duplicate after merge".strip()
                )
                nfc.save(update_fields=["removed_at", "notes"])
            else:
                seen_tag_ids.add(nfc.tag_id)

        # Move transactions
        Transaction.objects.filter(asset=dup).update(asset=primary)

        # Merge tags
        for tag in dup.tags.all():
            primary.tags.add(tag)

        # Concatenate descriptions
        if dup.description:
            if primary.description:
                primary.description += f"\n---\n{dup.description}"
            else:
                primary.description = dup.description

        # Concatenate notes
        if dup.notes:
            if primary.notes:
                primary.notes += f"\n---\n{dup.notes}"
            else:
                primary.notes = dup.notes

        # Fill in missing fields from duplicate
        if not primary.category and dup.category:
            primary.category = dup.category
        if not primary.current_location and dup.current_location:
            primary.current_location = dup.current_location
        if not primary.purchase_price and dup.purchase_price:
            primary.purchase_price = dup.purchase_price
        if not primary.estimated_value and dup.estimated_value:
            primary.estimated_value = dup.estimated_value

        # Sum quantities
        if dup.quantity and dup.quantity > 0:
            primary.quantity = (primary.quantity or 1) + dup.quantity

        # Move serials from duplicate to primary
        AssetSerial.objects.filter(asset=dup).update(asset=primary)

        # Dispose the duplicate
        dup.status = "disposed"
        dup.notes = (
            f"{dup.notes}\n\nMerged into {primary.barcode} "
            f"by {user}".strip()
        )
        dup.save()

        # Clear duplicate barcode after save (using direct DB update to
        # avoid save() regeneration)
        Asset.objects.filter(pk=dup.pk).update(barcode="", barcode_image="")

    primary.save()
    return primary

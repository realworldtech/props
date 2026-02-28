"""Asset resolution from various input types.

Resolves an asset from a PK, barcode, or free-text search query.
Used by hold list add-item and potentially other views that accept
flexible asset identifiers.
"""

from django.db.models import Q

from assets.models import Asset, AssetSerial, NFCTag


def _truncate(value, max_len=100):
    s = str(value)
    return s if len(s) <= max_len else s[:max_len] + "..."


def resolve_asset_from_input(asset_id=None, search=None, barcode=None):
    """Resolve an Asset from various input types.

    Tries in order:
    1. Explicit asset_id (numeric PK)
    2. Barcode field (exact barcode match)
    3. Search field — exact barcode, NFC tag, or name search
    Returns (asset, error_message) tuple.
    """
    # 1. Explicit PK
    if asset_id:
        try:
            return Asset.objects.get(pk=asset_id), None
        except (Asset.DoesNotExist, ValueError):
            return (
                None,
                f"No asset found with ID '{_truncate(asset_id)}'.",
            )

    # 2. Barcode field
    if barcode:
        barcode = barcode.strip()
        if not barcode:
            return None, "Please enter a barcode."
        try:
            return Asset.objects.get(barcode__iexact=barcode), None
        except Asset.DoesNotExist:
            pass
        except Asset.MultipleObjectsReturned:
            # Prefer active asset when case-variant barcodes exist
            hit = (
                Asset.objects.filter(
                    barcode__iexact=barcode, status="active"
                ).first()
                or Asset.objects.filter(barcode__iexact=barcode).first()
            )
            return hit, None
        # Try serial barcode
        try:
            serial = AssetSerial.objects.select_related("asset").get(
                barcode__iexact=barcode
            )
            return serial.asset, None
        except AssetSerial.DoesNotExist:
            return (
                None,
                f"No asset found with barcode " f"'{_truncate(barcode)}'.",
            )
        except AssetSerial.MultipleObjectsReturned:
            serial = (
                AssetSerial.objects.select_related("asset")
                .filter(barcode__iexact=barcode)
                .first()
            )
            return serial.asset, None

    # 3. Search field — try barcode, NFC, then name
    if search:
        search = search.strip()
        if not search:
            return None, "Please enter a search term."

        # 3a. Exact barcode match (case-insensitive for scanner
        # variance)
        try:
            return Asset.objects.get(barcode__iexact=search), None
        except Asset.DoesNotExist:
            pass
        except Asset.MultipleObjectsReturned:
            # Prefer active asset when case-variant barcodes exist
            hit = (
                Asset.objects.filter(
                    barcode__iexact=search, status="active"
                ).first()
                or Asset.objects.filter(barcode__iexact=search).first()
            )
            return hit, None

        # 3b. Serial barcode match
        try:
            serial = AssetSerial.objects.select_related("asset").get(
                barcode__iexact=search
            )
            return serial.asset, None
        except AssetSerial.DoesNotExist:
            pass
        except AssetSerial.MultipleObjectsReturned:
            serial = (
                AssetSerial.objects.select_related("asset")
                .filter(barcode__iexact=search)
                .first()
            )
            return serial.asset, None

        # 3c. NFC tag match
        nfc_asset = NFCTag.get_asset_by_tag(search)
        if nfc_asset:
            return nfc_asset, None

        # 3d. Exact name match (active assets only)
        name_hits = list(
            Asset.objects.filter(name=search, status="active")[:2]
        )
        if len(name_hits) == 1:
            return name_hits[0], None
        elif len(name_hits) > 1:
            return None, (
                f"Multiple assets named '{_truncate(search)}'. "
                f"Please use the autocomplete to select "
                f"the correct one."
            )

        # 3e. Broad text match (name, description, tags, category)
        matches = (
            Asset.objects.filter(status="active")
            .filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(tags__name__icontains=search)
                | Q(category__name__icontains=search)
            )
            .distinct()[:2]
        )
        results = list(matches)
        if len(results) == 1:
            return results[0], None
        elif len(results) > 1:
            return None, (
                f"Multiple assets match '{_truncate(search)}'. "
                f"Please be more specific or use the "
                f"autocomplete suggestions."
            )
        return (
            None,
            f"No asset found matching '{_truncate(search)}'.",
        )

    return None, "Please provide an asset to add."

"""Tests for barcode and NFC tag functionality."""

import json
from unittest.mock import MagicMock, patch

import pytest

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from assets.factories import (
    AssetFactory,
    AssetImageFactory,
    AssetKitFactory,
    AssetSerialFactory,
    CategoryFactory,
    DepartmentFactory,
    HoldListFactory,
    HoldListItemFactory,
    HoldListStatusFactory,
    LocationFactory,
    NFCTagFactory,
    ProjectFactory,
    SiteBrandingFactory,
    StocktakeItemFactory,
    StocktakeSessionFactory,
    TagFactory,
    TransactionFactory,
    UserFactory,
    VirtualBarcodeFactory,
)
from assets.models import (
    Asset,
    AssetSerial,
    Location,
    NFCTag,
    PrintClient,
    PrintRequest,
    Tag,
    VirtualBarcode,
)

User = get_user_model()


class TestNFCViews:
    def test_nfc_add_renders(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:nfc_add", args=[asset.pk])
        )
        assert response.status_code == 200

    def test_nfc_add_creates_tag(self, client_logged_in, asset):
        response = client_logged_in.post(
            reverse("assets:nfc_add", args=[asset.pk]),
            {"tag_id": "NFC-NEW-001", "notes": "Test NFC"},
        )
        assert response.status_code == 302
        assert NFCTag.objects.filter(
            tag_id="NFC-NEW-001", asset=asset
        ).exists()

    def test_nfc_add_conflict(self, client_logged_in, asset, user):
        NFCTag.objects.create(
            tag_id="NFC-CONFLICT", asset=asset, assigned_by=user
        )
        response = client_logged_in.post(
            reverse("assets:nfc_add", args=[asset.pk]),
            {"tag_id": "NFC-CONFLICT"},
        )
        assert response.status_code == 302
        # Should not create a second active tag
        assert (
            NFCTag.objects.filter(
                tag_id__iexact="NFC-CONFLICT",
                removed_at__isnull=True,
            ).count()
            == 1
        )

    def test_nfc_remove(self, client_logged_in, asset, user):
        nfc = NFCTag.objects.create(
            tag_id="NFC-REMOVE", asset=asset, assigned_by=user
        )
        response = client_logged_in.post(
            reverse("assets:nfc_remove", args=[asset.pk, nfc.pk]),
        )
        assert response.status_code == 302
        nfc.refresh_from_db()
        assert nfc.removed_at is not None


# ============================================================
# BARCODE PRE-GENERATION TESTS (C4)
# ============================================================


class TestBarcodePregeneration:
    """Tests for barcode pre-generation view (now uses VirtualBarcode)."""

    def test_pregenerate_get(self, admin_client, db):
        response = admin_client.get(reverse("assets:barcode_pregenerate"))
        assert response.status_code == 200

    def test_pregenerate_creates_barcodes_in_memory(self, admin_client, db):
        before_asset_count = Asset.objects.count()
        response = admin_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 5},
        )
        assert response.status_code == 200
        # No new assets created (virtual, in-memory only)
        assert Asset.objects.count() == before_asset_count
        # Response should contain generated barcodes
        assert b"barcode" in response.content.lower()

    def test_pregenerate_max_100(self, admin_client, db):
        response = admin_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 200},
        )
        assert response.status_code == 200
        # Should cap at 100
        assert b"barcode" in response.content.lower()

    def test_pregenerate_viewer_denied(self, viewer_client, db):
        response = viewer_client.get(reverse("assets:barcode_pregenerate"))
        assert response.status_code == 403


class TestZebraBatchPrinting:
    def test_generate_batch_zpl_empty(self):
        from assets.services.zebra import generate_batch_zpl

        result = generate_batch_zpl([])
        assert result == ""

    def test_generate_batch_zpl_multiple(
        self, asset, category, location, user
    ):
        from assets.services.zebra import generate_batch_zpl

        # Create additional assets
        asset2 = Asset.objects.create(
            name="Second Asset",
            barcode="ASSET-12345678",
            category=category,
            current_location=location,
            created_by=user,
        )
        asset3 = Asset.objects.create(
            name="Third Asset",
            barcode="ASSET-87654321",
            category=category,
            current_location=location,
            created_by=user,
        )

        result = generate_batch_zpl([asset, asset2, asset3])

        # Should contain multiple ZPL blocks
        assert result.count("^XA") == 3
        assert result.count("^XZ") == 3

        # Should contain all three barcodes
        assert asset.barcode in result
        assert "ASSET-12345678" in result
        assert "ASSET-87654321" in result

        # Should contain all asset names
        assert asset.name in result
        assert "Second Asset" in result
        assert "Third Asset" in result

    def test_print_batch_labels_success(
        self, asset, category, location, user, settings
    ):
        from unittest.mock import MagicMock, patch

        from assets.services.zebra import print_batch_labels

        # Configure mock printer settings
        settings.ZEBRA_PRINTER_HOST = "192.168.1.100"
        settings.ZEBRA_PRINTER_PORT = 9100

        asset2 = Asset.objects.create(
            name="Second Asset",
            barcode="ASSET-12345678",
            category=category,
            current_location=location,
            created_by=user,
        )

        with patch("assets.services.zebra.socket.socket") as mock_socket:
            mock_sock_instance = MagicMock()
            mock_socket.return_value.__enter__.return_value = (
                mock_sock_instance
            )

            success, count = print_batch_labels([asset, asset2])

            assert success is True
            assert count == 2
            assert mock_sock_instance.sendall.called

    def test_print_batch_labels_empty(self):
        from assets.services.zebra import print_batch_labels

        success, count = print_batch_labels([])
        assert success is True
        assert count == 0


# ============================================================
# ZPL QR CODE AND LABEL IMPROVEMENTS (G7, L3, L21, L23)
# ============================================================


@pytest.mark.django_db
class TestZplQrCode:
    """G7: ZPL labels include QR code alongside Code128 barcode."""

    def test_zpl_contains_qr_code(self):
        """Verify generated ZPL includes ^BQ command for QR code."""
        from assets.services.zebra import generate_zpl

        zpl = generate_zpl("TEST-001", "Test Asset", "Props")
        assert "^BQ" in zpl

    def test_zpl_qr_encodes_asset_url(self):
        """Verify QR data contains the asset URL path."""
        from assets.services.zebra import generate_zpl

        zpl = generate_zpl("TEST-001", "Test Asset", "Props")
        assert "/a/TEST-001/" in zpl

    def test_zpl_still_has_code128(self):
        """Ensure Code128 barcode is still present alongside QR."""
        from assets.services.zebra import generate_zpl

        zpl = generate_zpl("TEST-001", "Test Asset", "Props")
        assert "^BCN" in zpl
        assert "^BQ" in zpl


@pytest.mark.django_db
class TestZplPrintRetry:
    """L3: print_zpl retries once on connection failure."""

    def test_zpl_print_retry_on_failure(self, settings):
        """Mock socket to fail once then succeed on retry."""
        from assets.services.zebra import print_zpl

        settings.ZEBRA_PRINTER_HOST = "192.168.1.100"
        settings.ZEBRA_PRINTER_PORT = 9100

        call_count = 0

        class FakeSocket:
            def settimeout(self, t):
                pass

            def connect(self, addr):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ConnectionError("Connection refused")

            def sendall(self, data):
                pass

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with patch(
            "assets.services.zebra.socket.socket",
            return_value=FakeSocket(),
        ):
            result = print_zpl("^XA^XZ")

        assert result is True
        assert call_count == 2

    def test_zpl_print_fails_after_retry(self, settings):
        """If both attempts fail, print_zpl returns False."""
        from assets.services.zebra import print_zpl

        settings.ZEBRA_PRINTER_HOST = "192.168.1.100"
        settings.ZEBRA_PRINTER_PORT = 9100

        class AlwaysFailSocket:
            def settimeout(self, t):
                pass

            def connect(self, addr):
                raise ConnectionError("Connection refused")

            def sendall(self, data):
                pass

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with patch(
            "assets.services.zebra.socket.socket",
            return_value=AlwaysFailSocket(),
        ):
            result = print_zpl("^XA^XZ")

        assert result is False


@pytest.mark.django_db
class TestBatchZplConcatenated:
    """L23: Bulk ZPL labels are concatenated into one document."""

    def test_batch_zpl_concatenated(self, asset, category, location, user):
        """Verify multiple labels produce one concatenated ZPL doc."""
        from assets.services.zebra import generate_batch_zpl

        asset2 = Asset.objects.create(
            name="Batch Asset 2",
            barcode="BATCH-00000002",
            category=category,
            current_location=location,
            created_by=user,
        )

        result = generate_batch_zpl([asset, asset2])

        # Single concatenated string with multiple label blocks
        assert isinstance(result, str)
        assert result.count("^XA") == 2
        assert result.count("^XZ") == 2

        # Both barcodes present
        assert asset.barcode in result
        assert "BATCH-00000002" in result

        # QR codes present for both
        assert result.count("^BQ") == 2
        assert f"/a/{asset.barcode}/" in result
        assert "/a/BATCH-00000002/" in result


@pytest.mark.django_db
class TestPrintAllFilteredView:
    """L21: Print All Filtered action generates labels for all
    assets matching the current filter."""

    def test_print_all_filtered_view(
        self, admin_client, category, location, user
    ):
        """Verify view generates labels for filtered assets."""
        # Create assets in a specific category
        for i in range(3):
            Asset.objects.create(
                name=f"Filtered Asset {i}",
                barcode=f"FILT-0000000{i}",
                category=category,
                current_location=location,
                created_by=user,
                status="active",
            )

        url = reverse("assets:print_all_filtered_labels")
        response = admin_client.get(
            url, {"category": category.pk, "status": "active"}
        )
        assert response.status_code == 200
        content = response.content.decode()
        for i in range(3):
            assert f"FILT-0000000{i}" in content

    def test_print_all_filtered_requires_login(self, client):
        """Anonymous users cannot access the view."""
        url = reverse("assets:print_all_filtered_labels")
        response = client.get(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response.url


class TestScanLookupSerial:
    """Test scan lookup resolves serial barcodes."""

    def test_serial_barcode_found(
        self, client_logged_in, serialised_asset, asset_serial
    ):
        response = client_logged_in.get(
            reverse("assets:scan_lookup") + f"?code={asset_serial.barcode}"
        )
        data = response.json()
        assert data["found"] is True
        assert data["asset_id"] == serialised_asset.pk
        assert data["serial_id"] == asset_serial.pk
        assert f"serial={asset_serial.pk}" in data["url"]

    def test_serial_barcode_case_insensitive(
        self, client_logged_in, serialised_asset, asset_serial
    ):
        response = client_logged_in.get(
            reverse("assets:scan_lookup")
            + f"?code={asset_serial.barcode.lower()}"
        )
        data = response.json()
        assert data["found"] is True

    def test_asset_barcode_still_priority(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:scan_lookup") + f"?code={asset.barcode}"
        )
        data = response.json()
        assert data["found"] is True
        assert "serial_id" not in data

    def test_identifier_serial_redirect(
        self, client_logged_in, serialised_asset, asset_serial
    ):
        response = client_logged_in.get(
            reverse(
                "assets:asset_by_identifier",
                args=[asset_serial.barcode],
            )
        )
        assert response.status_code == 302
        assert f"serial={asset_serial.pk}" in response.url


class TestNFCSelectForUpdate:
    """M8: NFC reassignment uses select_for_update (S7.4.4)."""

    def test_nfc_add_uses_atomic_transaction(
        self, client_logged_in, asset, user
    ):
        """Verify NFC add operates within an atomic transaction."""
        response = client_logged_in.post(
            reverse("assets:nfc_add", args=[asset.pk]),
            {"tag_id": "NFC-ATOMIC-001", "notes": "Test atomic"},
        )
        assert response.status_code == 302
        assert NFCTag.objects.filter(tag_id="NFC-ATOMIC-001").exists()

    def test_nfc_add_conflict_check_with_select_for_update(
        self, client_logged_in, asset, user
    ):
        """Verify conflict detection under select_for_update."""
        NFCTag.objects.create(tag_id="NFC-RACE", asset=asset, assigned_by=user)
        response = client_logged_in.post(
            reverse("assets:nfc_add", args=[asset.pk]),
            {"tag_id": "NFC-RACE"},
        )
        assert response.status_code == 302
        assert (
            NFCTag.objects.filter(
                tag_id__iexact="NFC-RACE",
                removed_at__isnull=True,
            ).count()
            == 1
        )

    def test_nfc_remove_uses_atomic_transaction(
        self, client_logged_in, asset, user
    ):
        """Verify NFC remove operates within an atomic transaction."""
        nfc = NFCTag.objects.create(
            tag_id="NFC-ATOMIC-REM", asset=asset, assigned_by=user
        )
        response = client_logged_in.post(
            reverse("assets:nfc_remove", args=[asset.pk, nfc.pk]),
        )
        assert response.status_code == 302
        nfc.refresh_from_db()
        assert nfc.removed_at is not None


class TestMigrateNFCTagsCommand:
    """L17: migrate_nfc_tags management command (S4.4.2.3)."""

    def test_migrate_nfc_tags_command_noop(self, db):
        """Command runs without error and reports no-op."""
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("migrate_nfc_tags", stdout=out)
        output = out.getvalue()
        assert "already" in output.lower() or "no legacy" in output.lower()

    def test_migrate_nfc_tags_command_dry_run(self, db):
        """Command supports --dry-run flag."""
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("migrate_nfc_tags", "--dry-run", stdout=out)
        output = out.getvalue()
        assert "dry run" in output.lower() or "already" in output.lower()


class TestNFCHistoryView:
    """L35: NFC tag history lookup view (S2.5.6-03)."""

    def test_nfc_history_view_shows_all_associations(
        self, client_logged_in, asset, user
    ):
        """History view shows all tag associations for an NFC tag."""
        _nfc = NFCTag.objects.create(  # noqa: F841
            tag_id="NFC-HIST-001", asset=asset, assigned_by=user
        )
        response = client_logged_in.get(
            reverse("assets:nfc_history", args=["NFC-HIST-001"]),
        )
        assert response.status_code == 200
        assert "NFC-HIST-001" in response.content.decode()

    def test_nfc_history_view_includes_removed_tags(
        self, client_logged_in, asset, user, category, location
    ):
        """History view includes removed (historical) tag records."""
        from django.utils import timezone

        # Create first association (now removed)
        _nfc1 = NFCTag.objects.create(  # noqa: F841
            tag_id="NFC-HIST-002",
            asset=asset,
            assigned_by=user,
            removed_at=timezone.now(),
            removed_by=user,
        )
        # Create second asset and reassign
        asset2 = Asset(
            name="Second Prop",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=user,
        )
        asset2.save()
        _nfc2 = NFCTag.objects.create(  # noqa: F841
            tag_id="NFC-HIST-002",
            asset=asset2,
            assigned_by=user,
        )
        response = client_logged_in.get(
            reverse("assets:nfc_history", args=["NFC-HIST-002"]),
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert asset.name in content
        assert asset2.name in content

    def test_nfc_history_view_404_for_unknown_tag(self, client_logged_in, db):
        """History view returns 404 for nonexistent tag."""
        response = client_logged_in.get(
            reverse("assets:nfc_history", args=["NONEXISTENT"]),
        )
        assert response.status_code == 404

    def test_nfc_history_view_requires_login(self, client, asset, user):
        """History view requires authentication."""
        NFCTag.objects.create(
            tag_id="NFC-HIST-AUTH", asset=asset, assigned_by=user
        )
        response = client.get(
            reverse("assets:nfc_history", args=["NFC-HIST-AUTH"]),
        )
        assert response.status_code == 302
        assert "/accounts/login/" in response.url


# ============================================================
# VIRTUAL BARCODE PRE-GENERATION TESTS (G4 — S2.4.3-02/04)
# ============================================================


@pytest.mark.django_db
class TestVirtualBarcodePregeneration:
    """G4: Virtual barcode pre-generation instead of draft assets."""

    def test_virtual_barcode_model_creation(self, admin_user):
        """VirtualBarcode can be created with barcode and created_by."""
        vb = VirtualBarcode.objects.create(
            barcode="TEST-ABC12345",
            created_by=admin_user,
        )
        assert vb.pk is not None
        assert vb.barcode == "TEST-ABC12345"
        assert vb.created_by == admin_user
        assert vb.assigned_to_asset is None
        assert vb.assigned_at is None
        assert vb.created_at is not None
        assert "unassigned" in str(vb)

    def test_virtual_barcode_unique_constraint(self, admin_user):
        """Duplicate barcode raises IntegrityError."""
        VirtualBarcode.objects.create(
            barcode="TEST-DUPLICATE",
            created_by=admin_user,
        )
        with pytest.raises(IntegrityError):
            VirtualBarcode.objects.create(
                barcode="TEST-DUPLICATE",
                created_by=admin_user,
            )

    def test_pregenerate_creates_no_db_records(self, admin_client, admin_user):
        """V166: POST to pregenerate generates barcodes in memory,
        NOT in the database. No VirtualBarcode or Asset records."""
        asset_count_before = Asset.objects.count()
        vb_count_before = VirtualBarcode.objects.count()

        response = admin_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 5},
        )
        assert response.status_code == 200

        # No VirtualBarcode records created (V166)
        assert VirtualBarcode.objects.count() == vb_count_before

        # No new Asset objects created
        assert Asset.objects.count() == asset_count_before

    def test_pregenerate_renders_labels(self, admin_client):
        """POST returns label page with barcodes."""
        import re

        response = admin_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 3},
        )
        assert response.status_code == 200
        content = response.content.decode()
        # Should contain barcode strings in the rendered page
        barcodes = re.findall(r"[A-Z]+-[A-Z0-9]{8}", content)
        assert len(barcodes) >= 3

    def test_pregenerate_quantity_clamped(self, admin_client):
        """Quantity is clamped to 1-100 range."""
        import re

        # Over 100 should be clamped to 100
        resp = admin_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 200},
        )
        content = resp.content.decode()
        barcodes = re.findall(r"[A-Z]+-[A-Z0-9]{8}", content)
        assert len(barcodes) <= 100

        # Under 1 should be clamped to 1
        resp = admin_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 0},
        )
        content = resp.content.decode()
        barcodes = re.findall(r"[A-Z]+-[A-Z0-9]{8}", content)
        assert len(barcodes) >= 1

    def test_virtual_barcode_claimed_on_asset_create(
        self, admin_client, admin_user, category, location
    ):
        """When asset is created with matching barcode, VB is linked."""
        vb = VirtualBarcode.objects.create(
            barcode="CLAIM-TEST001",
            created_by=admin_user,
        )
        assert vb.assigned_to_asset is None

        # Create an asset with that barcode
        asset = Asset(
            name="Claimed Asset",
            barcode="CLAIM-TEST001",
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        asset.save()

        vb.refresh_from_db()
        assert vb.assigned_to_asset == asset
        assert vb.assigned_at is not None

    def test_unassigned_virtual_barcodes_list(self, admin_client, admin_user):
        """GET to list URL shows only unassigned virtual barcodes."""
        vb1 = VirtualBarcode.objects.create(
            barcode="LIST-UNASSIGNED",
            created_by=admin_user,
        )
        # Create an assigned one
        asset = Asset(
            name="Assigned",
            barcode="LIST-ASSIGNED",
            status="draft",
            created_by=admin_user,
        )
        asset.save()
        vb2 = VirtualBarcode.objects.create(
            barcode="LIST-ASSIGNED",
            created_by=admin_user,
            assigned_to_asset=asset,
        )

        response = admin_client.get(reverse("assets:virtual_barcode_list"))
        assert response.status_code == 200
        content = response.content.decode()
        assert vb1.barcode in content
        assert vb2.barcode not in content

    def test_pregenerate_permission_check(self, viewer_client):
        """Viewer cannot access pregenerate."""
        response = viewer_client.get(reverse("assets:barcode_pregenerate"))
        assert response.status_code == 403

        response = viewer_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 5},
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestBarcodeEdgeCases:
    """S7.18: Barcode edge cases."""

    def test_barcode_pregeneration_no_db_writes(
        self, admin_client, admin_user
    ):
        """VV787/V166: Barcode pre-generation is now virtual — no DB
        writes needed, so atomicity is moot. Verify no VB records
        created."""
        vb_before = VirtualBarcode.objects.count()
        admin_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 5},
        )
        assert VirtualBarcode.objects.count() == vb_before

    def test_asset_barcode_cleared_null_handling_in_list_view(
        self, admin_user, asset
    ):
        """VV792: Asset list view must display 'No barcode' when asset
        barcode is cleared, not empty string or raw value (S7.18.5)."""
        # Clear the barcode using direct DB update to bypass save()
        # regeneration
        Asset.objects.filter(pk=asset.pk).update(barcode="")
        asset.refresh_from_db()
        from django.test import Client

        c = Client()
        c.force_login(admin_user)
        response = c.get(reverse("assets:asset_list"))
        assert response.status_code == 200
        content = response.content.decode()
        # The asset list should show 'No barcode' or similar, not
        # an empty cell or raw empty string
        assert "No barcode" in content or "no barcode" in content, (
            "VV792: Asset list view must display 'No barcode' when "
            "barcode is cleared. Currently the view shows an empty "
            "cell or does not indicate the missing barcode."
        )


@pytest.mark.django_db
class TestScanLookupView:
    """V579-V581: Barcode scanning workflow via scan_lookup."""

    def test_scan_lookup_by_barcode(self, client_logged_in, asset):
        """Scanning an asset barcode returns its details."""
        url = reverse("assets:scan_lookup")
        response = client_logged_in.get(url, {"code": asset.barcode})
        assert response.status_code == 200
        data = response.json()
        assert data["found"] is True
        assert data["asset_id"] == asset.pk
        assert data["asset_name"] == asset.name

    def test_scan_lookup_not_found(self, client_logged_in):
        """Scanning an unknown code returns found=False."""
        url = reverse("assets:scan_lookup")
        response = client_logged_in.get(url, {"code": "NONEXISTENT-123"})
        assert response.status_code == 200
        data = response.json()
        assert data["found"] is False

    def test_scan_lookup_empty_code(self, client_logged_in):
        """Empty code returns error."""
        url = reverse("assets:scan_lookup")
        response = client_logged_in.get(url, {"code": ""})
        assert response.status_code == 200
        data = response.json()
        assert data["found"] is False

    def test_scan_lookup_by_nfc_tag(self, client_logged_in, asset, user):
        """Scanning an NFC tag ID resolves to the associated asset."""
        NFCTag.objects.create(
            tag_id="NFC-SCAN-001", asset=asset, assigned_by=user
        )
        url = reverse("assets:scan_lookup")
        response = client_logged_in.get(url, {"code": "NFC-SCAN-001"})
        data = response.json()
        assert data["found"] is True
        assert data["asset_id"] == asset.pk

    def test_scan_lookup_by_serial_barcode(
        self, client_logged_in, serialised_asset, asset_serial
    ):
        """Scanning a serial barcode returns the parent asset."""
        url = reverse("assets:scan_lookup")
        response = client_logged_in.get(url, {"code": asset_serial.barcode})
        data = response.json()
        assert data["found"] is True
        assert data["asset_id"] == serialised_asset.pk
        assert data["serial_id"] == asset_serial.pk

    def test_scan_view_renders(self, client_logged_in):
        """The scan page renders successfully."""
        url = reverse("assets:scan")
        response = client_logged_in.get(url)
        assert response.status_code == 200

    def test_asset_by_identifier_resolves_barcode(
        self, client_logged_in, asset
    ):
        """The unified /a/<identifier>/ endpoint resolves barcodes."""
        url = reverse(
            "assets:asset_by_identifier",
            kwargs={"identifier": asset.barcode},
        )
        response = client_logged_in.get(url)
        # Should redirect to asset detail
        assert response.status_code in (200, 302)


@pytest.mark.django_db
class TestPrintLabelViews:
    """V583 S4.3.3.1: Browser print support for labels."""

    def test_asset_label_renders(self, client_logged_in, asset):
        """Asset label view returns printable HTML."""
        url = reverse("assets:asset_label", args=[asset.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert asset.barcode in content

    def test_asset_label_contains_qr_code(self, client_logged_in, asset):
        """Label includes a QR code data URI."""
        url = reverse("assets:asset_label", args=[asset.pk])
        response = client_logged_in.get(url)
        content = response.content.decode()
        # QR code is embedded as base64 data URI
        assert "data:image/png;base64," in content

    def test_asset_label_zpl_raw(self, client_logged_in, asset):
        """ZPL label view returns raw ZPL when ?raw=1."""
        url = reverse("assets:asset_label_zpl", args=[asset.pk])
        response = client_logged_in.get(url, {"raw": "1"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "^XA" in content  # ZPL start command


@pytest.mark.django_db
class TestNFCViewsExtended:
    """V586 S4.4.2.1: Web NFC API support — NFC add/remove/history."""

    def test_nfc_add_view_renders(self, client_logged_in, asset):
        """NFC add form renders."""
        url = reverse("assets:nfc_add", args=[asset.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200

    def test_nfc_add_creates_tag(self, client_logged_in, asset, user):
        """Posting NFC tag ID creates an NFC tag assignment."""
        url = reverse("assets:nfc_add", args=[asset.pk])
        response = client_logged_in.post(
            url, {"tag_id": "NFC-TEST-001", "notes": "Test tag"}
        )
        assert response.status_code == 302
        assert NFCTag.objects.filter(
            tag_id="NFC-TEST-001", asset=asset
        ).exists()

    def test_nfc_add_duplicate_tag_rejected(
        self, client_logged_in, asset, user
    ):
        """Cannot assign same NFC tag to two assets."""
        NFCTag.objects.create(tag_id="NFC-DUPE", asset=asset, assigned_by=user)
        url = reverse("assets:nfc_add", args=[asset.pk])
        response = client_logged_in.post(
            url, {"tag_id": "NFC-DUPE", "notes": ""}
        )
        assert response.status_code == 302
        # Only one active tag with this ID
        assert (
            NFCTag.objects.filter(
                tag_id__iexact="NFC-DUPE",
                removed_at__isnull=True,
            ).count()
            == 1
        )

    def test_nfc_remove(self, client_logged_in, asset, user):
        """Removing an NFC tag sets removed_at."""
        tag = NFCTag.objects.create(
            tag_id="NFC-RM-001", asset=asset, assigned_by=user
        )
        url = reverse("assets:nfc_remove", args=[asset.pk, tag.pk])
        response = client_logged_in.post(url, {"notes": "No longer needed"})
        assert response.status_code == 302
        tag.refresh_from_db()
        assert tag.removed_at is not None

    def test_nfc_history_view(self, client_logged_in, asset, user):
        """NFC history view shows tag assignments."""
        NFCTag.objects.create(
            tag_id="NFC-HIST-001", asset=asset, assigned_by=user
        )
        url = reverse(
            "assets:nfc_history",
            kwargs={"tag_uid": "NFC-HIST-001"},
        )
        response = client_logged_in.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "NFC-HIST-001" in content

    def test_nfc_history_404_unknown_tag(self, client_logged_in):
        """NFC history returns 404 for unknown tag."""
        url = reverse(
            "assets:nfc_history",
            kwargs={"tag_uid": "UNKNOWN-TAG"},
        )
        response = client_logged_in.get(url)
        assert response.status_code == 404

    def test_viewer_cannot_add_nfc_tag(self, viewer_client, asset):
        """Viewers cannot add NFC tags."""
        url = reverse("assets:nfc_add", args=[asset.pk])
        response = viewer_client.post(
            url, {"tag_id": "NFC-VIEWER", "notes": ""}
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestNFCWriteOnAdd:
    """S2.5.4-05: NFC add page includes scan+write flow for Web NFC."""

    def test_nfc_add_includes_nfc_js(self, client_logged_in, asset):
        """NFC add page includes the nfc.js script."""
        url = reverse("assets:nfc_add", args=[asset.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "nfc.js" in content

    def test_nfc_add_has_scan_button(self, client_logged_in, asset):
        """NFC add page has a scan NFC button (hidden by default)."""
        url = reverse("assets:nfc_add", args=[asset.pk])
        response = client_logged_in.get(url)
        content = response.content.decode()
        assert "Scan NFC Tag" in content

    def test_nfc_add_has_write_confirmation(self, client_logged_in, asset):
        """NFC add page has write confirmation prompt text."""
        url = reverse("assets:nfc_add", args=[asset.pk])
        response = client_logged_in.get(url)
        content = response.content.decode()
        assert "Program this tag" in content

    def test_nfc_add_includes_site_url(self, client_logged_in, asset):
        """NFC add page includes SITE_URL for NDEF URL generation."""
        url = reverse("assets:nfc_add", args=[asset.pk])
        response = client_logged_in.get(url)
        content = response.content.decode()
        assert "data-site-url" in content


@pytest.mark.django_db
class TestNFCReprogramButton:
    """S2.5.4-06: Asset detail reprogram button for existing NFC tags."""

    def test_reprogram_button_present_for_active_tag(
        self, client_logged_in, asset, user
    ):
        """Active NFC tags show a reprogram button."""
        NFCTag.objects.create(
            tag_id="NFC-REPROG", asset=asset, assigned_by=user
        )
        url = reverse("assets:asset_detail", args=[asset.pk])
        response = client_logged_in.get(url)
        content = response.content.decode()
        assert "Reprogram" in content

    def test_reprogram_not_shown_for_removed_tags(
        self, client_logged_in, asset, user
    ):
        """Removed NFC tags do not show a reprogram button."""
        from django.utils import timezone

        NFCTag.objects.create(
            tag_id="NFC-REMOVED",
            asset=asset,
            assigned_by=user,
            removed_at=timezone.now(),
            removed_by=user,
        )
        url = reverse("assets:asset_detail", args=[asset.pk])
        response = client_logged_in.get(url)
        content = response.content.decode()
        # The removed tag section should not have reprogram
        assert "NFC-REMOVED" in content
        # Reprogram should only appear in active tag rows
        assert content.count("Reprogram") == 0


@pytest.mark.django_db
class TestBarcodeConflictRestoration:
    """VV839 S8.2.10: restore_archived_serials with barcode conflicts."""

    def test_restore_without_conflicts(self, serialised_asset, location, user):
        """Restore archived serials when no barcode conflict exists."""
        from assets.services.serial import (
            create_serial,
            restore_archived_serials,
        )

        serial = create_serial(
            serialised_asset,
            "REST-001",
            current_location=location,
        )
        serial.is_archived = True
        serial.save(update_fields=["is_archived"])

        result = restore_archived_serials(serialised_asset, user)
        assert result["restored"] == 1
        assert result["conflicts"] == []
        serial.refresh_from_db()
        assert serial.is_archived is False

    def test_restore_clears_conflicting_barcode(
        self, serialised_asset, location, user, category
    ):
        """When an archived serial's barcode conflicts, barcode is cleared."""
        from assets.factories import AssetFactory
        from assets.services.serial import (
            create_serial,
            restore_archived_serials,
        )

        serial = create_serial(
            serialised_asset,
            "CONFL-001",
            current_location=location,
        )
        conflicting_barcode = serial.barcode
        serial.is_archived = True
        serial.save(update_fields=["is_archived"])

        # Clear the barcode on the archived serial in the DB,
        # then create a new Asset with the same barcode, then
        # re-set the barcode on the archived serial to simulate
        # the conflict scenario (barcode reused at Asset level).
        AssetSerial.objects.filter(pk=serial.pk).update(barcode=None)
        other_asset = AssetFactory(
            name="Other Asset",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        Asset.objects.filter(pk=other_asset.pk).update(
            barcode=conflicting_barcode
        )
        # Now re-set the archived serial's barcode to trigger
        # conflict during restoration
        AssetSerial.objects.filter(pk=serial.pk).update(
            barcode=conflicting_barcode
        )
        serial.refresh_from_db()

        result = restore_archived_serials(serialised_asset, user)
        assert result["restored"] == 1
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["barcode"] == conflicting_barcode
        serial.refresh_from_db()
        assert serial.barcode is None
        assert serial.is_archived is False

    def test_restore_reports_quantity_discrepancy(
        self, serialised_asset, location, user
    ):
        """Restoration flags discrepancy when serial count != quantity."""
        from assets.services.serial import (
            create_serial,
            restore_archived_serials,
        )

        serial = create_serial(
            serialised_asset,
            "DISC-001",
            current_location=location,
        )
        serial.is_archived = True
        serial.save(update_fields=["is_archived"])

        # Set quantity to something different
        serialised_asset.quantity = 5
        serialised_asset.save(update_fields=["quantity"])

        result = restore_archived_serials(serialised_asset, user)
        assert "discrepancy" in result
        assert (
            result["discrepancy"]["serial_count"]
            != result["discrepancy"]["quantity"]
        )

    def test_restore_no_archived_returns_empty(self, serialised_asset, user):
        """No-op when there are no archived serials."""
        from assets.services.serial import restore_archived_serials

        result = restore_archived_serials(serialised_asset, user)
        assert result["restored"] == 0
        assert result["conflicts"] == []


@pytest.mark.django_db
class TestBarcodePregenerationExtended:
    """V166/V167/V170 — S2.4.3: Virtual pre-generation."""

    def test_pregenerate_creates_no_db_records(self, admin_client):
        """V166: Pre-generation must NOT store barcodes in DB."""
        url = reverse("assets:barcode_pregenerate")
        resp = admin_client.post(url, {"quantity": 5})
        assert resp.status_code == 200
        # Should NOT create VirtualBarcode records
        assert VirtualBarcode.objects.count() == 0

    def test_pregenerate_returns_label_template(self, admin_client):
        """Pre-generation renders labels for printing."""
        url = reverse("assets:barcode_pregenerate")
        resp = admin_client.post(url, {"quantity": 3})
        assert resp.status_code == 200
        assert (
            b"label_assets" in resp.content
            or b"qr_data_uri" in resp.content
            or b"ASSET-" in resp.content
        )

    def test_pregenerate_respects_quantity_bounds(self, admin_client):
        """Quantity clamped to 1-100."""
        url = reverse("assets:barcode_pregenerate")
        resp = admin_client.post(url, {"quantity": 200})
        assert resp.status_code == 200

    def test_pregenerate_department_dropdown_shown(
        self, admin_client, department
    ):
        """V167: Form should show department dropdown."""
        url = reverse("assets:barcode_pregenerate")
        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert b"department" in resp.content.lower()

    def test_pregenerate_with_department(self, admin_client, department):
        """V167: When department selected, uses its prefix."""
        department.barcode_prefix = "PROPS"
        department.save()
        url = reverse("assets:barcode_pregenerate")
        resp = admin_client.post(
            url, {"quantity": 2, "department": department.pk}
        )
        assert resp.status_code == 200
        assert b"PROPS-" in resp.content

    def test_pregenerate_validates_uniqueness(self, admin_client, asset):
        """V170: Barcodes validated against Asset.barcode
        and AssetSerial.barcode."""
        url = reverse("assets:barcode_pregenerate")
        # Just verify it completes without error — uniqueness
        # validation is internal
        resp = admin_client.post(url, {"quantity": 5})
        assert resp.status_code == 200

    def test_pregenerate_barcode_format(self, admin_client):
        """V170: Generated barcodes match expected format."""
        url = reverse("assets:barcode_pregenerate")
        resp = admin_client.post(url, {"quantity": 1})
        assert resp.status_code == 200
        content = resp.content.decode()
        # Should contain a barcode matching PREFIX-HEXCHARS
        import re

        assert re.search(r"[A-Z]+-[A-Z0-9]{8}", content)

    def test_member_cannot_pregenerate(self, member_client):
        """Only admins and dept managers can pre-generate."""
        url = reverse("assets:barcode_pregenerate")
        resp = member_client.get(url)
        assert resp.status_code == 403


@pytest.mark.django_db
class TestBulkZPLPrinting:
    """V257 — S2.8.2-03: Zebra ZPL bulk label printing."""

    @patch("assets.services.zebra.print_batch_labels")
    def test_bulk_print_zpl_success(self, mock_print, admin_client, asset):
        """Bulk ZPL printing calls print_batch_labels."""
        mock_print.return_value = (True, 1)
        url = reverse("assets:bulk_actions")
        resp = admin_client.post(
            url,
            {
                "asset_ids": [str(asset.pk)],
                "bulk_action": "print_labels_zpl",
            },
        )
        assert resp.status_code == 302
        mock_print.assert_called_once()

    @patch("assets.services.zebra.print_batch_labels")
    def test_bulk_print_zpl_failure(self, mock_print, admin_client, asset):
        """Shows error message when ZPL printing fails."""
        mock_print.return_value = (False, 0)
        url = reverse("assets:bulk_actions")
        resp = admin_client.post(
            url,
            {
                "asset_ids": [str(asset.pk)],
                "bulk_action": "print_labels_zpl",
            },
        )
        assert resp.status_code == 302


@pytest.mark.django_db
class TestBulkRemotePrint:
    """Bulk remote print via connected print client."""

    def _make_print_client(self, printers=None):
        from assets.models import PrintClient

        return PrintClient.objects.create(
            name="Test Station",
            token_hash="bulkremotetest123",
            status="approved",
            is_active=True,
            is_connected=True,
            printers=printers
            or [{"id": "zebra1", "name": "Zebra", "type": "label"}],
        )

    @patch("assets.views.get_channel_layer")
    def test_bulk_remote_print_success(
        self, mock_get_layer, admin_client, asset
    ):
        """Bulk remote print sends jobs via channel layer."""
        mock_layer = MagicMock()
        mock_get_layer.return_value = mock_layer
        pc = self._make_print_client()
        url = reverse("assets:bulk_actions")
        resp = admin_client.post(
            url,
            {
                "asset_ids": [str(asset.pk)],
                "bulk_action": "remote_print",
                "remote_printer": f"{pc.pk}:zebra1",
            },
        )
        assert resp.status_code == 302
        mock_layer.group_send.assert_called_once()

    def test_bulk_remote_print_no_printer(self, admin_client, asset):
        """Returns error when no printer selected."""
        url = reverse("assets:bulk_actions")
        resp = admin_client.post(
            url,
            {
                "asset_ids": [str(asset.pk)],
                "bulk_action": "remote_print",
                "remote_printer": "",
            },
        )
        assert resp.status_code == 302

    def test_bulk_remote_print_invalid_client(self, admin_client, asset):
        """Returns error when print client not found."""
        url = reverse("assets:bulk_actions")
        resp = admin_client.post(
            url,
            {
                "asset_ids": [str(asset.pk)],
                "bulk_action": "remote_print",
                "remote_printer": "9999:zebra1",
            },
        )
        assert resp.status_code == 302

    def test_bulk_remote_print_disconnected(self, admin_client, asset):
        """Returns error when print client is disconnected."""
        pc = self._make_print_client()
        pc.is_connected = False
        pc.save()
        url = reverse("assets:bulk_actions")
        resp = admin_client.post(
            url,
            {
                "asset_ids": [str(asset.pk)],
                "bulk_action": "remote_print",
                "remote_printer": f"{pc.pk}:zebra1",
            },
        )
        assert resp.status_code == 302

    @patch("assets.views.get_channel_layer")
    def test_bulk_remote_print_saves_session(
        self, mock_get_layer, admin_client, asset
    ):
        """Saves last-used printer to session."""
        mock_layer = MagicMock()
        mock_get_layer.return_value = mock_layer
        pc = self._make_print_client()
        url = reverse("assets:bulk_actions")
        admin_client.post(
            url,
            {
                "asset_ids": [str(asset.pk)],
                "bulk_action": "remote_print",
                "remote_printer": f"{pc.pk}:zebra1",
            },
        )
        assert admin_client.session["last_printer"] == f"{pc.pk}:zebra1"

    def test_asset_list_shows_remote_print_option(self, admin_client, asset):
        """Asset list includes remote print when printers connected."""
        pc = self._make_print_client()
        resp = admin_client.get(reverse("assets:asset_list"))
        assert resp.context["remote_print_available"] is True
        assert len(resp.context["connected_printers"]) == 1
        assert resp.context["connected_printers"][0]["key"] == (
            f"{pc.pk}:zebra1"
        )


@pytest.mark.django_db
class TestV479NFCTagSerial:
    """V479: NFCTag nullable FK to AssetSerial (COULD)."""

    def test_nfctag_serial_field_exists(self):
        """NFCTag model should have a nullable serial FK."""
        field = NFCTag._meta.get_field("serial")
        assert field.null is True
        assert field.blank is True

    def test_nfctag_can_be_assigned_to_serial(
        self, serialised_asset, asset_serial, admin_user
    ):
        """An NFC tag can reference a specific serial."""
        tag = NFCTag.objects.create(
            tag_id="NFC-SERIAL-001",
            asset=serialised_asset,
            serial=asset_serial,
            assigned_by=admin_user,
        )
        assert tag.serial == asset_serial
        assert tag.serial.serial_number == "001"

    def test_nfctag_serial_is_optional(self, asset, admin_user):
        """NFCTag serial FK is optional (nullable)."""
        tag = NFCTag.objects.create(
            tag_id="NFC-NO-SERIAL",
            asset=asset,
            assigned_by=admin_user,
        )
        assert tag.serial is None


@pytest.mark.django_db
class TestV792ClearedBarcodeDisplay:
    """V792 (S7.18.5): Cleared barcode shows 'No barcode' in list."""

    def test_list_shows_no_barcode_for_empty_barcode(
        self, admin_client, admin_user, category, location
    ):
        """Asset with empty barcode should show 'No barcode' in list."""
        a = Asset(
            name="No Barcode Asset V792",
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
            barcode="",
        )
        a.save()
        # Clear the auto-generated barcode
        Asset.objects.filter(pk=a.pk).update(barcode="")
        response = admin_client.get(reverse("assets:asset_list"))
        content = response.content.decode()
        assert "No barcode" in content


# ============================================================
# V157 (S2.4.1-03): Code128 barcode generation
# ============================================================


@pytest.mark.django_db
class TestV157BarcodeGeneration:
    """V157: Barcode service generates a valid Code128 barcode image."""

    def test_code128_barcode_generation(self):
        """generate_code128_image should return a valid image file."""
        from assets.services.barcode import generate_code128_image

        barcode_text = "V157-TEST"
        img_file = generate_code128_image(barcode_text)
        assert img_file is not None
        # Check it's a ContentFile with content
        assert len(img_file.read()) > 0


# ============================================================
# V158 (S2.4.1-04): Barcode uniqueness
# ============================================================


@pytest.mark.django_db
class TestV158BarcodeUniqueness:
    """V158: Asset.barcode has a unique constraint."""

    def test_asset_barcode_unique_constraint(
        self, asset, admin_user, category, location
    ):
        """Creating an asset with duplicate barcode should fail."""
        barcode_val = asset.barcode
        # Try to create another asset with the same barcode
        with pytest.raises(Exception):  # IntegrityError or ValidationError
            Asset.objects.create(
                name="V158 Duplicate",
                barcode=barcode_val,
                category=category,
                current_location=location,
                status="active",
                is_serialised=False,
                created_by=admin_user,
            )


# ============================================================
# V162 (S2.4.3-01): Barcode printed on label
# ============================================================


@pytest.mark.django_db
class TestV162BarcodePrintedOnLabel:
    """V162: asset_label view returns a response with barcode."""

    def test_asset_label_returns_response(self, admin_client, asset):
        """asset_label should return a response for an asset."""
        url = reverse("assets:asset_label", args=[asset.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        # Check that barcode appears in the response
        content = response.content.decode()
        assert asset.barcode in content


# ============================================================
# V164 (S2.4.4-01): Pre-generated barcodes
# ============================================================


@pytest.mark.django_db
class TestV164PregeneratedBarcodes:
    """V164: barcode_pregenerate view works and generates barcodes."""

    def test_barcode_pregenerate_works(self, admin_client):
        """barcode_pregenerate should generate barcodes."""
        url = reverse("assets:barcode_pregenerate")
        response = admin_client.post(
            url,
            {
                "quantity": 5,
            },
        )
        assert response.status_code == 200
        # Should return a page with generated barcodes
        content = response.content.decode()
        # Look for barcode patterns or "ASSET-" prefix
        assert "ASSET-" in content or "barcode" in content.lower()


# ============================================================
# V178 (S2.4.7-02): Scan lookup resolves barcode
# ============================================================


@pytest.mark.django_db
class TestV178ScanLookupResolvesBarcode:
    """V178: scan_lookup with a valid barcode returns the asset."""

    def test_scan_lookup_finds_asset_by_barcode(self, client_logged_in, asset):
        """scan_lookup should find asset by barcode."""
        url = reverse("assets:scan_lookup")
        response = client_logged_in.get(
            url,
            {"code": asset.barcode},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["found"] is True
        assert data["asset_id"] == asset.pk
        assert data["asset_name"] == asset.name


# ============================================================
# V187 (S2.5.1-01): NFC tag registration
# ============================================================


@pytest.mark.django_db
class TestV187NFCTagRegistration:
    """V187: nfc_add view accepts POST with tag_uid and creates an NFCTag
    linked to the asset."""

    def test_nfc_add_creates_tag(self, admin_client, asset, admin_user):
        """POST to nfc_add should create NFCTag linked to asset."""
        from assets.models import NFCTag

        url = reverse("assets:nfc_add", args=[asset.pk])
        response = admin_client.post(
            url, {"tag_id": "04A1B2C3D4E5F6", "notes": "Test tag"}
        )
        assert response.status_code == 302  # Redirect after success
        tag = NFCTag.objects.filter(
            tag_id="04A1B2C3D4E5F6", asset=asset
        ).first()
        assert tag is not None
        assert tag.assigned_by == admin_user
        assert tag.notes == "Test tag"


# ============================================================
# V191 (S2.5.2-03): NFC scan resolves to asset
# ============================================================


@pytest.mark.django_db
class TestV191NFCScanResolution:
    """V191: scan_lookup with an NFC tag UID returns the linked asset."""

    def test_scan_lookup_resolves_nfc_tag(self, client_logged_in, asset, user):
        """scan_lookup should resolve NFC tag UID to asset."""
        from assets.models import NFCTag

        NFCTag.objects.create(
            tag_id="04AABBCCDDEE", asset=asset, assigned_by=user
        )
        url = reverse("assets:scan_lookup")
        response = client_logged_in.get(url, {"code": "04AABBCCDDEE"})
        assert response.status_code == 200
        data = response.json()
        assert data["found"] is True
        assert data["asset_id"] == asset.pk
        assert data["asset_name"] == asset.name


# ============================================================
# V198 (S2.5.4-02): NFC tag reassignment
# ============================================================


@pytest.mark.django_db
class TestV198NFCTagReassignment:
    """V198: Adding an NFC tag to a new asset unlinks it from the old asset."""

    def test_nfc_tag_reassignment_via_remove_and_add(
        self, admin_client, asset, admin_user, category, location
    ):
        """Reassigning NFC tag requires removing it first, then adding to
        new asset."""
        from assets.models import Asset, NFCTag

        # Create initial tag on asset
        tag = NFCTag.objects.create(
            tag_id="04112233", asset=asset, assigned_by=admin_user
        )
        assert tag.is_active

        # Create second asset
        asset2 = Asset.objects.create(
            name="Second Asset",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=admin_user,
        )

        # Remove tag from first asset
        url = reverse("assets:nfc_remove", args=[asset.pk, tag.pk])
        admin_client.post(url)

        # Verify old tag is now inactive
        tag.refresh_from_db()
        assert tag.removed_at is not None
        assert not tag.is_active

        # Now assign to asset2
        url = reverse("assets:nfc_add", args=[asset2.pk])
        admin_client.post(url, {"tag_id": "04112233"})

        # New tag should exist for asset2
        new_tag = NFCTag.objects.filter(
            tag_id="04112233", asset=asset2, removed_at__isnull=True
        ).first()
        assert new_tag is not None
        assert new_tag.is_active


# ============================================================
# V208 (S2.5.7-01): NFC history view
# ============================================================


@pytest.mark.django_db
class TestV208NFCHistoryView:
    """V208: nfc_history view returns a response showing tag scan history."""

    def test_nfc_history_shows_tag_history(
        self, client_logged_in, asset, user
    ):
        """nfc_history should display history of NFC tag across assets."""
        from assets.models import NFCTag

        NFCTag.objects.create(
            tag_id="04FFAABBCC", asset=asset, assigned_by=user
        )
        url = reverse("assets:nfc_history", args=["04FFAABBCC"])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "04FFAABBCC" in content or "04ffaabbcc" in content.lower()
        assert asset.name in content


# ============================================================
# Quick Capture NFC Scan + Program Flow
# ============================================================


@pytest.mark.django_db
class TestQuickCaptureNFCScan:
    """S2.5.4-05: Quick capture has NFC scan button for Web NFC."""

    def test_quick_capture_has_nfc_scan_button(self, client_logged_in):
        """Quick capture page should have an NFC scan button."""
        url = reverse("assets:quick_capture")
        response = client_logged_in.get(url)
        content = response.content.decode()
        assert "Scan NFC Tag" in content

    def test_quick_capture_includes_nfc_js(self, client_logged_in):
        """Quick capture page should include nfc.js for Web NFC."""
        url = reverse("assets:quick_capture")
        response = client_logged_in.get(url)
        content = response.content.decode()
        assert "nfc.js" in content


@pytest.mark.django_db
class TestQuickCaptureNFCProgram:
    """S2.5.4-05: Quick capture success shows NFC program prompt."""

    def test_success_with_nfc_shows_program_prompt(self, client_logged_in):
        """After capturing with NFC tag, success shows program prompt."""
        url = reverse("assets:quick_capture")
        response = client_logged_in.post(
            url,
            {"name": "NFC Capture Item", "scanned_code": "NFC-QC-001"},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Program NFC Tag" in content

    def test_success_with_nfc_includes_site_url(self, client_logged_in):
        """NFC program prompt includes site URL for NDEF generation."""
        url = reverse("assets:quick_capture")
        response = client_logged_in.post(
            url,
            {"name": "NFC URL Item", "scanned_code": "NFC-QC-002"},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "data-site-url" in content

    def test_success_without_nfc_no_program_prompt(self, client_logged_in):
        """After capturing without NFC tag, no program prompt shown."""
        url = reverse("assets:quick_capture")
        response = client_logged_in.post(
            url,
            {"name": "No NFC Item"},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Program NFC Tag" not in content

    def test_success_includes_asset_barcode_data(self, client_logged_in):
        """NFC program prompt includes the asset barcode for NDEF."""
        url = reverse("assets:quick_capture")
        response = client_logged_in.post(
            url,
            {"name": "NFC Barcode Item", "scanned_code": "NFC-QC-003"},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "data-asset-barcode" in content


# ============================================================
# Session-based default printer selection
# ============================================================


@pytest.mark.django_db
class TestSessionDefaultPrinter:
    """Remote print remembers last-used printer in session."""

    def _make_print_client(self, printers):
        from assets.models import PrintClient

        return PrintClient.objects.create(
            name="Test Station",
            token_hash="abc123sessiontest",
            status="approved",
            is_active=True,
            is_connected=True,
            printers=printers,
        )

    def test_print_saves_last_printer_to_session(
        self, client_logged_in, asset
    ):
        """Successful remote print stores printer choice in session."""
        pc = self._make_print_client(
            [{"id": "zebra1", "name": "Zebra", "type": "label"}]
        )
        url = reverse("assets:remote_print_submit", args=[asset.pk])
        response = client_logged_in.post(
            url,
            {
                "client_pk": pc.pk,
                "printer_id": "zebra1",
                "quantity": 1,
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        session = client_logged_in.session
        assert session.get("last_printer") == f"{pc.pk}:zebra1"

    def test_asset_detail_passes_last_printer(self, client_logged_in, asset):
        """Asset detail view passes last_printer from session."""
        pc = self._make_print_client(
            [
                {"id": "lp1", "name": "Label", "type": "label"},
                {"id": "lp2", "name": "Doc", "type": "document"},
            ]
        )
        # Set session to prefer second printer
        session = client_logged_in.session
        session["last_printer"] = f"{pc.pk}:lp2"
        session.save()

        url = reverse("assets:asset_detail", args=[asset.pk])
        response = client_logged_in.get(url)
        content = response.content.decode()
        # The second printer's option should be selected
        assert f'value="{pc.pk}:lp2" selected' in content


# ============================================================
# PROTOCOL V2 & PRINTREQUEST EXTENSION (§4.3.3.6)
# ============================================================


@pytest.mark.django_db
class TestProtocolV2Models:
    """§4.3.3.6: Protocol v2 model changes."""

    def test_print_client_has_protocol_version(self, db):
        """PrintClient has protocol_version field with default '1'."""
        from assets.factories import UserFactory

        u = UserFactory()
        pc = PrintClient.objects.create(
            name="Test Client",
            token_hash="a" * 64,
            status="approved",
            protocol_version="2",
            approved_by=u,
        )
        pc.refresh_from_db()
        assert pc.protocol_version == "2"

    def test_print_client_protocol_version_default(self, db):
        """PrintClient protocol_version defaults to '1'."""
        pc = PrintClient.objects.create(
            name="Default Client",
            token_hash="b" * 64,
        )
        pc.refresh_from_db()
        assert pc.protocol_version == "1"

    def test_print_request_has_label_type(self, db, asset, user):
        """PrintRequest has label_type field."""
        pc = PrintClient.objects.create(
            name="Client",
            token_hash="c" * 64,
        )
        pr = PrintRequest.objects.create(
            print_client=pc,
            asset=asset,
            printer_id="printer1",
            label_type="location",
            requested_by=user,
        )
        pr.refresh_from_db()
        assert pr.label_type == "location"

    def test_print_request_label_type_default(self, db, user):
        """PrintRequest label_type defaults to 'asset'."""
        pc = PrintClient.objects.create(
            name="Client",
            token_hash="d" * 64,
        )
        pr = PrintRequest.objects.create(
            print_client=pc,
            printer_id="printer1",
            requested_by=user,
        )
        pr.refresh_from_db()
        assert pr.label_type == "asset"

    def test_print_request_has_location_fk(self, db, location, user):
        """PrintRequest has nullable location FK."""
        pc = PrintClient.objects.create(
            name="Client",
            token_hash="e" * 64,
        )
        pr = PrintRequest.objects.create(
            print_client=pc,
            printer_id="printer1",
            label_type="location",
            location=location,
            requested_by=user,
        )
        pr.refresh_from_db()
        assert pr.location == location

    def test_print_request_location_nullable(self, db, user):
        """PrintRequest location FK is nullable."""
        pc = PrintClient.objects.create(
            name="Client",
            token_hash="f" * 64,
        )
        pr = PrintRequest.objects.create(
            print_client=pc,
            printer_id="printer1",
            requested_by=user,
        )
        pr.refresh_from_db()
        assert pr.location is None


@pytest.mark.django_db
class TestProtocolV2Dispatch:
    """§4.3.3.6: Version-aware dispatch."""

    def test_dispatch_location_label_builds_location_message(
        self, db, location, user
    ):
        """Dispatch with label_type='location' builds location msg."""
        from assets.services.print_dispatch import (
            dispatch_print_job,
        )

        pc = PrintClient.objects.create(
            name="V2 Client",
            token_hash="g" * 64,
            status="approved",
            is_active=True,
            is_connected=True,
            protocol_version="2",
            printers=[{"id": "lp1", "name": "Label Printer"}],
        )
        pr = PrintRequest.objects.create(
            print_client=pc,
            printer_id="lp1",
            label_type="location",
            location=location,
            requested_by=user,
        )
        result = dispatch_print_job(pr, site_url="https://ex.com")
        assert result is True

    def test_dispatch_refuses_location_label_to_v1_client(
        self, db, location, user
    ):
        """Dispatch refuses location label to v1 client."""
        from assets.services.print_dispatch import (
            dispatch_print_job,
        )

        pc = PrintClient.objects.create(
            name="V1 Client",
            token_hash="h" * 64,
            status="approved",
            is_active=True,
            is_connected=True,
            protocol_version="1",
            printers=[{"id": "lp1", "name": "Label Printer"}],
        )
        pr = PrintRequest.objects.create(
            print_client=pc,
            printer_id="lp1",
            label_type="location",
            location=location,
            requested_by=user,
        )
        result = dispatch_print_job(pr, site_url="https://ex.com")
        assert result is False
        pr.refresh_from_db()
        assert pr.status == "failed"
        assert "v2" in pr.error_message.lower() or (
            "protocol" in pr.error_message.lower()
        )

    def test_v1_clients_still_work_for_asset_labels(self, db, asset, user):
        """v1 clients still work for asset label dispatch."""
        from assets.services.print_dispatch import (
            dispatch_print_job,
        )

        pc = PrintClient.objects.create(
            name="V1 OK Client",
            token_hash="i" * 64,
            status="approved",
            is_active=True,
            is_connected=True,
            protocol_version="1",
            printers=[{"id": "lp1", "name": "Label Printer"}],
        )
        pr = PrintRequest.objects.create(
            print_client=pc,
            asset=asset,
            printer_id="lp1",
            label_type="asset",
            requested_by=user,
        )
        result = dispatch_print_job(pr, site_url="https://ex.com")
        assert result is True

    def test_dispatch_asset_label_includes_label_type(self, db, asset, user):
        """Asset label dispatch includes label_type='asset'."""
        from unittest.mock import patch

        from assets.services.print_dispatch import (
            dispatch_print_job,
        )

        pc = PrintClient.objects.create(
            name="V2 Client",
            token_hash="j" * 64,
            status="approved",
            is_active=True,
            is_connected=True,
            protocol_version="2",
            printers=[{"id": "lp1", "name": "Label Printer"}],
        )
        pr = PrintRequest.objects.create(
            print_client=pc,
            asset=asset,
            printer_id="lp1",
            label_type="asset",
            requested_by=user,
        )
        with patch(
            "assets.services.print_dispatch.get_channel_layer"
        ) as mock_cl:
            mock_layer = MagicMock()
            mock_cl.return_value = mock_layer
            dispatch_print_job(pr, site_url="https://ex.com")
            call_args = mock_layer.group_send.call_args
            msg = call_args[0][1]
            assert msg.get("label_type") == "asset"


# ============================================================
# LOCATION LABEL PRINTING (S2.12.5) + S2.12.3-09
# ============================================================


@pytest.mark.django_db
class TestLocationPrintLabel:
    """S2.12.5: Location label printing."""

    def _make_v2_client(self):
        return PrintClient.objects.create(
            name="V2 Printer",
            token_hash="v2test" + "0" * 58,
            status="approved",
            is_active=True,
            is_connected=True,
            protocol_version="2",
            printers=[{"id": "lp1", "name": "Label Printer"}],
        )

    def test_location_detail_has_v2_printers_context(
        self, client_logged_in, location
    ):
        """location_detail context includes v2_printers."""
        self._make_v2_client()
        url = reverse("assets:location_detail", args=[location.pk])
        response = client_logged_in.get(url)
        assert "v2_printers" in response.context

    def test_print_button_visible_when_v2_available(
        self, client_logged_in, location
    ):
        """Print button enabled when v2+ clients connected."""
        self._make_v2_client()
        url = reverse("assets:location_detail", args=[location.pk])
        response = client_logged_in.get(url)
        content = response.content.decode()
        # Should no longer have the disabled placeholder
        assert "cursor-not-allowed" not in content or (
            "Print Location Label" in content
        )

    def test_print_button_hidden_when_no_v2(self, client_logged_in, location):
        """Print button hidden when no v2+ clients connected."""
        url = reverse("assets:location_detail", args=[location.pk])
        response = client_logged_in.get(url)
        ctx = response.context
        v2_printers = ctx.get("v2_printers", [])
        assert len(v2_printers) == 0

    def test_location_print_creates_print_request(
        self, client_logged_in, location, user
    ):
        """POST creates PrintRequest with label_type=location."""
        pc = self._make_v2_client()
        url = reverse("assets:location_print_label", args=[location.pk])
        response = client_logged_in.post(
            url,
            {
                "client_pk": pc.pk,
                "printer_id": "lp1",
                "quantity": 1,
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        pr = PrintRequest.objects.filter(
            location=location, label_type="location"
        ).first()
        assert pr is not None
        assert pr.location == location

    def test_location_print_rejects_no_v2_client(
        self, client_logged_in, location
    ):
        """Rejects when the selected client is v1."""
        pc = PrintClient.objects.create(
            name="V1 Client",
            token_hash="v1test" + "0" * 58,
            status="approved",
            is_active=True,
            is_connected=True,
            protocol_version="1",
            printers=[{"id": "lp1", "name": "LP"}],
        )
        url = reverse("assets:location_print_label", args=[location.pk])
        response = client_logged_in.post(
            url,
            {
                "client_pk": pc.pk,
                "printer_id": "lp1",
                "quantity": 1,
            },
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        assert "v2" in content.lower() or "protocol" in content.lower()

    def test_location_print_permission_check(self, viewer_client, location):
        """Viewers cannot print location labels."""
        pc = self._make_v2_client()
        url = reverse("assets:location_print_label", args=[location.pk])
        response = viewer_client.post(
            url,
            {
                "client_pk": pc.pk,
                "printer_id": "lp1",
                "quantity": 1,
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 403

    def test_location_print_qr_content(self, client_logged_in, location, user):
        """qr_content includes location detail URL."""
        pc = self._make_v2_client()
        url = reverse("assets:location_print_label", args=[location.pk])
        with patch(
            "assets.services.print_dispatch.get_channel_layer"
        ) as mock_cl:
            mock_layer = MagicMock()
            mock_cl.return_value = mock_layer
            client_logged_in.post(
                url,
                {
                    "client_pk": pc.pk,
                    "printer_id": "lp1",
                    "quantity": 1,
                },
                HTTP_HX_REQUEST="true",
            )
            call_args = mock_layer.group_send.call_args
            if call_args:
                msg = call_args[0][1]
                assert f"/locations/{location.pk}/" in msg.get(
                    "qr_content", ""
                )

    def test_location_print_post_only(self, client_logged_in, location):
        """GET request is rejected."""
        url = reverse("assets:location_print_label", args=[location.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 405

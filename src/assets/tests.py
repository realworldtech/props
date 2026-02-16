"""Tests for the assets app — models, services, and views."""

from unittest.mock import patch

import pytest

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from assets.models import (
    Asset,
    AssetImage,
    AssetKit,
    AssetSerial,
    Category,
    Department,
    Location,
    NFCTag,
    StocktakeSession,
    Tag,
    Transaction,
)

User = get_user_model()


# ============================================================
# MODEL TESTS
# ============================================================


class TestDepartment:
    def test_str(self, department):
        assert str(department) == "Props"

    def test_ordering(self, db):
        Department.objects.create(name="Zzz")
        Department.objects.create(name="Aaa")
        names = list(Department.objects.values_list("name", flat=True))
        assert names == sorted(names)


class TestTag:
    def test_str(self, tag):
        assert str(tag) == "fragile"

    def test_default_color(self, db):
        t = Tag.objects.create(name="test")
        assert t.color == "gray"


class TestCategory:
    def test_str(self, category):
        assert str(category) == "Hand Props"

    def test_unique_per_department(self, category, department):
        with pytest.raises(Exception):
            Category.objects.create(name="Hand Props", department=department)


class TestLocation:
    def test_str_is_full_path(self, location):
        assert str(location) == "Main Store"

    def test_full_path_with_parent(self, location, child_location):
        assert child_location.full_path == "Main Store > Shelf A"

    def test_circular_reference_prevented(self, location, child_location):
        location.parent = child_location
        with pytest.raises(ValidationError):
            location.clean()

    def test_max_depth_enforced(self, db):
        l1 = Location.objects.create(name="L1")
        l2 = Location.objects.create(name="L2", parent=l1)
        l3 = Location.objects.create(name="L3", parent=l2)
        l4 = Location.objects.create(name="L4", parent=l3)
        l5 = Location(name="L5", parent=l4)
        with pytest.raises(ValidationError, match="nesting depth"):
            l5.clean()

    def test_get_descendants(self, location, child_location):
        grandchild = Location.objects.create(
            name="Box 1", parent=child_location
        )
        descendants = location.get_descendants()
        assert child_location in descendants
        assert grandchild in descendants

    def test_get_absolute_url(self, location):
        url = location.get_absolute_url()
        assert f"/locations/{location.pk}/" in url


class TestAsset:
    def test_str(self, asset):
        assert asset.name in str(asset)
        assert asset.barcode in str(asset)

    def test_barcode_auto_generated(self, asset):
        assert asset.barcode
        assert asset.barcode.startswith("ASSET-")

    def test_barcode_unique(self, asset, category, location, user):
        a2 = Asset(
            name="Another",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        a2.save()
        assert a2.barcode != asset.barcode

    def test_valid_transitions(self, asset):
        assert asset.can_transition_to("retired")
        assert asset.can_transition_to("missing")
        assert asset.can_transition_to("disposed")
        assert not asset.can_transition_to("draft")

    def test_draft_transitions(self, draft_asset):
        assert draft_asset.can_transition_to("active")
        assert draft_asset.can_transition_to("disposed")
        assert not draft_asset.can_transition_to("retired")

    def test_disposed_no_transitions(self, asset):
        asset.status = "disposed"
        assert not asset.can_transition_to("active")
        assert not asset.can_transition_to("draft")

    def test_clean_non_draft_requires_category(self, db, location, user):
        a = Asset(
            name="No Category",
            current_location=location,
            status="active",
            created_by=user,
        )
        a.barcode = "TEST-NOCAT123"
        with pytest.raises(ValidationError, match="category"):
            a.clean()

    def test_clean_non_draft_requires_location(self, db, category, user):
        a = Asset(
            name="No Location",
            category=category,
            status="active",
            created_by=user,
        )
        a.barcode = "TEST-NOLOC123"
        with pytest.raises(ValidationError, match="current_location"):
            a.clean()

    def test_clean_draft_allows_missing_fields(self, draft_asset):
        draft_asset.clean()  # Should not raise

    def test_is_checked_out(self, asset, second_user):
        assert not asset.is_checked_out
        asset.checked_out_to = second_user
        assert asset.is_checked_out

    def test_department_property(self, asset, department):
        assert asset.department == department

    def test_department_property_none(self, draft_asset):
        assert draft_asset.department is None

    def test_primary_image(self, asset):
        assert asset.primary_image is None

    def test_active_nfc_tags_empty(self, asset):
        assert asset.active_nfc_tags.count() == 0

    def test_get_absolute_url(self, asset):
        assert f"/assets/{asset.pk}/" in asset.get_absolute_url()


class TestAssetImage:
    def test_first_image_becomes_primary(self, asset):
        from django.core.files.uploadedfile import SimpleUploadedFile

        img_file = SimpleUploadedFile(
            "test.jpg",
            b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        image = AssetImage.objects.create(asset=asset, image=img_file)
        assert image.is_primary

    def test_setting_primary_unsets_others(self, asset):
        from django.core.files.uploadedfile import SimpleUploadedFile

        img1 = SimpleUploadedFile(
            "test1.jpg",
            b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        img2 = SimpleUploadedFile(
            "test2.jpg",
            b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        i1 = AssetImage.objects.create(
            asset=asset, image=img1, is_primary=True
        )
        i2 = AssetImage.objects.create(
            asset=asset, image=img2, is_primary=True
        )
        i1.refresh_from_db()
        assert not i1.is_primary
        assert i2.is_primary


class TestNFCTag:
    def test_str(self, asset, user):
        nfc = NFCTag.objects.create(
            tag_id="NFC-001", asset=asset, assigned_by=user
        )
        assert "NFC-001" in str(nfc)
        assert "active" in str(nfc)

    def test_is_active(self, asset, user):
        nfc = NFCTag.objects.create(
            tag_id="NFC-002", asset=asset, assigned_by=user
        )
        assert nfc.is_active

    def test_get_asset_by_tag(self, asset, user):
        NFCTag.objects.create(tag_id="NFC-003", asset=asset, assigned_by=user)
        found = NFCTag.get_asset_by_tag("NFC-003")
        assert found == asset

    def test_get_asset_by_tag_not_found(self, db):
        assert NFCTag.get_asset_by_tag("NONEXISTENT") is None

    def test_get_asset_by_tag_case_insensitive(self, asset, user):
        NFCTag.objects.create(tag_id="NFC-CASE", asset=asset, assigned_by=user)
        assert NFCTag.get_asset_by_tag("nfc-case") == asset

    def test_unique_active_constraint(self, asset, user):
        NFCTag.objects.create(
            tag_id="NFC-UNIQUE", asset=asset, assigned_by=user
        )
        with pytest.raises(Exception):
            NFCTag.objects.create(
                tag_id="NFC-UNIQUE", asset=asset, assigned_by=user
            )


class TestTransaction:
    def test_str(self, asset, user):
        txn = Transaction.objects.create(
            asset=asset, user=user, action="checkout"
        )
        assert asset.name in str(txn)
        assert "Check Out" in str(txn)


class TestStocktakeSession:
    def test_str(self, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        assert location.name in str(session)

    def test_expected_assets(self, asset, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        expected = session.expected_assets
        assert asset in expected

    def test_missing_assets(self, asset, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        missing = session.missing_assets
        assert asset in missing

    def test_confirmed_reduces_missing(self, asset, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        session.confirmed_assets.add(asset)
        assert asset not in session.missing_assets

    def test_unexpected_assets(self, asset, location, user):
        other_loc = Location.objects.create(name="Other Place")
        session = StocktakeSession.objects.create(
            location=other_loc, started_by=user
        )
        session.confirmed_assets.add(asset)
        unexpected = session.unexpected_assets
        assert asset in unexpected


# ============================================================
# SERVICE TESTS
# ============================================================


class TestStateService:
    def test_validate_transition_valid(self, asset):
        from assets.services.state import validate_transition

        validate_transition(asset, "retired")  # Should not raise

    def test_validate_transition_invalid(self, asset):
        from assets.services.state import validate_transition

        with pytest.raises(ValidationError):
            validate_transition(asset, "draft")

    def test_validate_transition_noop(self, asset):
        from assets.services.state import validate_transition

        validate_transition(asset, "active")  # Same status, no-op

    def test_validate_transition_bad_status(self, asset):
        from assets.services.state import validate_transition

        with pytest.raises(ValidationError, match="not a valid status"):
            validate_transition(asset, "bogus")

    def test_transition_asset(self, asset):
        from assets.services.state import transition_asset

        result = transition_asset(asset, "retired")
        assert result.status == "retired"
        asset.refresh_from_db()
        assert asset.status == "retired"


class TestTransactionService:
    def test_create_checkout(self, asset, second_user, user):
        from assets.services.transactions import create_checkout

        txn = create_checkout(asset, second_user, user, notes="Test")
        assert txn.action == "checkout"
        assert txn.borrower == second_user
        asset.refresh_from_db()
        assert asset.checked_out_to == second_user

    def test_create_checkin(self, asset, second_user, user, location):
        from assets.services.transactions import create_checkin

        asset.checked_out_to = second_user
        asset.save()
        new_loc = Location.objects.create(name="Return Spot")
        txn = create_checkin(asset, new_loc, user, notes="Returned")
        assert txn.action == "checkin"
        asset.refresh_from_db()
        assert asset.checked_out_to is None
        assert asset.current_location == new_loc

    def test_create_transfer(self, asset, user):
        from assets.services.transactions import create_transfer

        new_loc = Location.objects.create(name="New Location")
        txn = create_transfer(asset, new_loc, user)
        assert txn.action == "transfer"
        asset.refresh_from_db()
        assert asset.current_location == new_loc


class TestPermissionsService:
    def test_superuser_is_system_admin(self, admin_user):
        from assets.services.permissions import get_user_role

        assert get_user_role(admin_user) == "system_admin"

    def test_regular_user_is_viewer(self, viewer_user):
        from assets.services.permissions import get_user_role

        assert get_user_role(viewer_user) == "viewer"

    def test_member_user_role(self, user):
        from assets.services.permissions import get_user_role

        assert get_user_role(user) == "member"

    def test_department_manager_by_assignment(self, user, department):
        from assets.services.permissions import get_user_role

        department.managers.add(user)
        assert get_user_role(user, department) == "department_manager"

    def test_can_edit_asset_admin(self, admin_user, asset):
        from assets.services.permissions import can_edit_asset

        assert can_edit_asset(admin_user, asset)

    def test_can_edit_asset_viewer_denied(self, viewer_user, asset):
        from assets.services.permissions import can_edit_asset

        assert not can_edit_asset(viewer_user, asset)

    def test_member_can_edit_own_draft(self, member_user, db):
        from assets.services.permissions import can_edit_asset

        a = Asset(name="My Draft", status="draft", created_by=member_user)
        a.save()
        assert can_edit_asset(member_user, a)

    def test_member_cannot_edit_others_draft(self, member_user, draft_asset):
        from assets.services.permissions import can_edit_asset

        assert not can_edit_asset(member_user, draft_asset)

    def test_can_delete_asset(self, admin_user, asset):
        from assets.services.permissions import can_delete_asset

        assert can_delete_asset(admin_user, asset)

    def test_viewer_cannot_delete(self, viewer_user, asset):
        from assets.services.permissions import can_delete_asset

        assert not can_delete_asset(viewer_user, asset)

    def test_can_checkout_member(self, member_user, asset):
        from assets.services.permissions import can_checkout_asset

        assert can_checkout_asset(member_user, asset)

    def test_viewer_cannot_checkout(self, viewer_user, asset):
        from assets.services.permissions import can_checkout_asset

        assert not can_checkout_asset(viewer_user, asset)


class TestMergeService:
    def test_merge_moves_tags(self, asset, user, category, location):
        from assets.services.merge import merge_assets

        dup = Asset(
            name="Duplicate",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        dup.save()
        t = Tag.objects.create(name="merge-test")
        dup.tags.add(t)

        merge_assets(asset, [dup], user)
        assert t in asset.tags.all()
        dup.refresh_from_db()
        assert dup.status == "disposed"

    def test_merge_fills_missing_description(self, user, category, location):
        from assets.services.merge import merge_assets

        primary = Asset(
            name="Primary",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        primary.save()

        dup = Asset(
            name="Dup",
            description="Has a description",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        dup.save()

        merge_assets(primary, [dup], user)
        primary.refresh_from_db()
        assert primary.description == "Has a description"

    def test_merge_moves_transactions(self, asset, user, category, location):
        from assets.services.merge import merge_assets

        dup = Asset(
            name="Dup With Txn",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        dup.save()
        Transaction.objects.create(asset=dup, user=user, action="audit")

        merge_assets(asset, [dup], user)
        assert asset.transactions.filter(action="audit").exists()


class TestExportService:
    def test_export_returns_bytes(self, asset):
        from assets.services.export import export_assets_xlsx

        buffer = export_assets_xlsx(Asset.objects.all())
        assert buffer.getvalue()[:4] == b"PK\x03\x04"  # ZIP/XLSX magic

    def test_export_contains_sheets(self, asset):
        from io import BytesIO

        import openpyxl

        from assets.services.export import export_assets_xlsx

        buffer = export_assets_xlsx(Asset.objects.all())
        wb = openpyxl.load_workbook(BytesIO(buffer.getvalue()))
        assert "Summary" in wb.sheetnames
        assert "Assets" in wb.sheetnames

    def test_export_includes_asset_data(self, asset):
        from io import BytesIO

        import openpyxl

        from assets.services.export import export_assets_xlsx

        buffer = export_assets_xlsx(Asset.objects.all())
        wb = openpyxl.load_workbook(BytesIO(buffer.getvalue()))
        ws = wb["Assets"]
        # Row 1 is header, row 2 should be our asset
        assert ws.cell(row=2, column=1).value == asset.name


class TestBarcodeService:
    def test_generate_barcode_string(self):
        from assets.services.barcode import generate_barcode_string

        bc = generate_barcode_string()
        assert bc.startswith("ASSET-")
        assert len(bc) == 14  # "ASSET-" + 8 hex chars

    def test_generate_barcode_string_unique(self):
        from assets.services.barcode import generate_barcode_string

        codes = {generate_barcode_string() for _ in range(100)}
        assert len(codes) == 100

    def test_generate_code128_image(self):
        from assets.services.barcode import generate_code128_image

        result = generate_code128_image("ASSET-TEST1234")
        assert result is not None
        # Should be a PNG
        data = result.read()
        assert len(data) > 0

    def test_get_asset_url(self):
        from assets.services.barcode import get_asset_url

        url = get_asset_url("ASSET-ABCD1234")
        assert "/a/ASSET-ABCD1234/" in url


class TestBulkService:
    def test_bulk_transfer(self, asset, user):
        from assets.services.bulk import bulk_transfer

        new_loc = Location.objects.create(name="Bulk Dest")
        result = bulk_transfer([asset.pk], new_loc.pk, user)
        assert result["transferred"] == 1
        asset.refresh_from_db()
        assert asset.current_location == new_loc

    def test_bulk_transfer_skips_checked_out(self, asset, user, second_user):
        from assets.services.bulk import bulk_transfer

        asset.checked_out_to = second_user
        asset.save()
        new_loc = Location.objects.create(name="Bulk Dest 2")
        result = bulk_transfer([asset.pk], new_loc.pk, user)
        assert result["transferred"] == 0
        assert len(result["skipped"]) == 1

    def test_bulk_status_change(self, asset, user):
        from assets.services.bulk import bulk_status_change

        count, failures = bulk_status_change([asset.pk], "retired", user)
        assert count == 1
        assert failures == []
        asset.refresh_from_db()
        assert asset.status == "retired"

    def test_bulk_status_change_skips_invalid(self, asset, user):
        from assets.services.bulk import bulk_status_change

        # active -> draft is not a valid transition
        count, failures = bulk_status_change([asset.pk], "draft", user)
        assert count == 0
        assert len(failures) == 1


# ============================================================
# VIEW TESTS
# ============================================================


class TestDashboardView:
    def test_requires_login(self, client, db):
        response = client.get(reverse("assets:dashboard"))
        assert response.status_code == 302

    def test_renders_for_logged_in(self, client_logged_in):
        response = client_logged_in.get(reverse("assets:dashboard"))
        assert response.status_code == 200


class TestAssetListView:
    def test_requires_login(self, client, db):
        response = client.get(reverse("assets:asset_list"))
        assert response.status_code == 302

    def test_renders(self, client_logged_in, asset):
        response = client_logged_in.get(reverse("assets:asset_list"))
        assert response.status_code == 200

    def test_search_filter(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?q=Test+Prop"
        )
        assert response.status_code == 200

    def test_status_filter(self, client_logged_in, asset, draft_asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?status=draft"
        )
        assert response.status_code == 200

    def test_pagination_size(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?page_size=50"
        )
        assert response.status_code == 200

    def test_invalid_page_size_defaults(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?page_size=999"
        )
        assert response.status_code == 200


class TestAssetDetailView:
    def test_renders(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert response.status_code == 200

    def test_404_for_nonexistent(self, client_logged_in):
        response = client_logged_in.get(
            reverse("assets:asset_detail", args=[99999])
        )
        assert response.status_code == 404


class TestAssetCreateView:
    def test_renders_form(self, client_logged_in):
        response = client_logged_in.get(reverse("assets:asset_create"))
        assert response.status_code == 200

    def test_create_asset(self, client_logged_in, category, location):
        response = client_logged_in.post(
            reverse("assets:asset_create"),
            {
                "name": "New Asset",
                "status": "active",
                "category": category.pk,
                "current_location": location.pk,
                "quantity": 1,
                "condition": "good",
            },
        )
        assert response.status_code == 302
        assert Asset.objects.filter(name="New Asset").exists()


class TestAssetEditView:
    def test_renders_form(self, admin_client, asset):
        response = admin_client.get(
            reverse("assets:asset_edit", args=[asset.pk])
        )
        assert response.status_code == 200

    def test_edit_asset(self, admin_client, asset, category, location):
        response = admin_client.post(
            reverse("assets:asset_edit", args=[asset.pk]),
            {
                "name": "Updated Name",
                "status": "active",
                "category": category.pk,
                "current_location": location.pk,
                "quantity": 1,
                "condition": "fair",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.name == "Updated Name"


class TestAssetDeleteView:
    def test_renders_confirmation(self, admin_client, asset):
        response = admin_client.get(
            reverse("assets:asset_delete", args=[asset.pk])
        )
        assert response.status_code == 200

    def test_soft_deletes(self, admin_client, asset):
        response = admin_client.post(
            reverse("assets:asset_delete", args=[asset.pk])
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == "disposed"

    def test_cannot_delete_checked_out(self, admin_client, asset, second_user):
        asset.checked_out_to = second_user
        asset.save()
        response = admin_client.post(
            reverse("assets:asset_delete", args=[asset.pk])
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status != "disposed"


class TestQuickCaptureView:
    def test_renders(self, admin_client):
        response = admin_client.get(reverse("assets:quick_capture"))
        assert response.status_code == 200

    def test_create_draft_with_name(self, admin_client):
        response = admin_client.post(
            reverse("assets:quick_capture"),
            {"name": "Quick Item"},
        )
        assert response.status_code == 200
        assert Asset.objects.filter(name="Quick Item", status="draft").exists()

    def test_auto_name_when_empty(self, admin_client):
        from io import BytesIO

        from PIL import Image

        from django.core.files.uploadedfile import SimpleUploadedFile

        # Create a valid JPEG image
        buf = BytesIO()
        Image.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)

        img = SimpleUploadedFile(
            "photo.jpg",
            buf.getvalue(),
            content_type="image/jpeg",
        )
        response = admin_client.post(
            reverse("assets:quick_capture"),
            {"image": img},
        )
        assert response.status_code == 200
        # Should have created a draft with auto-generated name
        latest = Asset.objects.filter(status="draft").latest("created_at")
        assert latest.name.startswith("Quick Capture")

    def test_barcode_conflict(self, admin_client, asset):
        response = admin_client.post(
            reverse("assets:quick_capture"),
            {"name": "Conflict", "scanned_code": asset.barcode},
        )
        assert response.status_code == 200
        # Should show error, not create new asset
        assert not Asset.objects.filter(name="Conflict").exists()

    def test_nfc_tag_assignment(self, admin_client):
        response = admin_client.post(
            reverse("assets:quick_capture"),
            {"name": "NFC Item", "scanned_code": "my-nfc-tag-123"},
        )
        assert response.status_code == 200
        a = Asset.objects.get(name="NFC Item")
        assert a.nfc_tags.filter(tag_id="my-nfc-tag-123").exists()


class TestScanViews:
    def test_scan_view_renders(self, client_logged_in):
        response = client_logged_in.get(reverse("assets:scan"))
        assert response.status_code == 200

    def test_scan_lookup_barcode(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:scan_lookup") + f"?code={asset.barcode}"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["found"] is True
        assert data["asset_name"] == asset.name

    def test_scan_lookup_nfc(self, client_logged_in, asset, user):
        NFCTag.objects.create(tag_id="SCAN-NFC", asset=asset, assigned_by=user)
        response = client_logged_in.get(
            reverse("assets:scan_lookup") + "?code=SCAN-NFC"
        )
        data = response.json()
        assert data["found"] is True

    def test_scan_lookup_not_found(self, client_logged_in, db):
        response = client_logged_in.get(
            reverse("assets:scan_lookup") + "?code=NONEXISTENT"
        )
        data = response.json()
        assert data["found"] is False
        assert "quick_capture_url" in data

    def test_scan_lookup_empty(self, client_logged_in, db):
        response = client_logged_in.get(
            reverse("assets:scan_lookup") + "?code="
        )
        data = response.json()
        assert data["found"] is False


class TestAssetByIdentifier:
    def test_barcode_redirect(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse(
                "assets:asset_by_identifier",
                args=[asset.barcode],
            )
        )
        assert response.status_code == 302
        assert f"/assets/{asset.pk}/" in response.url

    def test_nfc_redirect(self, client_logged_in, asset, user):
        NFCTag.objects.create(tag_id="ID-NFC", asset=asset, assigned_by=user)
        response = client_logged_in.get(
            reverse("assets:asset_by_identifier", args=["ID-NFC"])
        )
        assert response.status_code == 302

    def test_unknown_redirects_to_quick_capture(self, client_logged_in, db):
        response = client_logged_in.get(
            reverse("assets:asset_by_identifier", args=["UNKNOWN"])
        )
        assert response.status_code == 302
        assert "quick-capture" in response.url


class TestCheckoutCheckinTransfer:
    def test_checkout_renders(self, admin_client, asset):
        response = admin_client.get(
            reverse("assets:asset_checkout", args=[asset.pk])
        )
        assert response.status_code == 200

    def test_checkout_asset(self, admin_client, asset, second_user):
        response = admin_client.post(
            reverse("assets:asset_checkout", args=[asset.pk]),
            {"borrower": second_user.pk, "notes": "For show"},
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.checked_out_to == second_user
        assert Transaction.objects.filter(
            asset=asset, action="checkout"
        ).exists()

    def test_cannot_checkout_already_checked_out(
        self, admin_client, asset, second_user
    ):
        asset.checked_out_to = second_user
        asset.save()
        response = admin_client.get(
            reverse("assets:asset_checkout", args=[asset.pk])
        )
        assert response.status_code == 302

    def test_checkin_renders(self, admin_client, asset):
        response = admin_client.get(
            reverse("assets:asset_checkin", args=[asset.pk])
        )
        assert response.status_code == 200

    def test_checkin_asset(self, admin_client, asset, second_user, location):
        asset.checked_out_to = second_user
        asset.save()
        new_loc = Location.objects.create(name="Check-in Loc")
        response = admin_client.post(
            reverse("assets:asset_checkin", args=[asset.pk]),
            {"location": new_loc.pk},
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.checked_out_to is None
        assert asset.current_location == new_loc

    def test_transfer_renders(self, admin_client, asset):
        response = admin_client.get(
            reverse("assets:asset_transfer", args=[asset.pk])
        )
        assert response.status_code == 200

    def test_transfer_asset(self, admin_client, asset):
        new_loc = Location.objects.create(name="Transfer Dest")
        response = admin_client.post(
            reverse("assets:asset_transfer", args=[asset.pk]),
            {"location": new_loc.pk},
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.current_location == new_loc

    def test_cannot_transfer_checked_out(
        self, admin_client, asset, second_user
    ):
        asset.checked_out_to = second_user
        asset.save()
        response = admin_client.get(
            reverse("assets:asset_transfer", args=[asset.pk])
        )
        assert response.status_code == 302


class TestTransactionListView:
    def test_renders(self, client_logged_in, db):
        response = client_logged_in.get(reverse("assets:transaction_list"))
        assert response.status_code == 200

    def test_filter_by_action(self, client_logged_in, db):
        response = client_logged_in.get(
            reverse("assets:transaction_list") + "?action=checkout"
        )
        assert response.status_code == 200


class TestCRUDViews:
    """Test CRUD views for categories, locations, and tags."""

    def test_category_list(self, client_logged_in, category):
        response = client_logged_in.get(reverse("assets:category_list"))
        assert response.status_code == 200

    def test_category_create(self, admin_client, department):
        response = admin_client.post(
            reverse("assets:category_create"),
            {
                "name": "New Cat",
                "department": department.pk,
            },
        )
        assert response.status_code == 302
        assert Category.objects.filter(name="New Cat").exists()

    def test_category_edit(self, admin_client, category):
        response = admin_client.post(
            reverse("assets:category_edit", args=[category.pk]),
            {
                "name": "Updated Cat",
                "department": category.department.pk,
            },
        )
        assert response.status_code == 302
        category.refresh_from_db()
        assert category.name == "Updated Cat"

    def test_location_list(self, client_logged_in, location):
        response = client_logged_in.get(reverse("assets:location_list"))
        assert response.status_code == 200

    def test_location_detail(self, client_logged_in, location):
        response = client_logged_in.get(
            reverse("assets:location_detail", args=[location.pk])
        )
        assert response.status_code == 200

    def test_location_create(self, admin_client):
        response = admin_client.post(
            reverse("assets:location_create"),
            {"name": "New Loc"},
        )
        assert response.status_code == 302
        assert Location.objects.filter(name="New Loc").exists()

    def test_location_edit(self, admin_client, location):
        response = admin_client.post(
            reverse("assets:location_edit", args=[location.pk]),
            {"name": "Updated Loc"},
        )
        assert response.status_code == 302
        location.refresh_from_db()
        assert location.name == "Updated Loc"

    def test_tag_list(self, client_logged_in, tag):
        response = client_logged_in.get(reverse("assets:tag_list"))
        assert response.status_code == 200

    def test_tag_create(self, admin_client):
        response = admin_client.post(
            reverse("assets:tag_create"),
            {"name": "newtag", "color": "blue"},
        )
        assert response.status_code == 302
        assert Tag.objects.filter(name="newtag").exists()

    def test_tag_edit(self, admin_client, tag):
        response = admin_client.post(
            reverse("assets:tag_edit", args=[tag.pk]),
            {"name": "updated-tag", "color": "green"},
        )
        assert response.status_code == 302
        tag.refresh_from_db()
        assert tag.name == "updated-tag"


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


class TestStocktakeViews:
    def test_stocktake_list(self, client_logged_in, db):
        response = client_logged_in.get(reverse("assets:stocktake_list"))
        assert response.status_code == 200

    def test_stocktake_start_renders(self, client_logged_in, location):
        response = client_logged_in.get(reverse("assets:stocktake_start"))
        assert response.status_code == 200

    def test_stocktake_start_creates_session(self, client_logged_in, location):
        response = client_logged_in.post(
            reverse("assets:stocktake_start"),
            {"location": location.pk},
        )
        assert response.status_code == 302
        assert StocktakeSession.objects.filter(location=location).exists()

    def test_stocktake_start_resumes_existing(
        self, client_logged_in, location, user
    ):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        response = client_logged_in.post(
            reverse("assets:stocktake_start"),
            {"location": location.pk},
        )
        assert response.status_code == 302
        assert f"/stocktake/{session.pk}/" in response.url

    def test_stocktake_detail(self, client_logged_in, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        response = client_logged_in.get(
            reverse("assets:stocktake_detail", args=[session.pk])
        )
        assert response.status_code == 200

    def test_stocktake_confirm_by_id(
        self, client_logged_in, asset, location, user
    ):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        response = client_logged_in.post(
            reverse("assets:stocktake_confirm", args=[session.pk]),
            {"asset_id": asset.pk},
        )
        assert response.status_code == 302
        assert asset in session.confirmed_assets.all()

    def test_stocktake_confirm_by_barcode(
        self, client_logged_in, asset, location, user
    ):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        response = client_logged_in.post(
            reverse("assets:stocktake_confirm", args=[session.pk]),
            {"code": asset.barcode},
        )
        assert response.status_code == 302
        assert asset in session.confirmed_assets.all()

    def test_stocktake_complete(self, admin_client, location, admin_user):
        session = StocktakeSession.objects.create(
            location=location, started_by=admin_user
        )
        response = admin_client.post(
            reverse("assets:stocktake_complete", args=[session.pk]),
            {"action": "complete"},
        )
        assert response.status_code == 302
        session.refresh_from_db()
        assert session.status == "completed"

    def test_stocktake_abandon(self, admin_client, location, admin_user):
        session = StocktakeSession.objects.create(
            location=location, started_by=admin_user
        )
        response = admin_client.post(
            reverse("assets:stocktake_complete", args=[session.pk]),
            {"action": "abandon"},
        )
        assert response.status_code == 302
        session.refresh_from_db()
        assert session.status == "abandoned"

    def test_stocktake_complete_marks_missing(
        self, admin_client, asset, location, admin_user
    ):
        session = StocktakeSession.objects.create(
            location=location, started_by=admin_user
        )
        # Don't confirm the asset
        response = admin_client.post(
            reverse("assets:stocktake_complete", args=[session.pk]),
            {"action": "complete", "mark_missing": "1"},
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == "missing"


class TestExportView:
    def test_export_returns_xlsx(self, client_logged_in, asset):
        response = client_logged_in.get(reverse("assets:export_assets"))
        assert response.status_code == 200
        assert "spreadsheetml" in response["Content-Type"]

    def test_export_with_filter(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:export_assets") + "?status=active"
        )
        assert response.status_code == 200


class TestLabelViews:
    def test_label_renders(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_label", args=[asset.pk])
        )
        assert response.status_code == 200

    @patch("assets.services.zebra.print_zpl")
    @patch("assets.services.zebra.generate_zpl")
    def test_label_zpl_print(
        self, mock_gen, mock_print, client_logged_in, asset
    ):
        mock_gen.return_value = "^XA^XZ"
        mock_print.return_value = True
        response = client_logged_in.get(
            reverse("assets:asset_label_zpl", args=[asset.pk]) + "?raw=1"
        )
        # Raw mode returns ZPL text
        assert response.status_code == 200

    def test_label_zpl_raw(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_label_zpl", args=[asset.pk]) + "?raw=1"
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/plain"


class TestMergeViews:
    def test_merge_select_requires_2(self, admin_client, asset):
        response = admin_client.post(
            reverse("assets:asset_merge_select"),
            {"asset_ids": [asset.pk]},
        )
        assert response.status_code == 302

    def test_merge_select_renders(
        self, admin_client, asset, category, location, user
    ):
        a2 = Asset(
            name="Asset 2",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        a2.save()
        response = admin_client.post(
            reverse("assets:asset_merge_select"),
            {"asset_ids": [asset.pk, a2.pk]},
        )
        assert response.status_code == 200

    def test_merge_execute(
        self, admin_client, asset, category, location, user
    ):
        a2 = Asset(
            name="Merge Me",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        a2.save()
        response = admin_client.post(
            reverse("assets:asset_merge_execute"),
            {
                "primary_id": asset.pk,
                "asset_ids": f"{asset.pk},{a2.pk}",
            },
        )
        assert response.status_code == 302
        a2.refresh_from_db()
        assert a2.status == "disposed"


class TestDraftsQueueView:
    def test_renders(self, client_logged_in, draft_asset):
        response = client_logged_in.get(reverse("assets:drafts_queue"))
        assert response.status_code == 200


# ============================================================
# EDGE CASE TESTS (§7)
# ============================================================


class TestEdgeCaseStateTransitions:
    """§7.5: State transition edge cases."""

    def test_cannot_retire_checked_out_asset(self, asset, second_user):
        from assets.services.state import validate_transition

        asset.checked_out_to = second_user
        asset.save()
        with pytest.raises(ValidationError, match="Check it in"):
            validate_transition(asset, "retired")

    def test_cannot_dispose_checked_out_asset(self, asset, second_user):
        from assets.services.state import validate_transition

        asset.checked_out_to = second_user
        asset.save()
        with pytest.raises(ValidationError, match="Check it in"):
            validate_transition(asset, "disposed")


class TestEdgeCaseMerge:
    """§7.1: Merge edge cases."""

    def test_cannot_merge_checked_out_primary(
        self, asset, second_user, user, category, location
    ):
        from assets.services.merge import merge_assets

        asset.checked_out_to = second_user
        asset.save()
        dup = Asset(
            name="Dup",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        dup.save()
        with pytest.raises(ValueError, match="checked out"):
            merge_assets(asset, [dup], user)

    def test_cannot_merge_checked_out_duplicate(
        self, asset, second_user, user, category, location
    ):
        from assets.services.merge import merge_assets

        dup = Asset(
            name="Checked Out Dup",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=user,
        )
        dup.save()
        dup.checked_out_to = second_user
        dup.save()
        with pytest.raises(ValueError, match="checked out"):
            merge_assets(asset, [dup], user)


class TestEdgeCaseImageUpload:
    """§7.8: Image upload validation."""

    def test_rejects_oversized_image(self, client_logged_in, asset):
        from django.core.files.uploadedfile import SimpleUploadedFile

        # Create a file that claims to be > 10 MB
        big_file = SimpleUploadedFile(
            "big.jpg",
            b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        big_file.size = 11 * 1024 * 1024  # Fake size > 10 MB

        response = client_logged_in.post(
            reverse("assets:image_upload", args=[asset.pk]),
            {"image": big_file, "caption": "Too big"},
        )
        assert response.status_code == 302
        assert asset.images.count() == 0


class TestEdgeCasePageSize:
    """§7.3: Null safety for page_size."""

    def test_non_integer_page_size(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?page_size=abc"
        )
        assert response.status_code == 200


# ============================================================
# BATCH A-G COVERAGE TESTS
# ============================================================


class TestPermissionEnforcement:
    """Test that permission checks are properly enforced on views."""

    def test_viewer_cannot_edit_asset_get(self, viewer_client, asset):
        response = viewer_client.get(
            reverse("assets:asset_edit", args=[asset.pk])
        )
        assert response.status_code == 403

    def test_viewer_cannot_edit_asset_post(
        self, viewer_client, asset, category, location
    ):
        response = viewer_client.post(
            reverse("assets:asset_edit", args=[asset.pk]),
            {
                "name": "Hacked Name",
                "status": "active",
                "category": category.pk,
                "current_location": location.pk,
                "quantity": 1,
                "condition": "good",
            },
        )
        assert response.status_code == 403
        asset.refresh_from_db()
        assert asset.name != "Hacked Name"

    def test_viewer_cannot_delete_asset(self, viewer_client, asset):
        response = viewer_client.get(
            reverse("assets:asset_delete", args=[asset.pk])
        )
        assert response.status_code == 403

    def test_viewer_cannot_checkout(self, viewer_client, asset):
        response = viewer_client.get(
            reverse("assets:asset_checkout", args=[asset.pk])
        )
        assert response.status_code == 403

    def test_viewer_cannot_quick_capture(self, viewer_client):
        response = viewer_client.get(reverse("assets:quick_capture"))
        assert response.status_code == 403

    def test_viewer_cannot_create_category(self, viewer_client, department):
        response = viewer_client.post(
            reverse("assets:category_create"),
            {"name": "Sneaky Cat", "department": department.pk},
        )
        assert response.status_code == 403
        assert not Category.objects.filter(name="Sneaky Cat").exists()

    def test_viewer_cannot_create_location(self, viewer_client):
        response = viewer_client.post(
            reverse("assets:location_create"),
            {"name": "Sneaky Loc"},
        )
        assert response.status_code == 403

    def test_viewer_cannot_merge(self, viewer_client, asset):
        response = viewer_client.post(
            reverse("assets:asset_merge_select"),
            {"asset_ids": [asset.pk]},
        )
        assert response.status_code == 403

    def test_member_can_checkout(self, member_client, asset, second_user):
        response = member_client.post(
            reverse("assets:asset_checkout", args=[asset.pk]),
            {"borrower": second_user.pk, "notes": "Member checkout"},
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.checked_out_to == second_user

    def test_member_cannot_delete(self, member_client, asset):
        response = member_client.get(
            reverse("assets:asset_delete", args=[asset.pk])
        )
        assert response.status_code == 403

    def test_admin_can_edit(self, admin_client, asset, category, location):
        response = admin_client.post(
            reverse("assets:asset_edit", args=[asset.pk]),
            {
                "name": "Admin Edited",
                "status": "active",
                "category": category.pk,
                "current_location": location.pk,
                "quantity": 1,
                "condition": "good",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.name == "Admin Edited"

    def test_admin_can_delete(self, admin_client, asset):
        response = admin_client.post(
            reverse("assets:asset_delete", args=[asset.pk])
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == "disposed"

    def test_admin_can_checkout(self, admin_client, asset, second_user):
        response = admin_client.post(
            reverse("assets:asset_checkout", args=[asset.pk]),
            {"borrower": second_user.pk, "notes": "Admin checkout"},
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.checked_out_to == second_user

    def test_asset_detail_context_has_permissions(self, admin_client, asset):
        response = admin_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert response.status_code == 200
        assert response.context["can_edit"] is True
        assert response.context["can_delete"] is True
        assert response.context["can_checkout"] is True


class TestStateTransitionValidation:
    """Test form-level state transition validation."""

    def test_form_rejects_invalid_transition(
        self, admin_client, asset, category, location
    ):
        # active -> draft is not valid
        response = admin_client.post(
            reverse("assets:asset_edit", args=[asset.pk]),
            {
                "name": asset.name,
                "status": "draft",
                "category": category.pk,
                "current_location": location.pk,
                "quantity": 1,
                "condition": "good",
            },
        )
        # Should re-render the form with errors, not redirect
        assert response.status_code == 200
        asset.refresh_from_db()
        assert asset.status == "active"

    def test_form_allows_valid_transition(
        self, admin_client, asset, category, location
    ):
        # active -> retired is valid
        response = admin_client.post(
            reverse("assets:asset_edit", args=[asset.pk]),
            {
                "name": asset.name,
                "status": "retired",
                "category": category.pk,
                "current_location": location.pk,
                "quantity": 1,
                "condition": "good",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == "retired"

    def test_form_includes_retired_choice(self):
        from assets.forms import FORM_STATUS_CHOICES

        status_keys = [k for k, v in FORM_STATUS_CHOICES]
        assert "retired" in status_keys


class TestMyBorrowedItemsView:
    """Test the My Borrowed Items view (Batch D)."""

    def test_renders(self, client_logged_in):
        response = client_logged_in.get(reverse("assets:my_borrowed_items"))
        assert response.status_code == 200

    def test_shows_only_users_items(
        self, client_logged_in, asset, user, second_user
    ):
        # Check out asset to the logged-in user
        asset.checked_out_to = user
        asset.save()

        # Create another asset checked out to second_user
        a2 = Asset(
            name="Other Borrowed",
            category=asset.category,
            current_location=asset.current_location,
            status="active",
            created_by=user,
            checked_out_to=second_user,
        )
        a2.save()

        response = client_logged_in.get(reverse("assets:my_borrowed_items"))
        assert response.status_code == 200
        assets_in_ctx = list(response.context["assets"])
        assert asset in assets_in_ctx
        assert a2 not in assets_in_ctx

    def test_empty_when_nothing_borrowed(self, client_logged_in):
        response = client_logged_in.get(reverse("assets:my_borrowed_items"))
        assert response.status_code == 200
        assert len(response.context["assets"]) == 0


class TestDashboardBreakdowns:
    """Test dashboard context includes breakdown data (Batch D)."""

    def test_context_has_breakdowns(
        self, client_logged_in, asset, department, category, location, tag
    ):
        asset.tags.add(tag)
        response = client_logged_in.get(reverse("assets:dashboard"))
        assert response.status_code == 200
        ctx = response.context
        assert "dept_counts" in ctx
        assert "cat_counts" in ctx
        assert "loc_counts" in ctx
        assert "top_tags" in ctx

    def test_department_counts_accurate(
        self, client_logged_in, asset, department
    ):
        response = client_logged_in.get(reverse("assets:dashboard"))
        dept_counts = list(response.context["dept_counts"])
        # Our department should have at least 1 active asset
        dept_names = [d.name for d in dept_counts]
        assert department.name in dept_names

    def test_category_counts_accurate(self, client_logged_in, asset, category):
        response = client_logged_in.get(reverse("assets:dashboard"))
        cat_counts = list(response.context["cat_counts"])
        cat_names = [c.name for c in cat_counts]
        assert category.name in cat_names


class TestLocationDeactivateView:
    """Test location deactivation (Batch D)."""

    def test_deactivate_empty_location(self, admin_client, db):
        empty_loc = Location.objects.create(name="Empty Place")
        response = admin_client.post(
            reverse("assets:location_deactivate", args=[empty_loc.pk])
        )
        assert response.status_code == 302
        empty_loc.refresh_from_db()
        assert empty_loc.is_active is False

    def test_cannot_deactivate_with_active_assets(
        self, admin_client, location, asset
    ):
        response = admin_client.post(
            reverse("assets:location_deactivate", args=[location.pk])
        )
        assert response.status_code == 302
        location.refresh_from_db()
        assert location.is_active is True

    def test_viewer_cannot_deactivate(self, client_logged_in, location):
        response = client_logged_in.post(
            reverse("assets:location_deactivate", args=[location.pk])
        )
        assert response.status_code == 403


class TestBulkActionsView:
    """Test bulk operations view (Batch D)."""

    def test_bulk_transfer(self, admin_client, asset):
        new_loc = Location.objects.create(name="Bulk Target")
        response = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "asset_ids": [asset.pk],
                "bulk_action": "transfer",
                "location": new_loc.pk,
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.current_location == new_loc

    def test_bulk_status_change(self, admin_client, asset):
        response = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "asset_ids": [asset.pk],
                "bulk_action": "status_change",
                "new_status": "retired",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.status == "retired"

    def test_bulk_no_selection(self, admin_client):
        response = admin_client.post(
            reverse("assets:bulk_actions"),
            {"bulk_action": "transfer"},
        )
        assert response.status_code == 302

    def test_get_redirects(self, admin_client):
        response = admin_client.get(reverse("assets:bulk_actions"))
        assert response.status_code == 302

    def test_bulk_print_labels(self, admin_client, asset):
        response = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "asset_ids": [asset.pk],
                "bulk_action": "print_labels",
            },
        )
        assert response.status_code == 200
        assert "label_assets" in response.context


class TestStocktakeAuditTransactions:
    """Test stocktake confirm creates audit transactions (Batch D)."""

    def test_confirm_creates_audit_transaction(
        self, admin_client, asset, location, user
    ):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        admin_client.post(
            reverse("assets:stocktake_confirm", args=[session.pk]),
            {"asset_id": asset.pk},
        )
        assert Transaction.objects.filter(asset=asset, action="audit").exists()

    def test_scan_confirm_creates_audit_transaction(
        self, admin_client, asset, location, user
    ):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        admin_client.post(
            reverse("assets:stocktake_confirm", args=[session.pk]),
            {"code": asset.barcode},
        )
        assert Transaction.objects.filter(asset=asset, action="audit").exists()


class TestStocktakeSummaryView:
    """Test stocktake summary view (Batch E)."""

    def test_summary_renders(self, client_logged_in, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user, status="completed"
        )
        response = client_logged_in.get(
            reverse("assets:stocktake_summary", args=[session.pk])
        )
        assert response.status_code == 200
        ctx = response.context
        assert "total_expected" in ctx
        assert "confirmed_count" in ctx
        assert "missing_count" in ctx

    def test_complete_redirects_to_summary(self, admin_client, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        response = admin_client.post(
            reverse("assets:stocktake_complete", args=[session.pk]),
            {"action": "complete"},
        )
        assert response.status_code == 302
        assert f"/stocktake/{session.pk}/summary/" in response.url


class TestExportImprovements:
    """Test export filters and filename (Batch E)."""

    def test_export_date_stamped_filename(self, admin_client, asset):
        from datetime import date

        response = admin_client.get(reverse("assets:export_assets"))
        assert response.status_code == 200
        disposition = response["Content-Disposition"]
        assert date.today().isoformat() in disposition
        assert disposition.startswith("attachment;")

    def test_export_with_category_filter(self, admin_client, asset, category):
        response = admin_client.get(
            reverse("assets:export_assets") + f"?category={category.pk}"
        )
        assert response.status_code == 200
        assert "spreadsheetml" in response["Content-Type"]

    def test_export_with_location_filter(self, admin_client, asset, location):
        response = admin_client.get(
            reverse("assets:export_assets") + f"?location={location.pk}"
        )
        assert response.status_code == 200

    def test_export_with_search_query(self, admin_client, asset):
        response = admin_client.get(
            reverse("assets:export_assets") + "?q=Test"
        )
        assert response.status_code == 200


class TestLabelQRCode:
    """Test QR code on labels (Batch E)."""

    def test_label_has_qr_data(self, admin_client, asset):
        response = admin_client.get(
            reverse("assets:asset_label", args=[asset.pk])
        )
        assert response.status_code == 200
        ctx = response.context
        assert "qr_data_uri" in ctx
        # Should be a data URI with base64 PNG
        assert ctx["qr_data_uri"].startswith("data:image/png;base64,")


class TestAIAutoTrigger:
    """Test AI analysis is auto-triggered on image upload (Batch C)."""

    @patch(
        "props.context_processors.is_ai_analysis_enabled", return_value=True
    )
    @patch("assets.tasks.analyse_image.delay")
    def test_image_upload_triggers_ai(
        self, mock_delay, mock_enabled, admin_client, asset, admin_user
    ):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img = SimpleUploadedFile(
            "test.jpg", buf.getvalue(), content_type="image/jpeg"
        )

        admin_client.post(
            reverse("assets:image_upload", args=[asset.pk]),
            {"image": img, "caption": "test"},
        )
        assert mock_delay.called

    @patch(
        "props.context_processors.is_ai_analysis_enabled", return_value=True
    )
    @patch("assets.tasks.analyse_image.delay")
    def test_quick_capture_triggers_ai(
        self, mock_delay, mock_enabled, admin_client
    ):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "blue").save(buf, "JPEG")
        buf.seek(0)
        img = SimpleUploadedFile(
            "cap.jpg", buf.getvalue(), content_type="image/jpeg"
        )

        admin_client.post(
            reverse("assets:quick_capture"),
            {"name": "AI Capture Test", "image": img},
        )
        assert mock_delay.called


class TestAICostControls:
    """Test AI daily limit enforcement (Batch C)."""

    @patch("assets.services.ai.analyse_image_data")
    def test_daily_limit_skips_analysis(self, mock_api, db, asset, user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from django.utils import timezone

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "green").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "limit.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset, image=img_file, uploaded_by=user
        )

        # Create images that appear already processed today
        for i in range(5):
            buf2 = BytesIO()
            PILImage.new("RGB", (10, 10), "red").save(buf2, "JPEG")
            buf2.seek(0)
            f = SimpleUploadedFile(
                f"old{i}.jpg", buf2.getvalue(), content_type="image/jpeg"
            )
            AssetImage.objects.create(
                asset=asset,
                image=f,
                uploaded_by=user,
                ai_processing_status="completed",
                ai_processed_at=timezone.now(),
            )

        with override_settings(
            AI_ANALYSIS_DAILY_LIMIT=5,
            ANTHROPIC_API_KEY="test-key",
        ):
            from assets.tasks import analyse_image

            analyse_image(image.pk)

        image.refresh_from_db()
        assert image.ai_processing_status == "skipped"
        assert "limit" in image.ai_error_message.lower()
        mock_api.assert_not_called()


class TestAIImageResize:
    """Test AI image resizing (Batch C)."""

    def test_resize_large_image(self):
        from io import BytesIO

        from PIL import Image as PILImage

        from assets.services.ai import resize_image_for_ai

        # Create a 4000x4000 image (16MP) which exceeds 3MP default
        buf = BytesIO()
        PILImage.new("RGB", (4000, 4000), "red").save(buf, "JPEG")
        buf.seek(0)

        result_bytes, media_type = resize_image_for_ai(buf.getvalue())
        assert media_type == "image/jpeg"

        # Verify the result image is smaller
        result_img = PILImage.open(BytesIO(result_bytes))
        w, h = result_img.size
        assert w * h <= 3000000 + 10000  # Allow small rounding tolerance

    def test_small_image_unchanged_dimensions(self):
        from io import BytesIO

        from PIL import Image as PILImage

        from assets.services.ai import resize_image_for_ai

        # Create a small 100x100 image
        buf = BytesIO()
        PILImage.new("RGB", (100, 100), "blue").save(buf, "JPEG")
        buf.seek(0)

        result_bytes, media_type = resize_image_for_ai(buf.getvalue())
        result_img = PILImage.open(BytesIO(result_bytes))
        assert result_img.size == (100, 100)


class TestAIStatusView:
    """Test AI status polling view (Batch C)."""

    def test_processing_returns_html(self, admin_client, asset, admin_user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "stat.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset,
            image=img_file,
            uploaded_by=admin_user,
            ai_processing_status="processing",
        )

        response = admin_client.get(
            reverse("assets:ai_status", args=[asset.pk, image.pk])
        )
        assert response.status_code == 200
        assert b"AI analysis in progress" in response.content

    def test_completed_redirects(self, admin_client, asset, admin_user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "done.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset,
            image=img_file,
            uploaded_by=admin_user,
            ai_processing_status="completed",
        )

        response = admin_client.get(
            reverse("assets:ai_status", args=[asset.pk, image.pk])
        )
        assert response.status_code == 302


class TestAIRetryView:
    """Test AI re-analyse view (Batch C)."""

    @patch(
        "props.context_processors.is_ai_analysis_enabled", return_value=True
    )
    @patch("assets.tasks.reanalyse_image.delay")
    def test_reanalyse_triggers_task(
        self, mock_delay, mock_enabled, admin_client, asset, admin_user
    ):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "retry.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset,
            image=img_file,
            uploaded_by=admin_user,
            ai_processing_status="failed",
        )

        response = admin_client.get(
            reverse(
                "assets:ai_reanalyse",
                args=[asset.pk, image.pk],
            )
        )
        assert response.status_code == 302
        mock_delay.assert_called_once_with(image.pk)


class TestHomeLocation:
    """Test home_location is set on checkout (Batch F)."""

    def test_checkout_sets_home_location(
        self, admin_client, asset, second_user
    ):
        original_location = asset.current_location
        assert asset.home_location is None

        admin_client.post(
            reverse("assets:asset_checkout", args=[asset.pk]),
            {"borrower": second_user.pk, "notes": "Home loc test"},
        )
        asset.refresh_from_db()
        assert asset.home_location == original_location

    def test_home_location_not_overwritten(
        self, admin_client, asset, second_user, location
    ):
        # Manually set a home_location
        other_loc = Location.objects.create(name="Original Home")
        asset.home_location = other_loc
        asset.save()

        admin_client.post(
            reverse("assets:asset_checkout", args=[asset.pk]),
            {"borrower": second_user.pk, "notes": "Keep home"},
        )
        asset.refresh_from_db()
        # Home location should remain the original, not be overwritten
        assert asset.home_location == other_loc


class TestThumbnailGeneration:
    """Test thumbnail creation on AssetImage save (Batch F)."""

    def test_thumbnail_created_on_save(self, asset, user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (600, 600), "green").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "thumb_test.jpg",
            buf.getvalue(),
            content_type="image/jpeg",
        )

        image = AssetImage.objects.create(
            asset=asset,
            image=img_file,
            uploaded_by=user,
        )
        image.refresh_from_db()
        assert image.thumbnail
        assert image.thumbnail.name

        # Verify thumbnail is smaller
        thumb_img = PILImage.open(image.thumbnail)
        assert thumb_img.size[0] <= 300
        assert thumb_img.size[1] <= 300


class TestTagFormCaseInsensitive:
    """Test tag form rejects case-insensitive duplicates (Batch A)."""

    def test_duplicate_name_case_insensitive(self, tag):
        from assets.forms import TagForm

        form = TagForm(data={"name": "Fragile", "color": "blue"})
        assert not form.is_valid()
        assert "name" in form.errors

    def test_same_name_different_case_blocked(self, tag):
        from assets.forms import TagForm

        form = TagForm(data={"name": "FRAGILE", "color": "green"})
        assert not form.is_valid()

    def test_exact_same_name_blocked(self, tag):
        from assets.forms import TagForm

        form = TagForm(data={"name": "fragile", "color": "red"})
        assert not form.is_valid()

    def test_new_unique_name_allowed(self, tag):
        from assets.forms import TagForm

        form = TagForm(data={"name": "brand-new-tag", "color": "blue"})
        assert form.is_valid()


class TestDraftsQueueOrdering:
    """Test drafts queue ordering (Batch A)."""

    def test_drafts_ordered_newest_first(self, client_logged_in, user):
        a1 = Asset(name="Draft A", status="draft", created_by=user)
        a1.save()
        a2 = Asset(name="Draft B", status="draft", created_by=user)
        a2.save()

        response = client_logged_in.get(reverse("assets:drafts_queue"))
        assert response.status_code == 200
        drafts = list(response.context["page_obj"])
        # Most recently created should come first
        assert drafts[0].name == "Draft B"


class TestAssetDetailViewerPermissions:
    """Test that asset detail shows correct permission flags for viewer."""

    def test_viewer_sees_restricted_permissions(self, viewer_client, asset):
        response = viewer_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert response.status_code == 200
        assert response.context["can_edit"] is False
        assert response.context["can_delete"] is False
        assert response.context["can_checkout"] is False


class TestBulkActionsEdgeCases:
    """Additional bulk action edge cases."""

    def test_bulk_unknown_action(self, admin_client, asset):
        response = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "asset_ids": [asset.pk],
                "bulk_action": "unknown_action",
            },
        )
        assert response.status_code == 302

    def test_bulk_transfer_no_location(self, admin_client, asset):
        response = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "asset_ids": [asset.pk],
                "bulk_action": "transfer",
            },
        )
        assert response.status_code == 302

    def test_bulk_status_change_no_status(self, admin_client, asset):
        response = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "asset_ids": [asset.pk],
                "bulk_action": "status_change",
            },
        )
        assert response.status_code == 302


class TestCheckinHomeLocation:
    """Test checkin shows home_location context (Batch F)."""

    def test_checkin_context_has_home_location(
        self, admin_client, asset, second_user
    ):
        home = Location.objects.create(name="Home Base")
        asset.home_location = home
        asset.checked_out_to = second_user
        asset.save()

        response = admin_client.get(
            reverse("assets:asset_checkin", args=[asset.pk])
        )
        assert response.status_code == 200
        assert response.context["home_location"] == home


# ============================================================
# SESSION 16 TESTS
# ============================================================


class TestAINameSuggestion:
    """Test ai_name_suggestion field on AssetImage."""

    def test_field_defaults_empty(self, asset, user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "name_test.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset, image=img_file, uploaded_by=user
        )
        assert image.ai_name_suggestion == ""

    def test_name_suggestion_saved(self, asset, user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "name_sug.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset,
            image=img_file,
            uploaded_by=user,
            ai_name_suggestion="Brass Desk Lamp",
        )
        image.refresh_from_db()
        assert image.ai_name_suggestion == "Brass Desk Lamp"

    @patch("assets.services.ai.analyse_image_data")
    def test_analyse_task_saves_name_suggestion(
        self, mock_api, db, asset, user
    ):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        mock_api.return_value = {
            "description": "A brass lamp",
            "category_suggestion": "Lighting",
            "condition": "good",
            "tags": ["brass"],
            "ocr_text": "",
            "name_suggestion": "Vintage Brass Lamp",
        }

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "ai_name.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset, image=img_file, uploaded_by=user
        )

        with override_settings(ANTHROPIC_API_KEY="test-key"):
            from assets.tasks import analyse_image

            analyse_image(image.pk)

        image.refresh_from_db()
        assert image.ai_name_suggestion == "Vintage Brass Lamp"


class TestHandoverService:
    """Test custody handover creates two transactions atomically."""

    def test_handover_creates_two_transactions(self, asset, user, second_user):
        from assets.services.transactions import create_handover

        asset.checked_out_to = user
        asset.save()

        third_user = User.objects.create_user(
            username="newborrower",
            email="new@example.com",
            password="testpass123!",
        )

        checkin_txn, checkout_txn = create_handover(
            asset, third_user, second_user, notes="Test handover"
        )

        assert checkin_txn.action == "checkin"
        assert checkout_txn.action == "checkout"
        assert checkout_txn.borrower == third_user
        asset.refresh_from_db()
        assert asset.checked_out_to == third_user

    def test_handover_with_location(self, asset, user, second_user):
        from assets.services.transactions import create_handover

        asset.checked_out_to = user
        asset.save()
        new_loc = Location.objects.create(name="Handover Spot")

        create_handover(asset, second_user, user, to_location=new_loc)

        asset.refresh_from_db()
        assert asset.checked_out_to == second_user
        assert asset.current_location == new_loc

    def test_handover_transaction_count(self, asset, user, second_user):
        from assets.services.transactions import create_handover

        asset.checked_out_to = user
        asset.save()
        before_count = Transaction.objects.count()

        create_handover(asset, second_user, user)

        assert Transaction.objects.count() == before_count + 2


class TestBackdating:
    """Test backdating sets is_backdated and preserves created_at."""

    def test_backdated_checkout(self, asset, second_user, user):
        from datetime import timedelta

        from django.utils import timezone

        from assets.services.transactions import create_checkout

        past = timezone.now() - timedelta(days=7)
        txn = create_checkout(
            asset, second_user, user, notes="Backdated", timestamp=past
        )
        assert txn.is_backdated is True
        assert txn.timestamp == past
        # created_at should be roughly now, not the backdated time
        assert txn.created_at is not None

    def test_non_backdated_is_false(self, asset, second_user, user):
        from assets.services.transactions import create_checkout

        txn = create_checkout(asset, second_user, user, notes="Normal")
        assert txn.is_backdated is False

    def test_backdated_checkin(self, asset, second_user, user):
        from datetime import timedelta

        from django.utils import timezone

        from assets.services.transactions import create_checkin

        asset.checked_out_to = second_user
        asset.save()
        past = timezone.now() - timedelta(days=3)
        loc = Location.objects.create(name="Backdate Loc")
        txn = create_checkin(asset, loc, user, timestamp=past)
        assert txn.is_backdated is True
        assert txn.timestamp == past

    def test_backdated_transfer(self, asset, user):
        from datetime import timedelta

        from django.utils import timezone

        from assets.services.transactions import create_transfer

        past = timezone.now() - timedelta(days=5)
        loc = Location.objects.create(name="Back Transfer")
        txn = create_transfer(asset, loc, user, timestamp=past)
        assert txn.is_backdated is True

    def test_backdated_handover(self, asset, user, second_user):
        from datetime import timedelta

        from django.utils import timezone

        from assets.services.transactions import create_handover

        asset.checked_out_to = user
        asset.save()
        past = timezone.now() - timedelta(days=2)
        checkin_txn, checkout_txn = create_handover(
            asset, second_user, user, timestamp=past
        )
        assert checkin_txn.is_backdated is True
        assert checkout_txn.is_backdated is True
        assert checkin_txn.timestamp == past


class TestBorrowerRole:
    """Test Borrower group and role detection."""

    def test_get_user_role_returns_borrower(self, db, password):
        from django.contrib.auth.models import Group

        from assets.services.permissions import get_user_role

        group, _ = Group.objects.get_or_create(name="Borrower")
        borrower_user = User.objects.create_user(
            username="ext_borrower",
            email="ext@example.com",
            password=password,
        )
        borrower_user.groups.add(group)
        assert get_user_role(borrower_user) == "borrower"

    def test_borrower_cannot_login(self, client, db, password):
        from django.contrib.auth.models import Group
        from django.core.cache import cache

        # Clear ratelimit cache so earlier login tests don't block us
        cache.clear()

        group, _ = Group.objects.get_or_create(name="Borrower")
        borrower_user = User.objects.create_user(
            username="nologin_borrower",
            email="nologin@example.com",
            password=password,
        )
        borrower_user.groups.add(group)

        response = client.post(
            reverse("accounts:login"),
            {"username": "nologin_borrower", "password": password},
        )
        # Should not redirect to dashboard; should show borrower_no_access
        assert response.status_code == 200
        assert b"borrower" in response.content.lower()


class TestCanHandoverPermission:
    """Test can_handover_asset permission check."""

    def test_admin_can_handover(self, admin_user, asset):
        from assets.services.permissions import can_handover_asset

        assert can_handover_asset(admin_user, asset) is True

    def test_viewer_cannot_handover(self, viewer_user, asset):
        from assets.services.permissions import can_handover_asset

        assert can_handover_asset(viewer_user, asset) is False

    def test_member_cannot_handover(self, member_user, asset):
        from assets.services.permissions import can_handover_asset

        assert can_handover_asset(member_user, asset) is False

    def test_department_manager_can_handover(self, user, asset, department):
        from assets.services.permissions import can_handover_asset

        department.managers.add(user)
        assert can_handover_asset(user, asset) is True


class TestBulkCheckout:
    """Test bulk checkout service."""

    def test_bulk_checkout_single(self, asset, second_user, user):
        from assets.services.bulk import bulk_checkout

        result = bulk_checkout(
            [asset.pk], second_user.pk, user, notes="Bulk test"
        )
        assert result["checked_out"] == 1
        assert result["skipped"] == []
        asset.refresh_from_db()
        assert asset.checked_out_to == second_user

    def test_bulk_checkout_skips_already_checked_out(
        self, asset, second_user, user
    ):
        from assets.services.bulk import bulk_checkout

        asset.checked_out_to = second_user
        asset.save()

        third_user = User.objects.create_user(
            username="bulk_target",
            email="bulk@example.com",
            password="testpass123!",
        )
        result = bulk_checkout([asset.pk], third_user.pk, user)
        assert result["checked_out"] == 0
        assert asset.name in result["skipped"]

    def test_bulk_checkout_sets_home_location(self, asset, second_user, user):
        from assets.services.bulk import bulk_checkout

        assert asset.home_location is None
        bulk_checkout([asset.pk], second_user.pk, user)
        asset.refresh_from_db()
        assert asset.home_location is not None


class TestBulkCheckin:
    """Test bulk checkin service."""

    def test_bulk_checkin_single(self, asset, second_user, user, location):
        from assets.services.bulk import bulk_checkin

        asset.checked_out_to = second_user
        asset.save()

        new_loc = Location.objects.create(name="Bulk Return")
        result = bulk_checkin([asset.pk], new_loc.pk, user)
        assert result["checked_in"] == 1
        assert result["skipped"] == []
        asset.refresh_from_db()
        assert asset.checked_out_to is None
        assert asset.current_location == new_loc

    def test_bulk_checkin_skips_not_checked_out(self, asset, user, location):
        from assets.services.bulk import bulk_checkin

        new_loc = Location.objects.create(name="Bulk Return 2")
        result = bulk_checkin([asset.pk], new_loc.pk, user)
        assert result["checked_in"] == 0
        assert asset.name in result["skipped"]


class TestHandoverView:
    """Test the handover view."""

    def test_handover_renders(self, admin_client, asset, second_user):
        asset.checked_out_to = second_user
        asset.save()
        response = admin_client.get(
            reverse("assets:asset_handover", args=[asset.pk])
        )
        assert response.status_code == 200

    def test_handover_redirects_if_not_checked_out(self, admin_client, asset):
        response = admin_client.get(
            reverse("assets:asset_handover", args=[asset.pk])
        )
        assert response.status_code == 302

    def test_handover_post(self, admin_client, asset, second_user):
        third_user = User.objects.create_user(
            username="handover_target",
            email="handover@example.com",
            password="testpass123!",
        )
        asset.checked_out_to = second_user
        asset.save()

        response = admin_client.post(
            reverse("assets:asset_handover", args=[asset.pk]),
            {"borrower": third_user.pk, "notes": "Handover test"},
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.checked_out_to == third_user

    def test_viewer_cannot_handover(self, viewer_client, asset, second_user):
        asset.checked_out_to = second_user
        asset.save()
        response = viewer_client.get(
            reverse("assets:asset_handover", args=[asset.pk])
        )
        assert response.status_code == 403


class TestBackdatingViews:
    """Test that checkout/checkin/transfer views accept optional date."""

    def test_checkout_with_backdate(self, admin_client, asset, second_user):
        response = admin_client.post(
            reverse("assets:asset_checkout", args=[asset.pk]),
            {
                "borrower": second_user.pk,
                "notes": "Backdated checkout",
                "action_date": "2026-01-15T10:00",
            },
        )
        assert response.status_code == 302
        txn = Transaction.objects.filter(
            asset=asset, action="checkout"
        ).first()
        assert txn.is_backdated is True

    def test_checkin_with_backdate(
        self, admin_client, asset, second_user, location
    ):
        asset.checked_out_to = second_user
        asset.save()
        new_loc = Location.objects.create(name="Backdate CI Loc")
        response = admin_client.post(
            reverse("assets:asset_checkin", args=[asset.pk]),
            {
                "location": new_loc.pk,
                "action_date": "2026-01-20T14:30",
            },
        )
        assert response.status_code == 302
        txn = Transaction.objects.filter(asset=asset, action="checkin").first()
        assert txn.is_backdated is True

    def test_transfer_with_backdate(self, admin_client, asset):
        new_loc = Location.objects.create(name="Backdate TR Loc")
        response = admin_client.post(
            reverse("assets:asset_transfer", args=[asset.pk]),
            {
                "location": new_loc.pk,
                "action_date": "2026-01-10T09:00",
            },
        )
        assert response.status_code == 302
        txn = Transaction.objects.filter(
            asset=asset, action="transfer"
        ).first()
        assert txn.is_backdated is True


class TestBulkCheckoutCheckinViews:
    """Test bulk checkout/checkin via the bulk_actions view."""

    def test_bulk_checkout_view(self, admin_client, asset, second_user):
        response = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "asset_ids": [asset.pk],
                "bulk_action": "bulk_checkout",
                "bulk_borrower": second_user.pk,
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.checked_out_to == second_user

    def test_bulk_checkin_view(
        self, admin_client, asset, second_user, location
    ):
        asset.checked_out_to = second_user
        asset.save()
        new_loc = Location.objects.create(name="Bulk CI Dest")
        response = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "asset_ids": [asset.pk],
                "bulk_action": "bulk_checkin",
                "bulk_checkin_location": new_loc.pk,
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.checked_out_to is None
        assert asset.current_location == new_loc


# ============================================================
# LOST/STOLEN STATUS TESTS (D1, D2)
# ============================================================


class TestLostStolenStatuses:
    """Tests for lost and stolen asset statuses."""

    def test_lost_status_exists(self, db):
        choices = dict(Asset.STATUS_CHOICES)
        assert "lost" in choices
        assert "stolen" in choices

    def test_transition_active_to_lost(self, asset):
        assert asset.can_transition_to("lost")
        asset.status = "lost"
        asset.save(update_fields=["status"])
        asset.refresh_from_db()
        assert asset.status == "lost"

    def test_transition_active_to_stolen(self, asset):
        assert asset.can_transition_to("stolen")
        asset.status = "stolen"
        asset.save(update_fields=["status"])
        asset.refresh_from_db()
        assert asset.status == "stolen"

    def test_transition_lost_to_active(self, asset):
        asset.status = "lost"
        asset.save(update_fields=["status"])
        assert asset.can_transition_to("active")

    def test_transition_stolen_to_active(self, asset):
        asset.status = "stolen"
        asset.save(update_fields=["status"])
        assert asset.can_transition_to("active")

    def test_transition_lost_to_disposed(self, asset):
        asset.status = "lost"
        asset.save(update_fields=["status"])
        assert asset.can_transition_to("disposed")

    def test_cannot_transition_draft_to_lost(self, draft_asset):
        assert not draft_asset.can_transition_to("lost")

    def test_transition_missing_to_lost(self, asset):
        asset.status = "missing"
        asset.save(update_fields=["status"])
        assert asset.can_transition_to("lost")

    def test_lost_stolen_notes_field(self, asset):
        asset.lost_stolen_notes = "Last seen in storage room B"
        asset.save(update_fields=["lost_stolen_notes"])
        asset.refresh_from_db()
        assert asset.lost_stolen_notes == "Last seen in storage room B"

    def test_state_service_validates_lost_transition(self, asset):
        from assets.services.state import validate_transition

        validate_transition(asset, "lost")  # Should not raise

    def test_state_service_allows_checked_out_to_lost(self, asset, admin_user):
        """V1: lost/stolen MUST be allowed on checked-out assets."""
        from assets.services.state import validate_transition

        asset.checked_out_to = admin_user
        asset.save(update_fields=["checked_out_to"])
        validate_transition(asset, "lost")  # Should not raise

    def test_state_service_allows_checked_out_to_stolen(
        self, asset, admin_user
    ):
        """V1: lost/stolen MUST be allowed on checked-out assets."""
        from assets.services.state import validate_transition

        asset.checked_out_to = admin_user
        asset.save(update_fields=["checked_out_to"])
        validate_transition(asset, "stolen")  # Should not raise

    def test_state_service_still_blocks_checked_out_to_retired(
        self, asset, admin_user
    ):
        """V1: retired/disposed still blocked on checked-out."""
        from assets.services.state import validate_transition

        asset.checked_out_to = admin_user
        asset.save(update_fields=["checked_out_to"])
        with pytest.raises(ValidationError, match="Check it in"):
            validate_transition(asset, "retired")


# ============================================================
# DUE DATE TESTS (D4)
# ============================================================


class TestDueDate:
    """Tests for Transaction due_date field."""

    def test_transaction_due_date_nullable(self, asset, user, location):
        tx = Transaction(
            asset=asset,
            user=user,
            action="checkout",
            from_location=location,
        )
        tx.save()
        assert tx.due_date is None

    def test_transaction_due_date_set(self, asset, user, location):
        from datetime import date

        tx = Transaction(
            asset=asset,
            user=user,
            action="checkout",
            from_location=location,
            due_date=date(2026, 3, 15),
        )
        tx.save()
        tx.refresh_from_db()
        assert tx.due_date == date(2026, 3, 15)


# ============================================================
# RELOCATE TRANSACTION TESTS (C3, D5)
# ============================================================


class TestRelocateTransaction:
    """Tests for relocate transaction type."""

    def test_relocate_action_choice_exists(self, db):
        choices = dict(Transaction.ACTION_CHOICES)
        assert "relocate" in choices

    def test_create_relocate_transaction(self, asset, user, location):
        second_loc = Location.objects.create(
            name="New Home", address="456 Theatre Lane"
        )
        tx = Transaction(
            asset=asset,
            user=user,
            action="relocate",
            from_location=location,
            to_location=second_loc,
            notes="Moving permanent home",
        )
        tx.save()
        assert tx.action == "relocate"

    def test_relocate_view_get(self, admin_client, asset):
        response = admin_client.get(
            reverse("assets:asset_relocate", args=[asset.pk])
        )
        assert response.status_code == 200

    def test_relocate_view_post(self, admin_client, asset):
        new_loc = Location.objects.create(name="New Home")
        response = admin_client.post(
            reverse("assets:asset_relocate", args=[asset.pk]),
            {"location": new_loc.pk, "notes": "Permanent move"},
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.home_location == new_loc
        tx = Transaction.objects.filter(asset=asset, action="relocate").first()
        assert tx is not None
        assert tx.to_location == new_loc

    def test_relocate_viewer_denied(self, viewer_client, asset):
        response = viewer_client.get(
            reverse("assets:asset_relocate", args=[asset.pk])
        )
        assert response.status_code == 403

    def test_relocate_non_active_denied(self, admin_client, draft_asset):
        response = admin_client.get(
            reverse("assets:asset_relocate", args=[draft_asset.pk])
        )
        assert response.status_code == 302


# ============================================================
# ASSET LIST SORTING TESTS (C2)
# ============================================================


class TestAssetListSorting:
    """Tests for user-selectable sorting on asset list."""

    def test_sort_by_name_asc(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?sort=name"
        )
        assert response.status_code == 200
        assert response.context["current_sort"] == "name"

    def test_sort_by_name_desc(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?sort=-name"
        )
        assert response.status_code == 200
        assert response.context["current_sort"] == "-name"

    def test_sort_by_status(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?sort=status"
        )
        assert response.status_code == 200

    def test_sort_by_updated(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?sort=-updated"
        )
        assert response.status_code == 200

    def test_sort_by_category(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?sort=category"
        )
        assert response.status_code == 200

    def test_invalid_sort_defaults(self, client_logged_in, asset):
        response = client_logged_in.get(
            reverse("assets:asset_list") + "?sort=invalid"
        )
        assert response.status_code == 200
        assert response.context["current_sort"] == "invalid"

    def test_default_sort(self, client_logged_in, asset):
        response = client_logged_in.get(reverse("assets:asset_list"))
        assert response.status_code == 200
        assert response.context["current_sort"] == "-updated"


# ============================================================
# BARCODE PRE-GENERATION TESTS (C4)
# ============================================================


class TestBarcodePregeneration:
    """Tests for barcode pre-generation view."""

    def test_pregenerate_get(self, admin_client, db):
        response = admin_client.get(reverse("assets:barcode_pregenerate"))
        assert response.status_code == 200

    def test_pregenerate_creates_drafts(self, admin_client, db):
        before_count = Asset.objects.count()
        response = admin_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 5},
        )
        assert response.status_code == 200
        assert Asset.objects.count() == before_count + 5
        new_assets = Asset.objects.filter(name__startswith="Pre-generated")
        assert new_assets.count() == 5
        assert all(a.status == "draft" for a in new_assets)

    def test_pregenerate_max_100(self, admin_client, db):
        response = admin_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 200},
        )
        assert response.status_code == 200
        assert (
            Asset.objects.filter(name__startswith="Pre-generated").count()
            == 100
        )

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
# QUERY COUNT REGRESSION TESTS
# ============================================================


class TestQueryCounts:
    """Lock in query counts for key views to prevent N+1 regressions.

    Uses django_assert_num_queries to verify that views execute a fixed
    number of SQL queries regardless of data volume.
    """

    def test_dashboard_query_count(
        self,
        django_assert_num_queries,
        client_logged_in,
        asset,
        department,
        category,
        location,
        tag,
    ):
        """Dashboard should use a fixed number of queries."""
        asset.tags.add(tag)
        with django_assert_num_queries(20):
            response = client_logged_in.get(reverse("assets:dashboard"))
        assert response.status_code == 200

    def test_asset_list_query_count(
        self,
        django_assert_num_queries,
        client_logged_in,
        asset,
    ):
        """Asset list should use a fixed number of queries."""
        with django_assert_num_queries(16):
            response = client_logged_in.get(reverse("assets:asset_list"))
        assert response.status_code == 200

    def test_asset_detail_query_count(
        self,
        django_assert_num_queries,
        client_logged_in,
        asset,
    ):
        """Asset detail should use a fixed number of queries."""
        with django_assert_num_queries(28):
            response = client_logged_in.get(
                reverse("assets:asset_detail", args=[asset.pk])
            )
        assert response.status_code == 200


# ============================================================
# ADMIN TESTS
# ============================================================


class TestAssetAdmin:
    """Test AssetAdmin custom display methods."""

    def test_ai_analysis_summary_no_images(self, admin_user, asset):
        from assets.admin import AssetAdmin

        admin_instance = AssetAdmin(Asset, None)
        result = admin_instance.ai_analysis_summary(asset)
        assert result == "-"

    def test_ai_analysis_summary_with_images(self, admin_user, asset, user):
        from assets.admin import AssetAdmin

        # Create images with different statuses
        AssetImage.objects.create(
            asset=asset,
            image="test1.jpg",
            ai_processing_status="completed",
            uploaded_by=user,
        )
        AssetImage.objects.create(
            asset=asset,
            image="test2.jpg",
            ai_processing_status="pending",
            uploaded_by=user,
        )
        AssetImage.objects.create(
            asset=asset,
            image="test3.jpg",
            ai_processing_status="completed",
            uploaded_by=user,
        )

        admin_instance = AssetAdmin(Asset, None)
        result = admin_instance.ai_analysis_summary(asset)
        assert result == "2/3 analysed"


class TestAssetImageAdmin:
    """Test AssetImageAdmin changelist with AI stats."""

    def test_changelist_includes_ai_stats(self, admin_client, asset, user):
        # Create images with AI data
        AssetImage.objects.create(
            asset=asset,
            image="test1.jpg",
            ai_processing_status="completed",
            ai_prompt_tokens=100,
            ai_completion_tokens=50,
            uploaded_by=user,
        )
        AssetImage.objects.create(
            asset=asset,
            image="test2.jpg",
            ai_processing_status="failed",
            ai_prompt_tokens=0,
            ai_completion_tokens=0,
            uploaded_by=user,
        )
        AssetImage.objects.create(
            asset=asset,
            image="test3.jpg",
            ai_processing_status="pending",
            ai_prompt_tokens=0,
            ai_completion_tokens=0,
            uploaded_by=user,
        )

        response = admin_client.get("/admin/assets/assetimage/")
        assert response.status_code == 200
        assert "ai_stats" in response.context
        stats = response.context["ai_stats"]
        assert stats["total_images"] == 3
        assert stats["analysed"] == 1
        assert stats["failed"] == 1
        assert stats["total_prompt_tokens"] == 100
        assert stats["total_completion_tokens"] == 50


# ============================================================
# ASSET KITS & SERIALISATION TESTS (F2)
# ============================================================


class TestAssetNewFields:
    """Test is_serialised and is_kit defaults on Asset."""

    def test_is_serialised_default_true(self, category, location, user):
        """V3: new assets default to is_serialised=True."""
        a = Asset(
            name="Default Check",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        a.save()
        assert a.is_serialised is True

    def test_is_kit_default_false(self, asset):
        assert asset.is_kit is False

    def test_serialised_asset_flag(self, serialised_asset):
        assert serialised_asset.is_serialised is True

    def test_kit_asset_flag(self, kit_asset):
        assert kit_asset.is_kit is True


class TestTransactionNewFields:
    """Test new Transaction fields."""

    def test_quantity_default(self, asset, user):
        txn = Transaction.objects.create(
            asset=asset, user=user, action="checkout"
        )
        assert txn.quantity == 1

    def test_serial_fk_nullable(self, asset, user):
        txn = Transaction.objects.create(
            asset=asset, user=user, action="checkout"
        )
        assert txn.serial is None

    def test_serial_barcode_nullable(self, asset, user):
        txn = Transaction.objects.create(
            asset=asset, user=user, action="checkout"
        )
        assert txn.serial_barcode is None

    def test_kit_return_action(self, asset, user):
        txn = Transaction.objects.create(
            asset=asset, user=user, action="kit_return"
        )
        assert txn.get_action_display() == "Kit Return"

    def test_transaction_with_serial(
        self, serialised_asset, asset_serial, user
    ):
        txn = Transaction.objects.create(
            asset=serialised_asset,
            user=user,
            action="checkout",
            serial=asset_serial,
            serial_barcode=asset_serial.barcode,
        )
        assert txn.serial == asset_serial
        assert txn.serial_barcode == asset_serial.barcode


class TestAssetSerialModel:
    """Test AssetSerial model."""

    def test_creation(self, serialised_asset, location):
        serial = AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="002",
            barcode="TEST-SERIAL-002",
            current_location=location,
        )
        assert serial.status == "active"
        assert serial.condition == "good"
        assert serial.is_archived is False

    def test_str(self, asset_serial, serialised_asset):
        expected = f"{serialised_asset.name} #001"
        assert str(asset_serial) == expected

    def test_unique_serial_per_asset(self, serialised_asset, asset_serial):
        with pytest.raises(Exception):
            AssetSerial.objects.create(
                asset=serialised_asset,
                serial_number="001",
                barcode="TEST-DIFFERENT",
            )

    def test_unique_barcode(self, serialised_asset, asset_serial):
        with pytest.raises(Exception):
            AssetSerial.objects.create(
                asset=serialised_asset,
                serial_number="099",
                barcode=asset_serial.barcode,
            )

    def test_cross_table_barcode_validation(self, serialised_asset, asset):
        serial = AssetSerial(
            asset=serialised_asset,
            serial_number="099",
            barcode=asset.barcode,
        )
        with pytest.raises(ValidationError, match="already in use"):
            serial.clean()

    def test_clean_non_serialised_parent(self, asset, location):
        serial = AssetSerial(
            asset=asset,
            serial_number="001",
            barcode="TEST-BAD-SERIAL",
        )
        with pytest.raises(ValidationError, match="non-serialised"):
            serial.clean()

    def test_draft_status_rejected(self, serialised_asset):
        serial = AssetSerial(
            asset=serialised_asset,
            serial_number="099",
            status="draft",
        )
        with pytest.raises(ValidationError, match="draft"):
            serial.clean()

    def test_status_choices(self, db):
        choices = dict(AssetSerial.STATUS_CHOICES)
        assert "active" in choices
        assert "retired" in choices
        assert "missing" in choices
        assert "lost" in choices
        assert "stolen" in choices
        assert "disposed" in choices
        assert "draft" not in choices


class TestAssetKitModel:
    """Test AssetKit model."""

    def test_creation(self, kit_component, kit_asset, asset):
        assert kit_component.kit == kit_asset
        assert kit_component.component == asset
        assert kit_component.quantity == 1
        assert kit_component.is_required is True

    def test_str(self, kit_component, kit_asset, asset):
        expected = f"{kit_asset.name} -> {asset.name}"
        assert str(kit_component) == expected

    def test_unique_kit_component(self, kit_component, kit_asset, asset):
        with pytest.raises(Exception):
            AssetKit.objects.create(
                kit=kit_asset,
                component=asset,
            )

    def test_clean_kit_must_be_kit(self, asset, category, location, user):
        non_kit = Asset(
            name="Not A Kit",
            category=category,
            current_location=location,
            status="active",
            is_kit=False,
            created_by=user,
        )
        non_kit.save()
        ak = AssetKit(kit=non_kit, component=asset)
        with pytest.raises(ValidationError, match="is_kit"):
            ak.clean()

    def test_no_self_reference(self, kit_asset):
        ak = AssetKit(kit=kit_asset, component=kit_asset)
        with pytest.raises(ValidationError, match="itself"):
            ak.clean()

    def test_circular_reference(self, category, location, user):
        kit_a = Asset(
            name="Kit A",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            created_by=user,
        )
        kit_a.save()
        kit_b = Asset(
            name="Kit B",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            created_by=user,
        )
        kit_b.save()

        # A contains B
        AssetKit.objects.create(kit=kit_a, component=kit_b)
        # B contains A -> circular
        ak = AssetKit(kit=kit_b, component=kit_a)
        with pytest.raises(ValidationError, match="Circular"):
            ak.clean()

    def test_serial_must_belong_to_component(
        self, kit_asset, serialised_asset, asset_serial, asset
    ):
        # asset_serial belongs to serialised_asset, not to asset
        ak = AssetKit(
            kit=kit_asset,
            component=asset,
            serial=asset_serial,
        )
        with pytest.raises(ValidationError, match="component"):
            ak.clean()


class TestDerivedFields:
    """Test derived properties on Asset for serialised assets."""

    def test_effective_quantity_serialised_no_serials(self, serialised_asset):
        assert serialised_asset.effective_quantity == 0

    def test_effective_quantity_serialised_with_serials(
        self, serialised_asset, location
    ):
        for i in range(3):
            AssetSerial.objects.create(
                asset=serialised_asset,
                serial_number=f"EQ-{i}",
                barcode=f"EQ-SERIAL-{i}",
                current_location=location,
            )
        assert serialised_asset.effective_quantity == 3

    def test_effective_quantity_excludes_disposed(
        self, serialised_asset, location
    ):
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="EQ-A",
            barcode="EQ-A-BC",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="EQ-B",
            barcode="EQ-B-BC",
            status="disposed",
            current_location=location,
        )
        assert serialised_asset.effective_quantity == 1

    def test_effective_quantity_non_serialised(self, non_serialised_asset):
        assert non_serialised_asset.effective_quantity == 10

    def test_derived_status_non_serialised(self, asset):
        assert asset.derived_status == "active"

    def test_derived_status_serialised_active(
        self, serialised_asset, location
    ):
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="DS-1",
            barcode="DS-1-BC",
            status="active",
            current_location=location,
        )
        assert serialised_asset.derived_status == "active"

    def test_derived_status_serialised_missing_priority(
        self, serialised_asset, location
    ):
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="DS-2",
            barcode="DS-2-BC",
            status="retired",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="DS-3",
            barcode="DS-3-BC",
            status="missing",
            current_location=location,
        )
        # Missing should take priority over retired
        assert serialised_asset.derived_status == "missing"

    def test_derived_status_no_serials_falls_back(self, serialised_asset):
        assert serialised_asset.derived_status == "active"

    def test_condition_summary_non_serialised(self, asset):
        assert asset.condition_summary == "good"

    def test_condition_summary_serialised(self, serialised_asset, location):
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="CS-1",
            barcode="CS-1-BC",
            condition="good",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="CS-2",
            barcode="CS-2-BC",
            condition="good",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="CS-3",
            barcode="CS-3-BC",
            condition="fair",
            current_location=location,
        )
        summary = serialised_asset.condition_summary
        assert summary["good"] == 2
        assert summary["fair"] == 1

    def test_available_count_serialised(
        self, serialised_asset, location, second_user
    ):
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="AC-1",
            barcode="AC-1-BC",
            status="active",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="AC-2",
            barcode="AC-2-BC",
            status="active",
            checked_out_to=second_user,
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="AC-3",
            barcode="AC-3-BC",
            status="retired",
            current_location=location,
        )
        assert serialised_asset.available_count == 1

    def test_is_checked_out_serialised(
        self, serialised_asset, location, second_user
    ):
        assert not serialised_asset.is_checked_out
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="CO-1",
            barcode="CO-1-BC",
            status="active",
            checked_out_to=second_user,
            current_location=location,
        )
        assert serialised_asset.is_checked_out

    def test_is_checked_out_non_serialised(self, asset, second_user):
        assert not asset.is_checked_out
        asset.checked_out_to = second_user
        assert asset.is_checked_out


class TestSerialBarcodeService:
    """Test serial barcode generation and cross-table validation."""

    def test_pattern_generation(self):
        from assets.services.barcode import (
            generate_serial_barcode_string,
        )

        result = generate_serial_barcode_string("ASSET-ABCD1234", 1)
        assert result == "ASSET-ABCD1234-S001"

    def test_pattern_generation_high_index(self):
        from assets.services.barcode import (
            generate_serial_barcode_string,
        )

        result = generate_serial_barcode_string("ASSET-ABCD1234", 42)
        assert result == "ASSET-ABCD1234-S042"

    def test_cross_table_available(self, db):
        from assets.services.barcode import (
            validate_cross_table_barcode,
        )

        assert validate_cross_table_barcode("TOTALLY-UNIQUE-CODE")

    def test_cross_table_collision_asset(self, asset):
        from assets.services.barcode import (
            validate_cross_table_barcode,
        )

        assert not validate_cross_table_barcode(asset.barcode)

    def test_cross_table_collision_serial(
        self, serialised_asset, asset_serial
    ):
        from assets.services.barcode import (
            validate_cross_table_barcode,
        )

        assert not validate_cross_table_barcode(asset_serial.barcode)

    def test_cross_table_exclude_self(self, asset):
        from assets.services.barcode import (
            validate_cross_table_barcode,
        )

        # Excluding the asset itself should make it available
        assert validate_cross_table_barcode(
            asset.barcode, exclude_asset_pk=asset.pk
        )


class TestSerialCRUDService:
    """Test serial CRUD service functions."""

    def test_create_serial(self, serialised_asset):
        from assets.services.serial import create_serial

        serial = create_serial(
            serialised_asset, "SVC-001", condition="excellent"
        )
        assert serial.pk is not None
        assert serial.serial_number == "SVC-001"
        assert serial.barcode is not None
        assert serial.condition == "excellent"

    def test_create_serial_auto_barcode(self, serialised_asset):
        from assets.services.serial import create_serial

        serial = create_serial(serialised_asset, "SVC-002")
        assert serial.barcode.startswith(serialised_asset.barcode)
        assert "-S" in serial.barcode

    def test_create_serial_non_serialised_fails(self, asset):
        from assets.services.serial import create_serial

        with pytest.raises(ValidationError, match="non-serialised"):
            create_serial(asset, "FAIL-001")

    def test_update_serial(self, asset_serial):
        from assets.services.serial import update_serial

        updated = update_serial(asset_serial, condition="poor")
        assert updated.condition == "poor"
        asset_serial.refresh_from_db()
        assert asset_serial.condition == "poor"

    def test_archive_serial(self, asset_serial):
        from assets.services.serial import archive_serial

        archived = archive_serial(asset_serial)
        assert archived.is_archived is True
        asset_serial.refresh_from_db()
        assert asset_serial.is_archived is True

    def test_restore_serial(self, asset_serial):
        from assets.services.serial import archive_serial, restore_serial

        archive_serial(asset_serial)
        restored = restore_serial(asset_serial)
        assert restored.is_archived is False

    def test_get_available_serials(
        self, serialised_asset, location, second_user
    ):
        from assets.services.serial import get_available_serials

        s1 = AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="AV-1",
            barcode="AV-1-BC",
            status="active",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="AV-2",
            barcode="AV-2-BC",
            status="active",
            checked_out_to=second_user,
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="AV-3",
            barcode="AV-3-BC",
            status="retired",
            current_location=location,
        )
        available = get_available_serials(serialised_asset)
        assert list(available) == [s1]

    def test_get_serial_summary(self, serialised_asset, location, second_user):
        from assets.services.serial import get_serial_summary

        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="SUM-1",
            barcode="SUM-1-BC",
            status="active",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="SUM-2",
            barcode="SUM-2-BC",
            status="active",
            checked_out_to=second_user,
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="SUM-3",
            barcode="SUM-3-BC",
            status="retired",
            current_location=location,
        )
        summary = get_serial_summary(serialised_asset)
        assert summary["total"] == 3
        assert summary["by_status"]["active"] == 2
        assert summary["by_status"]["retired"] == 1
        assert summary["checked_out"] == 1
        assert summary["available"] == 1


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


class TestSerialAdmin:
    """Test AssetSerial and AssetKit admin registration."""

    def test_asset_serial_admin_registered(self, admin_client, db):
        response = admin_client.get("/admin/assets/assetserial/")
        assert response.status_code == 200

    def test_asset_kit_admin_registered(self, admin_client, db):
        response = admin_client.get("/admin/assets/assetkit/")
        assert response.status_code == 200

    def test_asset_admin_has_serial_inline(
        self, admin_client, serialised_asset
    ):
        response = admin_client.get(
            f"/admin/assets/asset/{serialised_asset.pk}/change/"
        )
        assert response.status_code == 200

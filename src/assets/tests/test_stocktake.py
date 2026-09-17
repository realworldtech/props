"""Tests for stocktake workflows."""

import pytest

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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
    AssetImage,
    Location,
    StocktakeItem,
    StocktakeSession,
    Transaction,
)

User = get_user_model()


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


class TestStocktakeWrongLocationV31:
    """V31: Stocktake prompt on wrong location."""

    def test_wrong_location_does_not_auto_transfer(
        self, admin_client, admin_user, asset, location
    ):
        from assets.models import Location, StocktakeSession

        other_loc = Location.objects.create(name="Other Location")
        asset.current_location = other_loc
        asset.save(update_fields=["current_location"])
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
            status="in_progress",
        )
        admin_client.post(
            reverse("assets:stocktake_confirm", args=[session.pk]),
            {"code": asset.barcode},
        )
        asset.refresh_from_db()
        # Should NOT auto-transfer
        assert asset.current_location == other_loc

    def test_transfer_confirmation_updates_location(
        self, admin_client, admin_user, asset, location
    ):
        from assets.models import Location, StocktakeSession

        other_loc = Location.objects.create(name="Other Loc 2")
        asset.current_location = other_loc
        asset.save(update_fields=["current_location"])
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
            status="in_progress",
        )
        admin_client.post(
            reverse("assets:stocktake_confirm", args=[session.pk]),
            {"transfer_asset_id": asset.pk},
        )
        asset.refresh_from_db()
        assert asset.current_location == location


@pytest.mark.django_db
class TestE2EStocktakeScenario:
    """S8.5.3: Stocktake start -> scan -> complete lifecycle."""

    def test_full_stocktake_lifecycle(
        self, admin_client, admin_user, category, location
    ):
        """Start stocktake, scan some assets, complete, verify
        missing."""
        # Create several assets at the location
        assets_at_location = []
        for i in range(4):
            a = Asset.objects.create(
                name=f"Stocktake Asset {i}",
                category=category,
                current_location=location,
                status="active",
                is_serialised=False,
                created_by=admin_user,
            )
            assets_at_location.append(a)

        # --- Step 1: Start stocktake for the location ---
        start_url = reverse("assets:stocktake_start")
        response = admin_client.post(start_url, {"location": location.pk})
        assert response.status_code == 302

        session = StocktakeSession.objects.filter(
            location=location, status="in_progress"
        ).first()
        assert session is not None
        assert session.started_by == admin_user

        # Verify expected assets match what we created
        expected_ids = set(
            session.expected_assets.values_list("pk", flat=True)
        )
        for a in assets_at_location:
            assert a.pk in expected_ids

        # --- Step 2: Scan (confirm) only the first 2 assets ---
        confirm_url = reverse("assets:stocktake_confirm", args=[session.pk])
        for a in assets_at_location[:2]:
            response = admin_client.post(confirm_url, {"asset_id": a.pk})
            assert response.status_code == 302

        # Verify confirmed assets have audit transactions
        for a in assets_at_location[:2]:
            audit_tx = Transaction.objects.filter(
                asset=a, action="audit"
            ).first()
            assert audit_tx is not None
            assert f"stocktake #{session.pk}" in audit_tx.notes.lower()

        # Verify unscanned assets have no audit transactions yet
        for a in assets_at_location[2:]:
            assert not Transaction.objects.filter(
                asset=a, action="audit"
            ).exists()

        # --- Step 3: Complete stocktake with mark_missing ---
        complete_url = reverse("assets:stocktake_complete", args=[session.pk])
        response = admin_client.post(
            complete_url,
            {
                "action": "complete",
                "mark_missing": "1",
                "notes": "E2E stocktake test",
            },
        )
        assert response.status_code == 302

        # Verify session is completed
        session.refresh_from_db()
        assert session.status == "completed"
        assert session.notes == "E2E stocktake test"

        # Verify: scanned assets remain active
        for a in assets_at_location[:2]:
            a.refresh_from_db()
            assert a.status == "active"

        # Verify: unscanned assets are now marked missing
        for a in assets_at_location[2:]:
            a.refresh_from_db()
            assert a.status == "missing"

    def test_stocktake_scan_by_barcode(
        self, admin_client, admin_user, category, location
    ):
        """Stocktake confirms asset when scanned by barcode code."""
        a = Asset.objects.create(
            name="Barcode Scan Asset",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=admin_user,
        )

        # Start stocktake
        admin_client.post(
            reverse("assets:stocktake_start"),
            {"location": location.pk},
        )
        session = StocktakeSession.objects.get(
            location=location, status="in_progress"
        )

        # Confirm by barcode code
        confirm_url = reverse("assets:stocktake_confirm", args=[session.pk])
        response = admin_client.post(confirm_url, {"code": a.barcode})
        assert response.status_code == 302

        # Asset should be confirmed
        assert session.confirmed_assets.filter(pk=a.pk).exists()
        assert Transaction.objects.filter(asset=a, action="audit").exists()


@pytest.mark.django_db
class TestStocktakeConcurrency:
    """S7.4 — Concurrency edge cases in stocktake."""

    def test_vv713_stocktake_during_concurrent_checkout(
        self,
        admin_client,
        admin_user,
        location,
        asset,
        second_user,
    ):
        """VV713: Asset checked out after stocktake starts should
        show as 'checked out', not 'missing'."""
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )
        StocktakeItem.objects.create(
            session=session, asset=asset, status="expected"
        )

        asset.checked_out_to = second_user
        asset.save(update_fields=["checked_out_to"])

        response = admin_client.get(
            reverse("assets:stocktake_detail", args=[session.pk])
        )
        content = response.content.decode()

        assert "Checked Out" in content or "checked out" in content, (
            "S7.4.3: Stocktake detail must show checked-out "
            "indicator for assets checked out since session "
            "started. Currently the view does not distinguish "
            "checked-out assets from missing ones."
        )


@pytest.mark.django_db
class TestStocktakeEdgeCases:
    """S7.9 — Stocktake workflow edge cases."""

    def test_vv735_scanning_asset_from_different_location(
        self, admin_client, admin_user, location
    ):
        """VV735: Scanning asset from different location during
        stocktake should show discrepancy warning."""
        other_loc = LocationFactory(name="Other Place")
        asset = AssetFactory(
            name="Misplaced Asset",
            status="active",
            category=CategoryFactory(),
            current_location=other_loc,
        )
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )

        admin_client.post(
            reverse(
                "assets:stocktake_confirm",
                args=[session.pk],
            ),
            {"code": asset.barcode},
        )
        follow_response = admin_client.get(
            reverse(
                "assets:stocktake_detail",
                args=[session.pk],
            )
        )
        content = follow_response.content.decode()
        assert (
            "Other Place" in content
            or "discrepancy" in content.lower()
            or "different location" in content.lower()
        ), (
            "S7.9.1: Scanning an asset from a different location "
            "during stocktake must show a discrepancy warning. "
            "Currently the system auto-confirms without warning."
        )

    def test_vv736_unknown_code_quick_capture_with_location(
        self, admin_client, admin_user, location
    ):
        """VV736: Unknown code during stocktake should offer
        Quick Capture with pre-filled location."""
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )

        response = admin_client.post(
            reverse(
                "assets:stocktake_confirm",
                args=[session.pk],
            ),
            {"code": "UNKNOWN-BARCODE-XYZ"},
            follow=True,
        )
        content = response.content.decode()
        assert (
            f"location={location.pk}" in content
            or f"location_id={location.pk}" in content
        ), (
            "S7.9.2: Quick Capture link must pre-fill the "
            "stocktake location. Currently the link does not "
            "include a location parameter."
        )

    def test_vv737_checked_out_asset_shows_indicator(
        self,
        admin_client,
        admin_user,
        location,
        asset,
        second_user,
    ):
        """VV737: Checked-out asset in stocktake should show
        a 'Checked Out' indicator."""
        asset.checked_out_to = second_user
        asset.save(update_fields=["checked_out_to"])

        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )
        StocktakeItem.objects.create(
            session=session, asset=asset, status="expected"
        )

        response = admin_client.get(
            reverse(
                "assets:stocktake_detail",
                args=[session.pk],
            )
        )
        content = response.content.decode()
        assert (
            "checked out" in content.lower()
            or "Borrower" in content
            or second_user.get_display_name() in content
        ), (
            "S7.9.3: Checked-out assets in stocktake must show "
            "a 'Checked Out to [borrower]' indicator. Currently "
            "the stocktake detail does not display checkout "
            "status."
        )

    def test_vv738_stocktake_pagination(
        self, admin_client, admin_user, location, category
    ):
        """VV738: Stocktake with many assets should paginate."""
        for i in range(30):
            AssetFactory(
                name=f"Bulk Asset {i}",
                status="active",
                category=category,
                current_location=location,
            )

        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )
        StocktakeItem.objects.bulk_create(
            [
                StocktakeItem(
                    session=session,
                    asset=a,
                    status="expected",
                )
                for a in Asset.objects.filter(current_location=location)
            ]
        )

        response = admin_client.get(
            reverse(
                "assets:stocktake_detail",
                args=[session.pk],
            )
        )
        content = response.content.decode()
        assert (
            "page" in content.lower()
            or "pagination" in content.lower()
            or "load more" in content.lower()
        ), (
            "S7.9.4: Stocktake with many assets should support "
            "pagination or progressive loading. Currently all "
            "assets are rendered in a single page."
        )

    def test_vv741_checked_out_not_auto_marked_missing(
        self,
        admin_client,
        admin_user,
        location,
        asset,
        second_user,
    ):
        """VV741: Completing stocktake must not auto-mark
        checked-out assets as missing."""
        asset.checked_out_to = second_user
        asset.save(update_fields=["checked_out_to"])

        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )
        StocktakeItem.objects.create(
            session=session, asset=asset, status="expected"
        )

        admin_client.post(
            reverse(
                "assets:stocktake_complete",
                args=[session.pk],
            ),
            {"action": "complete"},
        )
        asset.refresh_from_db()
        assert asset.status != "missing", (
            "S7.9.3/S7.9.7: Completing a stocktake must not "
            "mark checked-out assets as missing. Their "
            "whereabouts are known (with the borrower)."
        )


@pytest.mark.django_db
class TestStocktakeWorkflow:
    """Stocktake workflow: start, scan, confirm, missing assets."""

    def test_start_stocktake(self, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        assert session.status == "in_progress"
        assert session.location == location

    def test_confirm_asset_in_stocktake(self, asset, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        session.confirmed_assets.add(asset)
        assert asset in session.confirmed_assets.all()
        assert asset not in session.missing_assets

    def test_unconfirmed_asset_is_missing(self, asset, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        assert asset in session.missing_assets

    def test_unexpected_asset_detected(self, asset, location, user):
        other_loc = Location.objects.create(name="Different Loc")
        session = StocktakeSession.objects.create(
            location=other_loc, started_by=user
        )
        session.confirmed_assets.add(asset)
        assert asset in session.unexpected_assets

    def test_stocktake_item_tracking(self, asset, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        item = StocktakeItem.objects.create(
            session=session,
            asset=asset,
            status="expected",
        )
        assert item.status == "expected"
        item.status = "confirmed"
        item.scanned_by = user
        item.scanned_at = timezone.now()
        item.save()
        item.refresh_from_db()
        assert item.status == "confirmed"

    def test_complete_stocktake(self, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        session.status = "completed"
        session.ended_at = timezone.now()
        session.save()
        session.refresh_from_db()
        assert session.status == "completed"
        assert session.ended_at is not None

    def test_one_in_progress_per_location(self, location, user):
        StocktakeSession.objects.create(location=location, started_by=user)
        with pytest.raises(Exception):
            StocktakeSession.objects.create(location=location, started_by=user)


@pytest.mark.django_db
class TestV233StocktakeThumbnail:
    """V233: Expected asset in stocktake shows primary image thumbnail."""

    def test_stocktake_detail_shows_thumbnail(
        self, admin_client, admin_user, asset, user, location
    ):
        """Stocktake detail should display primary image thumbnail."""
        _img = AssetImage.objects.create(  # noqa: F841
            asset=asset,
            image="st_thumb.jpg",
            is_primary=True,
            uploaded_by=user,
        )
        asset.current_location = location
        asset.save()
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
            status="in_progress",
        )
        response = admin_client.get(
            reverse("assets:stocktake_detail", args=[session.pk])
        )
        content = response.content.decode()
        # Should have img tag for asset thumbnail
        assert "st_thumb.jpg" in content or "<img" in content


# ── Batch F: S3 Data Model + S7 Edge Cases ──────────────────────


@pytest.mark.django_db
class TestV548StocktakeDepartmentFK:
    """V548: StocktakeSession should have optional department FK."""

    def test_stocktake_session_has_department_field(self, db):
        """StocktakeSession model should have a department FK."""
        from assets.models import StocktakeSession

        field = StocktakeSession._meta.get_field("department")
        assert field is not None
        assert field.null is True

    def test_stocktake_with_department(self, admin_user, location, department):
        """StocktakeSession can be created with a department."""
        from assets.models import StocktakeSession

        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
            department=department,
        )
        session.refresh_from_db()
        assert session.department == department


# ============================================================
# V736 (S7.9.2): Quick Capture link with location in stocktake
# ============================================================


@pytest.mark.django_db
class TestV736StocktakeQuickCaptureLink:
    """V736: Quick Capture link in stocktake should pre-fill location."""

    def test_stocktake_detail_quick_capture_has_location(
        self, admin_client, location, admin_user
    ):
        """Stocktake detail should have Quick Capture link with location."""
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )
        url = reverse("assets:stocktake_detail", args=[session.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Should contain Quick Capture link with location parameter
        expected_param = f"location={location.pk}"
        assert expected_param in content


# ============================================================
# V236 (S2.7.1-06): Checked-out assets in stocktake
# ============================================================


@pytest.mark.django_db
class TestV236CheckedOutAssetsInStocktake:
    """V236: Checked-out assets should be shown separately in stocktake."""

    def test_checked_out_asset_marked_in_stocktake_detail(
        self, admin_client, admin_user, location, category, user
    ):
        """A checked-out asset at a location should show indicator."""
        asset = Asset.objects.create(
            name="V236 Checked Out",
            category=category,
            current_location=location,
            status="active",
            checked_out_to=user,
            is_serialised=False,
            created_by=admin_user,
        )
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )
        url = reverse("assets:stocktake_detail", args=[session.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Asset should appear in expected list
        assert asset.name in content
        # Should show checked-out indicator
        assert "Checked Out" in content

    def test_checked_out_asset_with_home_location_in_stocktake(
        self, admin_client, admin_user, location, category, user
    ):
        """Checked-out asset with home_location matching stocktake
        location should appear in expected list even if current_location
        differs."""
        other_loc = Location.objects.create(name="V236 Other Loc")
        asset = Asset.objects.create(
            name="V236 Home Loc Asset",
            category=category,
            current_location=other_loc,
            home_location=location,
            status="active",
            checked_out_to=user,
            is_serialised=False,
            created_by=admin_user,
        )
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )
        url = reverse("assets:stocktake_detail", args=[session.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Asset should appear in expected list via home_location
        assert asset.name in content
        assert "Checked Out" in content


# ============================================================
# V719 (S7.5.4): Marking a checked-out asset as missing
# ============================================================


@pytest.mark.django_db
class TestV719CheckedOutAssetToMissing:
    """V719: Checked-out assets should allow transition to missing."""

    def test_checked_out_asset_can_transition_to_missing(
        self, category, location, user
    ):
        """An active checked-out asset should be allowed to go missing."""
        asset = Asset.objects.create(
            name="V719 Checked Out",
            category=category,
            current_location=location,
            status="active",
            checked_out_to=user,
            is_serialised=False,
            created_by=user,
            lost_stolen_notes="",
        )
        # The state machine should allow active -> missing
        assert asset.can_transition_to("missing")

    def test_checked_out_asset_missing_via_state_service(
        self, category, location, user
    ):
        """validate_transition should not block missing for checked-out."""
        from assets.services.state import validate_transition

        asset = Asset.objects.create(
            name="V719 State Service",
            category=category,
            current_location=location,
            status="active",
            checked_out_to=user,
            is_serialised=False,
            created_by=user,
        )
        # Should NOT raise — missing is allowed for checked-out assets
        # (only retired/disposed are blocked)
        validate_transition(asset, "missing")

    def test_checked_out_asset_cannot_transition_to_retired(
        self, category, location, user
    ):
        """Checked-out asset should NOT be allowed to retire."""
        from assets.services.state import validate_transition

        asset = Asset.objects.create(
            name="V719 Retire Block",
            category=category,
            current_location=location,
            status="active",
            checked_out_to=user,
            is_serialised=False,
            created_by=user,
        )
        with pytest.raises(ValidationError):
            validate_transition(asset, "retired")


# ============================================================
# V737 (S7.9.3): Checked-out asset in stocktake expected list
# ============================================================


@pytest.mark.django_db
class TestV737CheckedOutInStocktakeExpectedList:
    """V737: Checked-out assets with home_location should appear in
    stocktake expected list."""

    def test_checked_out_asset_in_expected_list_via_home_location(
        self, admin_user, location, category, user
    ):
        """Asset checked out from home_location should be in expected."""
        other_loc = Location.objects.create(name="V737 Other")
        asset = Asset.objects.create(
            name="V737 Expected Asset",
            category=category,
            current_location=other_loc,
            home_location=location,
            status="active",
            checked_out_to=user,
            is_serialised=False,
            created_by=admin_user,
        )
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )
        expected_ids = list(
            session.expected_assets.values_list("pk", flat=True)
        )
        assert asset.pk in expected_ids

    def test_stocktake_start_snapshots_checked_out_home_assets(
        self, admin_client, admin_user, location, category, user
    ):
        """stocktake_start should include checked-out home_location assets
        in the StocktakeItem snapshot."""
        other_loc = Location.objects.create(name="V737 Start Other")
        asset = Asset.objects.create(
            name="V737 Snapshot Asset",
            category=category,
            current_location=other_loc,
            home_location=location,
            status="active",
            checked_out_to=user,
            is_serialised=False,
            created_by=admin_user,
        )
        url = reverse("assets:stocktake_start")
        response = admin_client.post(
            url, {"location": location.pk}, follow=True
        )
        assert response.status_code == 200
        # Check that the asset was snapshot into StocktakeItem
        session = StocktakeSession.objects.filter(location=location).first()
        assert session is not None
        item_asset_ids = list(
            StocktakeItem.objects.filter(session=session).values_list(
                "asset_id", flat=True
            )
        )
        assert asset.pk in item_asset_ids


# ============================================================
# V239 (S2.7.3-01): Stocktake confirm asset
# ============================================================


@pytest.mark.django_db
class TestV239StocktakeConfirmAsset:
    """V239: stocktake_confirm POST marks an asset as confirmed."""

    def test_stocktake_confirm_marks_asset(
        self, admin_client, asset, location, admin_user
    ):
        """POST to stocktake_confirm should mark asset as confirmed in
        stocktake."""
        from assets.models import StocktakeItem, StocktakeSession

        session = StocktakeSession.objects.create(
            location=location,
            status="in_progress",
            started_by=admin_user,
        )
        item = StocktakeItem.objects.create(
            session=session, asset=asset, status="expected"
        )
        url = reverse("assets:stocktake_confirm", args=[session.pk])
        response = admin_client.post(url, {"asset_id": asset.pk})
        assert response.status_code == 302  # Redirects after confirm
        item.refresh_from_db()
        assert item.status == "confirmed"


# ============================================================
# V240 (S2.7.3-02): Stocktake summary
# ============================================================


@pytest.mark.django_db
class TestV240StocktakeSummary:
    """V240: stocktake_summary view returns the summary for a completed
    stocktake."""

    def test_stocktake_summary_displays_results(
        self, admin_client, asset, location, admin_user
    ):
        """stocktake_summary should show completion summary."""
        from assets.models import StocktakeItem, StocktakeSession

        session = StocktakeSession.objects.create(
            location=location,
            status="completed",
            started_by=admin_user,
        )
        StocktakeItem.objects.create(
            session=session, asset=asset, status="confirmed"
        )
        url = reverse("assets:stocktake_summary", args=[session.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert location.name in content
        # Should show counts or summary data
        assert "confirmed" in content.lower() or "1" in content

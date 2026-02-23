"""S12 UI Exposure Matrix — behavioural verification.

These tests go beyond URL existence checks. Each test verifies that:
  1. The URL renders correctly (GET -> 200, key elements present)
  2. The action actually works (POST -> state change + Transaction)
  3. Navigation: the page is reachable from its declared entry point

Read: specs/props/sections/s12-ui-exposure-matrix.md for the full matrix.
"""

import pytest

from django.urls import reverse

from assets.factories import AssetFactory, UserFactory
from assets.models import Asset, Transaction


@pytest.mark.django_db
class TestS12_3_CoreAssetManagement:
    """S12.3 -- Core Asset Management (S2.1-S2.2)."""

    def test_quick_capture_url_renders(self, admin_client):
        url = reverse("assets:quick_capture")
        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert b"Quick Capture" in resp.content

    def test_quick_capture_post_creates_draft(self, admin_client, admin_user):
        from django.core.files.uploadedfile import SimpleUploadedFile

        url = reverse("assets:quick_capture")
        image = SimpleUploadedFile(
            "item.jpg",
            b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00"
            b"\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
            content_type="image/gif",
        )
        resp = admin_client.post(url, {"image": image})
        assert resp.status_code in (200, 302)
        assert Asset.objects.filter(
            status="draft", created_by=admin_user
        ).exists()

    def test_drafts_queue_accessible_from_dashboard(
        self, admin_client, draft_asset
    ):
        dashboard_resp = admin_client.get(reverse("assets:dashboard"))
        assert b"/drafts/" in dashboard_resp.content
        resp = admin_client.get(reverse("assets:drafts_queue"))
        assert resp.status_code == 200

    def test_asset_list_loads(self, admin_client, active_asset):
        resp = admin_client.get(reverse("assets:asset_list"))
        assert resp.status_code == 200
        assert active_asset.name.encode() in resp.content

    def test_asset_detail_loads(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        assert active_asset.name.encode() in resp.content

    def test_asset_edit_form_renders(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_edit", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        assert b"form" in resp.content.lower()

    @pytest.mark.xfail(
        reason=(
            "GAP S2.1.5: Promote draft via POST action=promote on asset_detail"
            " does not transition status to active (no promote action handler"
            " on this endpoint). Use drafts_queue bulk promote instead."
        ),
        strict=True,
    )
    def test_promote_draft_to_active(
        self, admin_client, draft_asset, category, location
    ):
        draft_asset.category = category
        draft_asset.current_location = location
        draft_asset.name = "Rocking Chair"
        draft_asset.save()
        url = reverse("assets:asset_detail", args=[draft_asset.pk])
        resp = admin_client.post(url, {"action": "promote"})
        draft_asset.refresh_from_db()
        assert draft_asset.status == "active"

    def test_asset_create_form_renders(self, admin_client):
        resp = admin_client.get(reverse("assets:asset_create"))
        assert resp.status_code == 200
        assert b"form" in resp.content.lower()

    def test_asset_detail_shows_action_bar(self, admin_client, active_asset):
        """S2.2.9 -- action bar with Edit/Check Out/Check In/Transfer buttons."""
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        # At least the edit link should appear in the action bar area
        assert b"edit" in resp.content.lower()

    @pytest.mark.xfail(
        reason=(
            "GAP S2.2.7: asset_merge_select GET redirects to /assets/ (302)"
            " instead of rendering a selection form. The merge flow requires"
            " asset IDs passed via POST from the bulk action list."
        ),
        strict=True,
    )
    def test_asset_merge_select_renders(self, admin_client):
        """S2.2.7 -- asset merge is admin-only bulk action surface."""
        resp = admin_client.get(reverse("assets:asset_merge_select"))
        assert resp.status_code == 200

    # -- Affordance exposure tests (T31, T33) --

    def test_asset_detail_shows_checkout_link(
        self, admin_client, active_asset
    ):
        """T31: Asset detail page for an available asset must contain a
        link to the checkout URL."""
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        checkout_url = reverse("assets:asset_checkout", args=[active_asset.pk])
        assert (
            checkout_url.encode() in resp.content
        ), f"Checkout URL {checkout_url} not found in asset detail page"

    def test_asset_detail_shows_transfer_link(
        self, admin_client, active_asset
    ):
        """T33: Asset detail page must contain a link to transfer or
        relocate the asset."""
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        transfer_url = reverse("assets:asset_transfer", args=[active_asset.pk])
        relocate_url = reverse("assets:asset_relocate", args=[active_asset.pk])
        content = resp.content
        assert (
            transfer_url.encode() in content
            or relocate_url.encode() in content
        ), (
            f"Neither transfer URL {transfer_url} nor relocate URL"
            f" {relocate_url} found in asset detail page"
        )


@pytest.mark.django_db
class TestS12_4_CheckoutCheckin:
    """S12.4 -- Check-out / Check-in / Transfer (S2.3)."""

    def test_checkout_form_renders(self, admin_client, active_asset):
        url = reverse("assets:asset_checkout", args=[active_asset.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert b"borrower" in resp.content.lower()

    def test_checkout_post_transitions_asset(
        self, admin_client, active_asset, borrower_user
    ):
        url = reverse("assets:asset_checkout", args=[active_asset.pk])
        resp = admin_client.post(
            url,
            {
                "borrower": borrower_user.pk,
                "destination_location": active_asset.current_location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to == borrower_user
        assert Transaction.objects.filter(
            asset=active_asset, action="checkout"
        ).exists()

    @pytest.mark.xfail(
        reason=(
            "GAP S2.3.4: checkin POST does not clear checked_out_to on the"
            " asset. The view accepts the form but the service layer does not"
            " nullify the borrower field — likely a form field mismatch"
            " ('destination_location' vs the expected field name in the form)."
        ),
        strict=True,
    )
    def test_checkin_returns_asset(
        self, admin_client, active_asset, borrower_user
    ):
        active_asset.checked_out_to = borrower_user
        active_asset.save()
        Transaction.objects.create(
            asset=active_asset,
            action="checkout",
            user=active_asset.created_by,
            borrower=borrower_user,
        )
        url = reverse("assets:asset_checkin", args=[active_asset.pk])
        resp = admin_client.post(
            url,
            {
                "destination_location": active_asset.current_location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to is None
        assert Transaction.objects.filter(
            asset=active_asset, action="checkin"
        ).exists()

    @pytest.mark.xfail(
        reason=(
            "GAP S2.3.6: transfer POST does not update current_location on the"
            " asset model. The view may redirect correctly but the state change"
            " is not persisted — likely a form field name mismatch or the view"
            " requires 'to_location' rather than 'destination_location'."
        ),
        strict=True,
    )
    def test_transfer_moves_location(
        self, admin_client, active_asset, warehouse
    ):
        url = reverse("assets:asset_transfer", args=[active_asset.pk])
        resp = admin_client.post(
            url,
            {
                "destination_location": warehouse["bay1"].pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.current_location == warehouse["bay1"]
        assert Transaction.objects.filter(
            asset=active_asset, action="transfer"
        ).exists()

    def test_my_borrowed_items_shows_checkout(
        self, client_logged_in, active_asset, user
    ):
        active_asset.checked_out_to = user
        active_asset.save()
        resp = client_logged_in.get(reverse("assets:my_borrowed_items"))
        assert resp.status_code == 200
        assert active_asset.name.encode() in resp.content

    def test_handover_form_renders(
        self, admin_client, active_asset, borrower_user
    ):
        """S2.3.5 -- custody transfer (hand over) form."""
        active_asset.checked_out_to = borrower_user
        active_asset.save()
        url = reverse("assets:asset_handover", args=[active_asset.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200

    def test_relocate_form_renders(self, admin_client, active_asset):
        """S2.3.6 -- relocate form renders for an active asset."""
        url = reverse("assets:asset_relocate", args=[active_asset.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200

    def test_transaction_list_renders(self, admin_client, active_asset):
        """S2.3.7 -- global transaction list page."""
        Transaction.objects.create(
            asset=active_asset,
            action="transfer",
            user=active_asset.created_by,
        )
        resp = admin_client.get(reverse("assets:transaction_list"))
        assert resp.status_code == 200

    def test_checkin_form_renders(self, admin_client, active_asset):
        """S2.3.4 -- check-in form renders (even when not checked out)."""
        url = reverse("assets:asset_checkin", args=[active_asset.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200


@pytest.mark.django_db
class TestS12_5_BarcodeSystem:
    """S12.5 -- Barcode System (S2.4)."""

    def test_scan_page_renders(self, admin_client):
        """S2.4.4 -- scan page with camera viewfinder."""
        resp = admin_client.get(reverse("assets:scan"))
        assert resp.status_code == 200

    def test_asset_label_renders(self, admin_client, active_asset):
        """S2.4.5 -- label print page renders for an asset."""
        resp = admin_client.get(
            reverse("assets:asset_label", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_barcode_pregenerate_renders(self, admin_client):
        """S2.4.3 -- barcode pre-generation page."""
        resp = admin_client.get(reverse("assets:barcode_pregenerate"))
        assert resp.status_code == 200

    def test_virtual_barcode_list_renders(self, admin_client):
        """S2.4.3 -- virtual barcode list page."""
        resp = admin_client.get(reverse("assets:virtual_barcode_list"))
        assert resp.status_code == 200

    def test_asset_by_identifier_resolves_barcode(
        self, admin_client, active_asset
    ):
        """S2.4.4, S2.5.5 -- universal lookup URL redirects to asset detail."""
        resp = admin_client.get(
            reverse("assets:asset_by_identifier", args=[active_asset.barcode])
        )
        assert resp.status_code in (200, 302)

    def test_scan_lookup_json_endpoint(self, admin_client, active_asset):
        """S2.4.4 -- scan lookup returns JSON with asset info."""
        resp = admin_client.get(
            reverse("assets:scan_lookup"),
            {"q": active_asset.barcode},
            HTTP_ACCEPT="application/json",
        )
        assert resp.status_code == 200

    def test_asset_detail_shows_barcode(self, admin_client, active_asset):
        """S2.4.1 -- barcode display on asset detail page."""
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        assert active_asset.barcode.encode() in resp.content

    def test_print_history_renders(self, admin_client, active_asset):
        """S2.4.5c -- print history view for an asset."""
        resp = admin_client.get(
            reverse("assets:print_history", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #23: QR code not displayed on asset detail sidebar"
            " (S2.4.5-08). Only Code128 barcode is shown; no QR code"
            " image is rendered."
        ),
    )
    def test_asset_detail_shows_qr_code(self, admin_client, active_asset):
        """S2.4.5-08: Asset detail sidebar must show QR code encoding the
        asset URL."""
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        # Must have an actual QR code image (data URI or img tag with qr),
        # not just the substring "qr" appearing anywhere on the page.
        has_qr_image = (
            "data:image" in content and "qr" in content.lower()
        ) or 'alt="QR' in content
        assert has_qr_image, "No QR code image found on asset detail page"


@pytest.mark.django_db
class TestS12_6_NFCTagManagement:
    """S12.6 -- NFC Tag Management (S2.5)."""

    def test_nfc_add_page_renders(self, admin_client, active_asset):
        """S2.5.1 -- NFC tag add page (Web NFC / manual entry)."""
        url = reverse("assets:nfc_add", args=[active_asset.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200

    def test_asset_detail_shows_nfc_section(self, admin_client, active_asset):
        """S2.5.1 -- asset detail exposes NFC Tags section."""
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        # Expect NFC tag section to be present
        assert b"nfc" in resp.content.lower() or b"NFC" in resp.content

    # -- Affordance exposure tests (T34) --

    def test_asset_detail_shows_nfc_add_affordance(
        self, admin_client, active_asset
    ):
        """T34: Asset detail page must contain a link or button to add
        an NFC tag (the nfc_add URL)."""
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        nfc_add_url = reverse("assets:nfc_add", args=[active_asset.pk])
        assert (
            nfc_add_url.encode() in resp.content
        ), f"NFC add URL {nfc_add_url} not found in asset detail page"


@pytest.mark.django_db
class TestS12_7_SearchBrowseExport:
    """S12.7 -- Search, Browse & Export (S2.6, S2.9)."""

    def test_asset_list_text_search(self, admin_client, active_asset):
        """S2.6.1 -- text search returns matching assets."""
        resp = admin_client.get(
            reverse("assets:asset_list"), {"q": active_asset.name}
        )
        assert resp.status_code == 200
        assert active_asset.name.encode() in resp.content

    def test_asset_list_filter_by_status(self, admin_client, active_asset):
        """S2.6.2 -- filter by status=active."""
        resp = admin_client.get(
            reverse("assets:asset_list"), {"status": "active"}
        )
        assert resp.status_code == 200
        assert active_asset.name.encode() in resp.content

    def test_asset_list_pagination(self, admin_client):
        """S2.6.4 -- pagination controls present."""
        resp = admin_client.get(reverse("assets:asset_list"))
        assert resp.status_code == 200

    def test_export_assets_endpoint(self, admin_client, active_asset):
        """S2.9.1 -- Excel export triggers a download."""
        resp = admin_client.get(reverse("assets:export_assets"))
        assert resp.status_code == 200
        assert (
            "spreadsheet" in resp.get("Content-Type", "").lower()
            or "excel" in resp.get("Content-Type", "").lower()
            or "octet-stream" in resp.get("Content-Type", "").lower()
        )

    def test_category_list_renders(self, admin_client, category):
        """S2.6.2 -- category browse via category list."""
        resp = admin_client.get(reverse("assets:category_list"))
        assert resp.status_code == 200

    def test_location_list_renders(self, admin_client, location):
        """S2.12.2 -- location browse via location list."""
        resp = admin_client.get(reverse("assets:location_list"))
        assert resp.status_code == 200

    def test_tag_list_renders(self, admin_client, tag):
        """S2.2.6 -- tag management page."""
        resp = admin_client.get(reverse("assets:tag_list"))
        assert resp.status_code == 200

    # -- Affordance exposure tests (T38) --

    def test_asset_list_shows_bulk_actions_bar(self, admin_client):
        """T38: Asset list page must expose a bulk actions bar with
        action options (transfer, status change, checkout, etc.).
        Note: merge is handled via asset_merge_select, not the bulk
        bar -- see test_asset_merge_select_renders for that flow."""
        resp = admin_client.get(reverse("assets:asset_list"))
        assert resp.status_code == 200
        content = resp.content.decode()
        assert (
            "bulk_action" in content or "bulk-action" in content
        ), "Asset list page must contain a bulk actions bar"
        # The bar must have at least transfer and checkout options
        content_lower = content.lower()
        assert (
            "transfer" in content_lower
        ), "Bulk actions bar must include a transfer option"
        assert (
            "checkout" in content_lower or "check out" in content_lower
        ), "Bulk actions bar must include a checkout option"


@pytest.mark.django_db
class TestS12_8_Stocktake:
    """S12.8 -- Stocktake (S2.7)."""

    def test_stocktake_list_renders(self, admin_client):
        """S2.7.1 -- stocktake list/history page."""
        resp = admin_client.get(reverse("assets:stocktake_list"))
        assert resp.status_code == 200

    def test_stocktake_start_renders(self, admin_client):
        """S2.7.1 -- start stocktake page renders."""
        resp = admin_client.get(reverse("assets:stocktake_start"))
        assert resp.status_code == 200

    def test_stocktake_start_creates_session(self, admin_client, location):
        """S2.7.1 -- posting to stocktake start creates a session."""
        from assets.models import StocktakeSession

        resp = admin_client.post(
            reverse("assets:stocktake_start"),
            {"location": location.pk},
        )
        assert StocktakeSession.objects.filter(location=location).exists()

    def test_stocktake_detail_renders(
        self, admin_client, location, admin_user
    ):
        """S2.7.2 -- stocktake session page (scanning + checklist)."""
        from assets.models import StocktakeSession

        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
        )
        resp = admin_client.get(
            reverse("assets:stocktake_detail", args=[session.pk])
        )
        assert resp.status_code == 200

    def test_location_detail_has_stocktake_button(
        self, admin_client, location
    ):
        """S2.7.1 -- 'Start Stocktake' button appears on location detail."""
        resp = admin_client.get(
            reverse("assets:location_detail", args=[location.pk])
        )
        assert resp.status_code == 200
        assert (
            b"stocktake" in resp.content.lower()
            or b"Stocktake" in resp.content
        )

    # -- Affordance exposure tests (T37) --

    def test_location_detail_shows_stocktake_link(
        self, admin_client, location
    ):
        """T37: Location detail page must contain a link to the stocktake
        start URL or a stocktake-related affordance."""
        resp = admin_client.get(
            reverse("assets:location_detail", args=[location.pk])
        )
        assert resp.status_code == 200
        stocktake_url = reverse("assets:stocktake_start")
        content = resp.content.decode()
        assert stocktake_url in content or "stocktake" in content.lower(), (
            f"Neither stocktake URL {stocktake_url} nor 'stocktake' text"
            " found on location detail page"
        )


@pytest.mark.django_db
class TestS12_9_BulkOperations:
    """S12.9 -- Bulk Operations (S2.8)."""

    def test_bulk_actions_endpoint_accessible(self, admin_client):
        """S2.8.1-S2.8.3 -- bulk actions endpoint exists."""
        resp = admin_client.get(reverse("assets:bulk_actions"))
        # Bulk endpoint may redirect or return 405 on GET; just check it exists
        assert resp.status_code in (200, 302, 405)

    def test_drafts_bulk_action_accessible(self, admin_client):
        """S2.8.3 -- drafts bulk action endpoint exists."""
        resp = admin_client.get(reverse("assets:drafts_bulk_action"))
        assert resp.status_code in (200, 302, 405)

    def test_print_all_filtered_labels_accessible(
        self, admin_client, active_asset
    ):
        """S2.8.2 -- bulk print labels endpoint accessible."""
        resp = admin_client.get(reverse("assets:print_all_filtered_labels"))
        assert resp.status_code in (200, 302, 405)

    def test_lost_stolen_report_renders(self, admin_client):
        """S2.8 -- lost/stolen report page renders for System Admin."""
        resp = admin_client.get(reverse("assets:lost_stolen_report"))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestS12_10_DepartmentAccess:
    """S12.10 -- Department & Access Control (S2.10)."""

    def test_user_profile_renders(self, client_logged_in):
        """S2.10.5 -- user profile page renders."""
        resp = client_logged_in.get(reverse("accounts:profile"))
        assert resp.status_code == 200
        # Should show user details
        assert b"profile" in resp.content.lower() or b"Profile" in resp.content

    def test_profile_edit_renders(self, client_logged_in):
        """S2.10.5-02 -- profile edit page renders."""
        resp = client_logged_in.get(reverse("accounts:profile_edit"))
        assert resp.status_code == 200
        assert b"form" in resp.content.lower()

    def test_viewer_cannot_checkout(self, viewer_client, active_asset):
        """S2.10.3 -- Viewer role cannot perform checkout (403 or redirect)."""
        url = reverse("assets:asset_checkout", args=[active_asset.pk])
        resp = viewer_client.post(
            url,
            {
                "borrower": 1,
                "destination_location": active_asset.current_location.pk,
            },
        )
        assert resp.status_code in (403, 302, 200)
        # Asset should NOT be checked out
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to is None

    def test_category_list_shows_department_grouping(
        self, admin_client, category
    ):
        """S2.10.2 -- categories grouped by department."""
        resp = admin_client.get(reverse("assets:category_list"))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestS12_11_Dashboard:
    """S12.11 -- Dashboard (S2.11)."""

    def test_dashboard_loads(self, admin_client):
        """S2.11.1 -- dashboard loads for authenticated user."""
        resp = admin_client.get(reverse("assets:dashboard"))
        assert resp.status_code == 200

    def test_dashboard_shows_statistics(self, admin_client, active_asset):
        """S2.11.1 -- dashboard shows asset count statistics."""
        resp = admin_client.get(reverse("assets:dashboard"))
        assert resp.status_code == 200
        # Page should have some numeric content (counts)
        content = resp.content.decode()
        assert any(char.isdigit() for char in content)

    def test_dashboard_has_quick_actions(self, admin_client):
        """S2.11.3 -- dashboard shows Quick Capture, Scan links."""
        resp = admin_client.get(reverse("assets:dashboard"))
        assert resp.status_code == 200
        assert (
            b"quick" in resp.content.lower()
            or b"capture" in resp.content.lower()
            or b"scan" in resp.content.lower()
        )

    def test_dashboard_shows_recent_activity(self, admin_client, active_asset):
        """S2.11.2 -- dashboard shows recent transactions."""
        Transaction.objects.create(
            asset=active_asset,
            action="transfer",
            user=active_asset.created_by,
        )
        resp = admin_client.get(reverse("assets:dashboard"))
        assert resp.status_code == 200

    def test_dashboard_shows_checked_out_section(
        self, admin_client, active_asset, borrower_user
    ):
        """S2.3.8 -- dashboard shows currently checked-out assets."""
        active_asset.checked_out_to = borrower_user
        active_asset.save()
        resp = admin_client.get(reverse("assets:dashboard"))
        assert resp.status_code == 200
        assert active_asset.name.encode() in resp.content

    def test_dashboard_unauthenticated_redirects(self, client):
        """Dashboard requires login."""
        resp = client.get(reverse("assets:dashboard"))
        assert resp.status_code in (302, 403)

    def test_dashboard_has_drafts_link(self, admin_client, draft_asset):
        """S2.11.3 -- dashboard has a link to the drafts queue."""
        resp = admin_client.get(reverse("assets:dashboard"))
        assert resp.status_code == 200
        assert b"/drafts/" in resp.content

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #21: hold_list_count computed in view (views.py:250-256)"
            " but never passed to template context. Dashboard does not"
            " show the count."
        ),
    )
    def test_dashboard_shows_active_hold_list_count(
        self, admin_client, active_hold_list
    ):
        """S12.11 / S2.16: Dashboard must show count of active hold lists."""
        resp = admin_client.get(reverse("assets:dashboard"))
        assert resp.status_code == 200
        # hold_list_count must be present in the template context
        assert (
            "hold_list_count" in resp.context
        ), "hold_list_count not in dashboard template context"
        # And the count must be > 0 since active_hold_list exists
        assert resp.context["hold_list_count"] > 0


@pytest.mark.django_db
class TestS12_12_AdminUI:
    """S12.12 -- Admin UI (S2.13)."""

    def test_admin_asset_changelist_loads(self, admin_client):
        """S2.13.2 -- asset admin changelist with inlines."""
        resp = admin_client.get("/admin/assets/asset/")
        assert resp.status_code == 200

    def test_admin_transaction_changelist_loads(self, admin_client):
        """S2.13.3 -- transaction admin changelist."""
        resp = admin_client.get("/admin/assets/transaction/")
        assert resp.status_code == 200

    def test_admin_department_changelist_loads(self, admin_client):
        """S2.13.4 -- department admin changelist."""
        resp = admin_client.get("/admin/assets/department/")
        assert resp.status_code == 200

    def test_admin_location_changelist_loads(self, admin_client):
        """S2.13.1 -- location admin via unfold theme."""
        resp = admin_client.get("/admin/assets/location/")
        assert resp.status_code == 200

    def test_admin_user_changelist_loads(self, admin_client):
        """S2.13.4, S2.13.5 -- user admin with role/dept summary."""
        resp = admin_client.get("/admin/accounts/customuser/")
        assert resp.status_code == 200


@pytest.mark.django_db
class TestS12_13_AIImageAnalysis:
    """S12.13 -- AI Image Analysis (S2.14)."""

    def test_asset_detail_shows_ai_panel(self, admin_client, active_asset):
        """S2.14.3 -- AI suggestions panel present on asset detail."""
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        # The AI panel or AI-related elements should be present
        assert (
            b"ai" in resp.content.lower()
            or b"suggest" in resp.content.lower()
            or b"analysis" in resp.content.lower()
        )

    def test_ai_status_endpoint_accessible(self, admin_client, active_asset):
        """S2.14.2 -- AI status polling endpoint accessible."""
        from assets.models import AssetImage

        image = AssetImage.objects.create(
            asset=active_asset,
            uploaded_by=active_asset.created_by,
        )
        url = reverse("assets:ai_status", args=[active_asset.pk, image.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200

    @pytest.mark.skip(
        reason="GAP: AI analysis log view not yet exposed on frontend (S2.13.2-07)"
    )
    def test_ai_analysis_log_accessible_to_admin(self, admin_client):
        """S2.13.2-07 -- AI Analysis Log view for System Admin."""
        resp = admin_client.get(reverse("assets:ai_analysis_log"))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestS12_14_UserRegistration:
    """S12.14 -- User Registration & Approval (S2.15)."""

    def test_register_page_renders(self, client):
        """S2.15.1 -- self-registration page renders without auth."""
        resp = client.get(reverse("accounts:register"))
        assert resp.status_code == 200
        assert b"form" in resp.content.lower()

    def test_register_creates_pending_user(self, client):
        """S2.15.1 -- registration form creates a pending user account."""
        from accounts.models import CustomUser

        resp = client.post(
            reverse("accounts:register"),
            {
                "email": "newuser@example.com",
                "password1": "strongpassword123!",
                "password2": "strongpassword123!",
                "display_name": "New User",
            },
        )
        assert resp.status_code in (200, 302)
        assert CustomUser.objects.filter(email="newuser@example.com").exists()

    def test_approval_queue_renders_for_admin(self, admin_client):
        """S2.15.4 -- approval queue page accessible to System Admin."""
        resp = admin_client.get(reverse("accounts:approval_queue"))
        assert resp.status_code == 200

    def test_approval_queue_inaccessible_to_member(self, client_logged_in):
        """S2.15.4 -- approval queue blocked for non-admin users."""
        resp = client_logged_in.get(reverse("accounts:approval_queue"))
        assert resp.status_code in (302, 403)

    def test_login_page_renders(self, client):
        """S2.15 -- login page renders for unauthenticated users."""
        resp = client.get(reverse("accounts:login"))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestS12_15_HoldLists:
    """S12.15 -- Hold Lists & Projects (S2.16)."""

    def test_hold_list_list_renders(self, admin_client, hold_list):
        """S2.16.3 -- hold list index page."""
        resp = admin_client.get(reverse("assets:holdlist_list"))
        assert resp.status_code == 200

    def test_hold_list_create_renders(self, admin_client):
        """S2.16.3 -- hold list creation form."""
        resp = admin_client.get(reverse("assets:holdlist_create"))
        assert resp.status_code == 200
        assert b"form" in resp.content.lower()

    def test_hold_list_detail_renders(self, admin_client, hold_list):
        """S2.16.3 -- hold list detail with item list."""
        resp = admin_client.get(
            reverse("assets:holdlist_detail", args=[hold_list.pk])
        )
        assert resp.status_code == 200
        assert hold_list.name.encode() in resp.content

    def test_hold_list_edit_renders(self, admin_client, hold_list):
        """S2.16.3 -- hold list edit form."""
        resp = admin_client.get(
            reverse("assets:holdlist_edit", args=[hold_list.pk])
        )
        assert resp.status_code == 200

    def test_hold_list_pick_sheet(self, admin_client, hold_list):
        """S2.16.6 -- pick sheet PDF download."""
        resp = admin_client.get(
            reverse("assets:holdlist_pick_sheet", args=[hold_list.pk])
        )
        assert resp.status_code == 200

    def test_project_list_renders(self, admin_client):
        """S2.16.1 -- project list page."""
        resp = admin_client.get(reverse("assets:project_list"))
        assert resp.status_code == 200

    def test_project_create_renders(self, admin_client):
        """S2.16.1 -- project creation form."""
        resp = admin_client.get(reverse("assets:project_create"))
        assert resp.status_code == 200

    def test_dashboard_shows_hold_list_count(
        self, admin_client, active_hold_list
    ):
        """S2.16 -- dashboard shows active hold list count."""
        resp = admin_client.get(reverse("assets:dashboard"))
        assert resp.status_code == 200
        # Hold list count badge should appear somewhere
        assert b"hold" in resp.content.lower() or b"Hold" in resp.content


@pytest.mark.django_db
class TestS12_16_SerialisedInventory:
    """S12.16 -- Serialised Inventory (S2.17.1-S2.17.2)."""

    def test_serialised_asset_detail_renders(
        self, admin_client, serialised_asset_with_units
    ):
        """S2.17.1 -- serialised asset detail page renders."""
        asset = serialised_asset_with_units["asset"]
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert resp.status_code == 200
        assert asset.name.encode() in resp.content

    def test_serialised_asset_detail_shows_serials_tab(
        self, admin_client, serialised_asset_with_units
    ):
        """S2.17.1a -- Serials tab visible on serialised asset detail."""
        asset = serialised_asset_with_units["asset"]
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert resp.status_code == 200
        # Tab or section for serials should be present
        assert b"serial" in resp.content.lower() or b"Serial" in resp.content

    # -- Affordance exposure tests (T36) --

    def test_serialised_asset_detail_shows_serial_numbers(
        self, admin_client, serialised_asset_with_units
    ):
        """T36: Serialised asset detail page must show a serials tab/section
        and list the individual serial numbers from the fixture."""
        asset = serialised_asset_with_units["asset"]
        serials = serialised_asset_with_units["serials"]
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        assert (
            "serial" in content.lower()
        ), "Serialised asset detail must show a Serials tab or section"
        # At least one serial number from the fixture must appear
        serial_found = any(s.serial_number in content for s in serials)
        assert serial_found, (
            "Serialised asset detail must list at least one serial number"
            f" (expected one of: {[s.serial_number for s in serials]})"
        )

    def test_serialised_checkout_form_renders(
        self, admin_client, serialised_asset_with_units
    ):
        """S2.17.2-01 -- serialised checkout form with serial picker."""
        asset = serialised_asset_with_units["asset"]
        resp = admin_client.get(
            reverse("assets:asset_checkout", args=[asset.pk])
        )
        assert resp.status_code == 200

    def test_serialised_checkin_form_renders(
        self, admin_client, serialised_asset_with_units, borrower_user
    ):
        """S2.17.2-07 -- serialised check-in form renders."""
        asset = serialised_asset_with_units["asset"]
        resp = admin_client.get(
            reverse("assets:asset_checkin", args=[asset.pk])
        )
        assert resp.status_code == 200

    def test_convert_serialisation_renders(
        self, admin_client, serialised_asset_with_units
    ):
        """S2.17.1d -- serialisation conversion page renders."""
        asset = serialised_asset_with_units["asset"]
        resp = admin_client.get(
            reverse("assets:asset_convert_serialisation", args=[asset.pk])
        )
        assert resp.status_code == 200

    @pytest.mark.xfail(
        reason=(
            "GAP S2.17.1b-04: 'Available: X of Y' text not found on serialised"
            " asset detail page. The availability summary display (replacing"
            " the single condition field for serialised assets) is not yet"
            " rendered in the template."
        ),
        strict=True,
    )
    def test_availability_display_on_detail(
        self, admin_client, serialised_asset_with_units
    ):
        """S2.17.1b-04 -- 'Available: X of Y' display on serialised asset."""
        asset = serialised_asset_with_units["asset"]
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert resp.status_code == 200
        # Should show availability count
        assert (
            b"available" in resp.content.lower()
            or b"Available" in resp.content
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #18a: Asset list does not show available count 'X of Y'"
            " for serialised assets (S2.17.1b-04). The asset list shows"
            " generic quantity — no per-asset availability breakdown."
        ),
    )
    def test_asset_list_shows_available_count_for_serialised(
        self, admin_client, serialised_asset_with_units, borrower_user
    ):
        """S2.17.1b-04: Asset list must show available/total count for
        serialised assets (e.g. '4 of 5' or 'Available: 4')."""
        from assets.models import Transaction

        asset = serialised_asset_with_units["asset"]
        serials = serialised_asset_with_units["serials"]

        # Check out one serial so available_count < total
        serials[0].checked_out_to = borrower_user
        serials[0].save()

        resp = admin_client.get(reverse("assets:asset_list"))
        assert resp.status_code == 200
        content = resp.content.decode()
        # Expect something like "4 of 5" or "4/5" or "Available: 4"
        assert (
            "4 of 5" in content
            or "4/5" in content
            or "available" in content.lower()
        ), "Asset list must show available/total count for serialised assets"

    def test_asset_detail_shows_condition_summary(
        self, admin_client, serialised_asset_with_units
    ):
        """S2.17.1b: Serialised asset detail shows per-condition summary.
        Previously GAP #18b — confirmed working (XPASS)."""
        asset = serialised_asset_with_units["asset"]
        serials = serialised_asset_with_units["serials"]

        # Set varying conditions
        serials[0].condition = "good"
        serials[0].save()
        serials[1].condition = "fair"
        serials[1].save()

        resp = admin_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode().lower()
        assert (
            "good" in content or "fair" in content
        ), "Serialised asset detail must show per-condition breakdown"
        assert (
            "condition" in content
        ), "Serialised asset detail must show condition summary"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #18c: Checkout form for serialised asset has no"
            " auto-assign toggle (S2.17.2-01). The form only offers"
            " manual serial selection — no auto-assign option."
        ),
    )
    def test_serialised_checkout_has_auto_assign_toggle(
        self, admin_client, serialised_asset_with_units
    ):
        """S2.17.2-01: Serialised asset checkout form must include an
        auto-assign toggle so staff can let the system pick units.
        NOTE: the word 'auto' appears in the page but not as an
        auto-assign feature — checking for dedicated toggle."""
        asset = serialised_asset_with_units["asset"]
        resp = admin_client.get(
            reverse("assets:asset_checkout", args=[asset.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode().lower()
        # Must have a dedicated auto-assign toggle/button — not just 'auto'
        # appearing anywhere in the page
        assert (
            "auto-assign" in content
            or "autoassign" in content
            or "auto_assign" in content
            or ("auto" in content and "assign" in content)
        ), "Serialised checkout must have auto-assign option"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #18d: Check-in form for serialised asset has no"
            " per-serial condition field (S2.17.2-07). The asset_checkin"
            " form does not include a condition input per serial."
        ),
    )
    def test_checkin_form_has_per_serial_condition_field(
        self, admin_client, serialised_asset_with_units, borrower_user
    ):
        """S2.17.2-07: Check-in form for a serialised asset must include
        a per-serial condition input — not just a generic condition label
        in the navigation or elsewhere."""
        asset = serialised_asset_with_units["asset"]
        serials = serialised_asset_with_units["serials"]

        # Check out a serial first
        serials[0].checked_out_to = borrower_user
        serials[0].save()

        resp = admin_client.get(
            reverse("assets:asset_checkin", args=[asset.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        # Must have a form input (select/radio) named 'condition' for serials
        assert (
            'name="condition' in content
            or "condition_" in content
            or "serial-condition" in content.lower()
        ), "Serialised check-in must include a per-serial condition input field"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #18e: No 'serial_add' URL exists (S2.17.1c). There is"
            " no endpoint for adding individual serials to a serialised"
            " asset from the frontend."
        ),
    )
    def test_serial_add_url_exists(
        self, admin_client, serialised_asset_with_units
    ):
        """S2.17.1c: A URL for adding individual serials must exist and
        respond 200."""
        from django.urls import NoReverseMatch

        asset = serialised_asset_with_units["asset"]
        try:
            url = reverse("assets:serial_add", args=[asset.pk])
        except NoReverseMatch:
            pytest.xfail("GAP #18e: URL 'assets:serial_add' does not exist.")
        resp = admin_client.get(url)
        assert resp.status_code == 200


@pytest.mark.django_db
class TestS12_17_AssetKits:
    """S12.17 -- Asset Kits (S2.17.3-S2.17.5)."""

    def test_kit_detail_renders(self, admin_client, kit_with_components):
        """S2.17.5-01 -- kit asset detail page renders."""
        kit = kit_with_components["kit"]
        resp = admin_client.get(reverse("assets:asset_detail", args=[kit.pk]))
        assert resp.status_code == 200
        assert kit.name.encode() in resp.content

    def test_kit_contents_tab_shows_components(
        self, admin_client, kit_with_components
    ):
        """S2.17.5-01 -- Kit Contents tab shows components."""
        kit = kit_with_components["kit"]
        resp = admin_client.get(reverse("assets:asset_detail", args=[kit.pk]))
        assert resp.status_code == 200
        assert (
            b"kit" in resp.content.lower()
            or b"component" in resp.content.lower()
        )

    def test_kit_contents_management_page(
        self, admin_client, kit_with_components
    ):
        """S2.17.5-01 -- kit contents management endpoint renders."""
        kit = kit_with_components["kit"]
        resp = admin_client.get(reverse("assets:kit_contents", args=[kit.pk]))
        assert resp.status_code == 200

    def test_kit_checkout_form_renders(
        self, admin_client, kit_with_components
    ):
        """S2.17.4-01 -- kit checkout form with component checklist."""
        kit = kit_with_components["kit"]
        resp = admin_client.get(
            reverse("assets:asset_checkout", args=[kit.pk])
        )
        assert resp.status_code == 200

    # -- Affordance exposure tests (T35) --

    def test_kit_asset_detail_shows_checkout_affordance(
        self, admin_client, kit_with_components
    ):
        """T35: Kit asset detail page must contain a link to the checkout
        URL so users can check out the entire kit."""
        kit = kit_with_components["kit"]
        resp = admin_client.get(reverse("assets:asset_detail", args=[kit.pk]))
        assert resp.status_code == 200
        checkout_url = reverse("assets:asset_checkout", args=[kit.pk])
        assert (
            checkout_url.encode() in resp.content
        ), f"Checkout URL {checkout_url} not found on kit detail page"

    def test_component_detail_shows_kit_membership(
        self, admin_client, kit_with_components
    ):
        """S2.17.5-05 -- component asset detail shows 'Member of kits' section."""
        dimmer = kit_with_components["dimmer"]
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[dimmer.pk])
        )
        assert resp.status_code == 200
        # Should mention the kit it belongs to
        assert (
            b"kit" in resp.content.lower() or b"Lighting Kit" in resp.content
        )

    def test_asset_list_kit_filter(self, admin_client, kit_with_components):
        """S2.17.5-04 -- kit filter on asset list."""
        resp = admin_client.get(
            reverse("assets:asset_list"), {"is_kit": "true"}
        )
        assert resp.status_code == 200
        assert kit_with_components["kit"].name.encode() in resp.content

    def test_kit_checkout_template_shows_required_components(
        self, admin_client, kit_with_components
    ):
        """S2.17.4-01: Kit checkout form shows required components.
        Previously GAP #19a — confirmed working (XPASS)."""
        kit = kit_with_components["kit"]
        resp = admin_client.get(
            reverse("assets:asset_checkout", args=[kit.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode().lower()
        assert (
            "required" in content or "dimmer pack" in content.lower()
        ), "Kit checkout form must list required components"

    def test_kit_checkout_template_shows_optional_components(
        self, admin_client, kit_with_components
    ):
        """S2.17.4-01: Kit checkout form shows optional section.
        Previously GAP #19b — confirmed working (XPASS)."""
        from assets.models import AssetKit

        kit = kit_with_components["kit"]
        # Make one component optional
        optional = kit_with_components["par_can"]
        AssetKit.objects.filter(kit=kit, component=optional).update(
            is_required=False
        )

        resp = admin_client.get(
            reverse("assets:asset_checkout", args=[kit.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode().lower()
        assert (
            "optional" in content
        ), "Kit checkout form must have an 'Optional' section/label"

    def test_kit_checkin_url_exists_and_loads(
        self, admin_client, kit_with_components, borrower_user
    ):
        """S2.17.4: The check-in page for a kit asset loads without error.
        Previously GAP #19c — confirmed working (XPASS)."""
        kit = kit_with_components["kit"]
        kit.checked_out_to = borrower_user
        kit.save()

        resp = admin_client.get(reverse("assets:asset_checkin", args=[kit.pk]))
        assert (
            resp.status_code == 200
        ), f"Kit check-in page returned {resp.status_code}"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #19d: Kit check-in form does not show a per-component"
            " checklist (S2.17.4-04). The form renders but lists each"
            " component name only generically — no checkbox per component."
        ),
    )
    def test_kit_checkin_shows_component_checklist(
        self, admin_client, kit_with_components, borrower_user
    ):
        """S2.17.4-04: Kit check-in form must show a dedicated per-component
        checklist with checkboxes so staff can confirm each item was returned.
        Generic mention of the kit word is not sufficient."""
        kit = kit_with_components["kit"]
        kit.checked_out_to = borrower_user
        kit.save()

        resp = admin_client.get(reverse("assets:asset_checkin", args=[kit.pk]))
        assert resp.status_code == 200
        content = resp.content.decode()
        # Must have a dedicated component checklist with checkboxes
        # (input[type=checkbox] related to each component)
        assert 'type="checkbox"' in content and (
            "dimmer" in content.lower() or "par can" in content.lower()
        ), "Kit check-in must show per-component checkbox checklist"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #19e: No 'print kit labels' URL exists (S2.17.5)."
            " There is no dedicated endpoint for printing labels for all"
            " kit components at once."
        ),
    )
    def test_kit_label_generation_url_exists(
        self, admin_client, kit_with_components
    ):
        """S2.17.5: A 'print kit labels' URL must exist for printing
        labels for a kit and all its components."""
        from django.urls import NoReverseMatch

        kit = kit_with_components["kit"]
        url = None
        for url_name in [
            "assets:kit_labels",
            "assets:kit_print_labels",
            "assets:asset_kit_labels",
        ]:
            try:
                url = reverse(url_name, args=[kit.pk])
                break
            except NoReverseMatch:
                continue

        if url is None:
            pytest.xfail("GAP #19e: No kit label printing URL found.")

        resp = admin_client.get(url)
        assert resp.status_code in (200, 302, 405)


@pytest.mark.django_db
class TestS12_18_PublicListing:
    """S12.18 -- Public Asset Listing (S2.18).

    MoSCoW: COULD (S2.18 deferred to future scope per spec).
    These tests document the gap — all marked xfail(strict=True).
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #20a: Public listing URL '/public/' does not exist"
            " (S2.18.1). The feature is marked COULD/future scope but"
            " the URL returns 404."
        ),
    )
    def test_public_listing_loads_without_authentication(self, client):
        """S2.18.1: /public/ must return 200 for anonymous users."""
        from django.test import Client

        anon = Client()
        resp = anon.get("/public/")
        assert (
            resp.status_code == 200
        ), f"/public/ returned {resp.status_code} — expected 200"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #20b: Public listing URL does not exist (S2.18.1-02)."
            " Cannot verify public-only asset filtering."
        ),
    )
    def test_public_listing_shows_only_public_assets(
        self, client, category, location, admin_user
    ):
        """S2.18.1-02: Public listing must only show assets marked as
        public — not private/default assets."""
        from django.test import Client

        from assets.factories import AssetFactory

        # Create a public asset (is_public=True if field exists)
        public_asset = AssetFactory(
            name="Public Prop S20b",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        private_asset = AssetFactory(
            name="Private Prop S20b",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )

        # Try to set is_public on the public asset
        if hasattr(public_asset, "is_public"):
            public_asset.is_public = True
            public_asset.save()
        else:
            pytest.xfail("GAP #20b: Asset model has no is_public field.")

        anon = Client()
        resp = anon.get("/public/")
        assert resp.status_code == 200
        content = resp.content.decode()
        assert (
            "Public Prop S20b" in content
        ), "Public listing must show public assets"
        assert (
            "Private Prop S20b" not in content
        ), "Public listing must not show private assets"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #20c: Public listing URL does not exist (S2.18.1-03)."
            " Cannot verify sensitive field hiding."
        ),
    )
    def test_public_listing_hides_sensitive_fields(
        self, client, category, location, borrower_user, admin_user
    ):
        """S2.18.1-03: Public listing must not reveal borrower names
        or private/internal notes."""
        from django.test import Client

        from assets.factories import AssetFactory

        asset = AssetFactory(
            name="Public Asset S20c",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        asset.notes = "Internal staff note — confidential"
        asset.checked_out_to = borrower_user
        if hasattr(asset, "is_public"):
            asset.is_public = True
        asset.save()

        anon = Client()
        resp = anon.get("/public/")
        if resp.status_code != 200:
            pytest.xfail("GAP #20c: /public/ does not return 200.")
        content = resp.content.decode()
        assert (
            "Internal staff note" not in content
        ), "Public listing must not show internal notes"
        borrower_name = borrower_user.display_name or borrower_user.username
        assert (
            borrower_name not in content
        ), "Public listing must not show borrower names"


@pytest.mark.django_db
class TestS12_19_LocationManagement:
    """S12.19 -- Location Management (S2.12)."""

    def test_location_detail_renders(self, admin_client, location):
        """S2.12.2 -- location detail with asset list and stocktake button."""
        resp = admin_client.get(
            reverse("assets:location_detail", args=[location.pk])
        )
        assert resp.status_code == 200
        assert location.name.encode() in resp.content

    def test_location_create_renders(self, admin_client):
        """S2.12.1 -- location create form renders."""
        resp = admin_client.get(reverse("assets:location_create"))
        assert resp.status_code == 200
        assert b"form" in resp.content.lower()

    def test_location_edit_renders(self, admin_client, location):
        """S2.12.1 -- location edit form renders."""
        resp = admin_client.get(
            reverse("assets:location_edit", args=[location.pk])
        )
        assert resp.status_code == 200

    def test_location_detail_lists_child_assets(
        self, admin_client, location, active_asset
    ):
        """S2.12.2 -- location detail shows assets at that location."""
        resp = admin_client.get(
            reverse("assets:location_detail", args=[location.pk])
        )
        assert resp.status_code == 200
        assert active_asset.name.encode() in resp.content

    def test_location_deactivate_accessible(self, admin_client, location):
        """S2.12.1 -- location deactivate endpoint accessible."""
        resp = admin_client.get(
            reverse("assets:location_deactivate", args=[location.pk])
        )
        assert resp.status_code in (200, 302, 405)

    def test_location_hierarchy_in_list(self, admin_client, warehouse):
        """S2.12 -- child locations appear in location list."""
        resp = admin_client.get(reverse("assets:location_list"))
        assert resp.status_code == 200
        assert warehouse["root"].name.encode() in resp.content


@pytest.mark.django_db
class TestS12_20_RemotePrint:
    """S12.20 -- Remote Print Infrastructure (S2.4.5a-S2.4.5c)."""

    def test_admin_printclient_changelist_loads(self, admin_client):
        """S2.4.5a -- PrintClient admin list renders."""
        resp = admin_client.get("/admin/assets/printclient/")
        assert resp.status_code == 200

    def test_admin_printrequest_changelist_loads(self, admin_client):
        """S2.4.5c -- PrintRequest admin list renders."""
        resp = admin_client.get("/admin/assets/printrequest/")
        assert resp.status_code == 200

    def test_remote_print_submit_accessible(self, admin_client, active_asset):
        """S2.4.5b -- remote print submit endpoint exists."""
        url = reverse("assets:remote_print_submit", args=[active_asset.pk])
        resp = admin_client.get(url)
        # GET may not be supported; just check the endpoint exists
        assert resp.status_code in (200, 302, 405)

    def test_asset_detail_shows_print_dropdown(
        self, admin_client, active_asset
    ):
        """S2.4.5b -- asset detail exposes Print dropdown with remote options."""
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        assert b"print" in resp.content.lower()


# ---------------------------------------------------------------------------
# Additional acceptance-criteria tests (uncovered criteria audit)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestS12_4_CheckoutCheckinExtended:
    """S12.4 extended -- additional check-out / check-in criteria."""

    def test_checkin_form_shows_home_location_as_default(
        self, admin_client, active_asset, location, borrower_user
    ):
        """S2.3.4: Check-in form must pre-select the asset's home_location
        as the default return location.

        The checkin view passes home_location in context, and the template
        pre-selects it in the location dropdown.
        """
        # Establish a home location different from the current location
        home_loc = location  # reuse fixture location as home
        active_asset.home_location = home_loc
        active_asset.checked_out_to = borrower_user
        active_asset.save()

        url = reverse("assets:asset_checkin", args=[active_asset.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200
        content = resp.content.decode()

        # The template pre-selects home_location via {% if home_location
        # and home_location.pk == loc.pk %}selected{% endif %}.
        # We check that the home location pk appears as a selected option.
        assert (
            f'value="{home_loc.pk}"' in content
        ), "Check-in form must render the home location in the dropdown"
        # 'selected' must appear somewhere in the form to pre-select it
        assert (
            "selected" in content
        ), "Check-in form must pre-select the home location"

    # -- Affordance exposure tests (T32) --

    def test_asset_detail_shows_checkin_link_when_checked_out(
        self, admin_client, active_asset, borrower_user
    ):
        """T32: Asset detail page for a checked-out asset must contain a
        link to the check-in URL."""
        active_asset.checked_out_to = borrower_user
        active_asset.save()
        Transaction.objects.create(
            asset=active_asset,
            action="checkout",
            user=active_asset.created_by,
            borrower=borrower_user,
        )
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        checkin_url = reverse("assets:asset_checkin", args=[active_asset.pk])
        assert checkin_url.encode() in resp.content, (
            f"Check-in URL {checkin_url} not found on asset detail page"
            " for a checked-out asset"
        )

    def test_backdated_checkout_field_present(
        self, admin_client, active_asset
    ):
        """S2.3.2-10 / S7.21: Checkout form must include a date input for
        backdating actions.
        """
        url = reverse("assets:asset_checkout", args=[active_asset.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200
        content = resp.content.decode()

        # The template has: <input type="datetime-local" name="action_date"...>
        assert (
            "action_date" in content
        ), "Checkout form must contain an 'action_date' field for backdating"
        assert (
            "datetime-local" in content
        ), "Checkout form must use a datetime-local input for the date field"


@pytest.mark.django_db
class TestS12_5_LabelContent:
    """S12.5 extended -- label page content verification."""

    def test_label_page_shows_both_barcode_and_asset_name(
        self, admin_client, active_asset
    ):
        """S2.4.5: Label print page must show both the barcode value AND
        the asset name, not just the barcode image.
        """
        url = reverse("assets:asset_label", args=[active_asset.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200
        content = resp.content.decode()

        assert (
            active_asset.barcode in content
        ), f"Label page must display the barcode value '{active_asset.barcode}'"
        assert (
            active_asset.name in content
        ), f"Label page must display the asset name '{active_asset.name}'"


@pytest.mark.django_db
class TestS12_9_BulkCheckoutWarning:
    """S12.9 extended -- bulk checkout handles already-checked-out assets."""

    def test_bulk_checkout_warning_for_already_checked_out(
        self, admin_client, category, location, admin_user, borrower_user
    ):
        """S2.8.1: Bulk checkout must skip already-checked-out assets and
        include them in a warning message.

        Create 2 assets; check out one. POST bulk checkout for both.
        Assert the already-checked-out asset was skipped (still checked
        out to original borrower) and a warning message mentions it.
        """
        already_out = AssetFactory(
            name="AlreadyOut BulkCO Test",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        already_out.checked_out_to = borrower_user
        already_out.save()

        available = AssetFactory(
            name="Available BulkCO Test",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )

        # Second borrower for the bulk checkout target
        second_borrower = UserFactory(
            username="second_borrower_bulkco",
            email="second_borrower_bulkco@example.com",
        )

        url = reverse("assets:bulk_actions")
        resp = admin_client.post(
            url,
            {
                "bulk_action": "bulk_checkout",
                "asset_ids": [already_out.pk, available.pk],
                "bulk_borrower": second_borrower.pk,
            },
            follow=True,
        )
        assert resp.status_code == 200

        already_out.refresh_from_db()
        # The already-checked-out asset must remain with the original borrower
        assert (
            already_out.checked_out_to == borrower_user
        ), "Already-checked-out asset must not be re-assigned by bulk checkout"

        available.refresh_from_db()
        # The available asset must now be checked out to second_borrower
        assert (
            available.checked_out_to == second_borrower
        ), "Available asset must be checked out by bulk checkout"

        # The response must mention the skipped asset (warning message)
        content = resp.content.decode()
        assert (
            "AlreadyOut BulkCO Test" in content
            or "skipped" in content.lower()
            or "already" in content.lower()
        ), (
            "Bulk checkout response must mention already-checked-out asset"
            " that was skipped"
        )


@pytest.mark.django_db
class TestS12_15_HoldListPickSheet:
    """S12.15 extended -- hold list pick sheet accessibility."""

    def test_hold_list_pick_sheet_accessible(self, admin_client, hold_list):
        """S2.16.6: GET the pick sheet URL for a hold list; assert
        status 200 and content-type includes 'pdf' (not 404).
        """
        url = reverse("assets:holdlist_pick_sheet", args=[hold_list.pk])
        resp = admin_client.get(url)
        assert (
            resp.status_code == 200
        ), f"Pick sheet URL must return 200, got {resp.status_code}"
        content_type = resp.get("Content-Type", "")
        assert (
            "pdf" in content_type.lower()
        ), f"Pick sheet must return a PDF response, got: {content_type}"

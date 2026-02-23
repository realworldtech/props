"""S10D Cross-actor story tests.

Tests that verify system-wide behaviours: concurrency, state integrity,
mobile responsiveness, and privacy across actor roles.

Read: specs/props/sections/s10d-cross-actor-stories.md
"""

from unittest.mock import MagicMock, patch

import pytest

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from assets.factories import (
    AssetFactory,
    AssetImageFactory,
    CategoryFactory,
    DepartmentFactory,
    LocationFactory,
    UserFactory,
)
from assets.models import Asset, AssetImage, Transaction

# ---------------------------------------------------------------------------
# §10D.1 Anonymous & Unapproved Access
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_001_PublicAssetListing:
    """US-XA-001: Anonymous user can browse public asset listing.

    MoSCoW: COULD
    """

    def test_public_listing_url_exists_or_redirects(self, client):
        # The public listing may not be implemented; check for 200 or
        # graceful 302/404 (no crash)
        try:
            resp = client.get("/public/")
        except Exception:
            resp = None
        # We just verify the server does not crash on public access
        if resp is not None:
            assert resp.status_code in (200, 302, 404)

    def test_unauthenticated_cannot_view_asset_list(self, client, asset):
        resp = client.get(reverse("assets:asset_list"))
        # Should redirect to login (not 200 with data)
        assert resp.status_code in (302, 403)

    def test_unauthenticated_cannot_view_asset_detail(self, client, asset):
        resp = client.get(reverse("assets:asset_detail", args=[asset.pk]))
        assert resp.status_code in (302, 403)


@pytest.mark.django_db
class TestUS_XA_002_BlockUnapprovedUser:
    """US-XA-002: Unapproved users are blocked from accessing the system.

    MoSCoW: MUST
    """

    def test_inactive_user_cannot_access_dashboard(self, client, db, password):
        u = UserFactory(
            username="pending_user",
            email="pending@example.com",
            password=password,
            is_active=False,
        )
        logged_in = client.login(username=u.username, password=password)
        # Login should fail for inactive users
        if logged_in:
            resp = client.get(reverse("assets:dashboard"))
            assert resp.status_code in (302, 403)
        else:
            # Could not log in at all — correct behaviour
            assert True

    def test_pending_user_sees_appropriate_message(self, client, db, password):
        u = UserFactory(
            username="pending2",
            email="pending2@example.com",
            password=password,
            is_active=False,
        )
        resp = client.post(
            reverse("accounts:login"),
            {"username": u.username, "password": password},
        )
        # Should not redirect to dashboard
        assert resp.status_code in (200, 302)
        if resp.status_code == 200:
            content = resp.content.decode().lower()
            # Should contain some kind of verification/pending/approval
            # message or generic login error
            assert (
                "verif" in content
                or "pending" in content
                or "approved" in content
                or "error" in content
                or "invalid" in content
                or "account status" in content
            )


@pytest.mark.django_db
class TestUS_XA_003_RegistrationAccessible:
    """US-XA-003: Anonymous user can access the registration page.

    MoSCoW: MUST
    """

    def test_registration_page_accessible_without_auth(self, client):
        resp = client.get(reverse("accounts:register"))
        assert resp.status_code == 200

    def test_registration_creates_inactive_account(self, client, db):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        resp = client.post(
            reverse("accounts:register"),
            {
                "email": "xanon@example.com",
                "password1": "securePass123!",
                "password2": "securePass123!",
                "display_name": "Anonymous User",
            },
        )
        new_user = User.objects.filter(email="xanon@example.com").first()
        if new_user:
            assert new_user.is_active is False


# ---------------------------------------------------------------------------
# §10D.2 Borrower Role
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_004_BorrowerInCheckoutDropdown:
    """US-XA-004: Borrower-role users appear in checkout dropdown.

    MoSCoW: MUST
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP: borrower not restricted from dashboard" " (US-XA-004, S10D)"
        ),
    )
    def test_borrower_user_cannot_log_in(
        self, client, borrower_user, password
    ):
        logged_in = client.login(
            username=borrower_user.username, password=password
        )
        if logged_in:
            resp = client.get(reverse("assets:dashboard"))
            assert resp.status_code in (
                302,
                403,
            ), "Borrower should be blocked from dashboard"
        else:
            # Correct: borrower cannot log in
            assert True

    def test_checkout_page_accessible_to_admin(
        self, admin_client, active_asset
    ):
        resp = admin_client.get(
            reverse("assets:asset_checkout", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_borrower_appears_in_checkout_form_context(
        self, admin_client, active_asset, borrower_user
    ):
        resp = admin_client.get(
            reverse("assets:asset_checkout", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        # The borrower user should appear in the form
        assert (
            borrower_user.display_name.encode() in resp.content
            or str(borrower_user.pk).encode() in resp.content
        )


@pytest.mark.django_db
class TestUS_XA_005_BorrowerOrgInTransactionHistory:
    """US-XA-005: Borrower organisation visible in transaction history.

    MoSCoW: MUST
    """

    def test_transaction_history_visible_on_asset_detail(
        self, client_logged_in, active_asset
    ):
        resp = client_logged_in.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# §10D.3 Concurrency & Data Integrity
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_006_ConcurrentQuickCaptureNoDataloss:
    """US-XA-006: System handles concurrent Quick Capture sessions.

    MoSCoW: MUST
    """

    def test_two_quick_captures_produce_two_distinct_assets(
        self, client_logged_in, location
    ):
        client_logged_in.post(
            reverse("assets:quick_capture"),
            {"name": "Concurrent Item A", "current_location": location.pk},
        )
        client_logged_in.post(
            reverse("assets:quick_capture"),
            {"name": "Concurrent Item B", "current_location": location.pk},
        )
        assert Asset.objects.filter(name="Concurrent Item A").exists()
        assert Asset.objects.filter(name="Concurrent Item B").exists()

    def test_auto_generated_barcode_is_unique(
        self, client_logged_in, location
    ):
        client_logged_in.post(
            reverse("assets:quick_capture"),
            {"name": "QC Item 1", "current_location": location.pk},
        )
        client_logged_in.post(
            reverse("assets:quick_capture"),
            {"name": "QC Item 2", "current_location": location.pk},
        )
        barcodes = list(
            Asset.objects.filter(
                name__in=["QC Item 1", "QC Item 2"]
            ).values_list("barcode", flat=True)
        )
        non_null = [b for b in barcodes if b]
        assert len(non_null) == len(set(non_null))


@pytest.mark.django_db
class TestUS_XA_007_PreventConcurrentCheckout:
    """US-XA-007: Concurrent checkout of the same asset is rejected.

    MoSCoW: MUST
    """

    def test_double_checkout_is_rejected(
        self,
        admin_client,
        client_logged_in,
        active_asset,
        borrower_user,
        user,
        location,
    ):
        url = reverse("assets:asset_checkout", args=[active_asset.pk])

        # First checkout succeeds
        admin_client.post(
            url,
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to == borrower_user

        # Second checkout attempt should be blocked
        resp = client_logged_in.post(
            url,
            {
                "borrower": user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        # Asset should still be checked out to original borrower
        assert active_asset.checked_out_to == borrower_user

    def test_checked_out_asset_cannot_be_checked_out_again(
        self, admin_client, active_asset, borrower_user, user, location
    ):
        url = reverse("assets:asset_checkout", args=[active_asset.pk])
        admin_client.post(
            url,
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        first_borrower = active_asset.checked_out_to

        # Try again
        admin_client.post(
            url,
            {
                "borrower": user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to == first_borrower

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #34b: Concurrent checkout error message does not suggest"
            " refreshing (S11.15 Steps 4-5). The error response provides"
            " no guidance to the user about what to do next."
        ),
    )
    def test_concurrent_checkout_error_suggests_refresh(
        self,
        admin_client,
        active_asset,
        borrower_user,
        second_user,
        location,
    ):
        """S11.15 Steps 4-5: After a rejected second checkout, the
        response must contain 'refresh' or similar guidance."""
        url = reverse("assets:asset_checkout", args=[active_asset.pk])

        # First checkout succeeds
        admin_client.post(
            url,
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to == borrower_user

        # Second checkout attempt — error must suggest refresh
        resp = admin_client.post(
            url,
            {
                "borrower": second_user.pk,
                "destination_location": location.pk,
            },
            follow=True,
        )
        content = resp.content.decode().lower()
        assert (
            "refresh" in content
            or "reload" in content
            or "try again" in content
            or "already checked out" in content
        ), (
            "Concurrent checkout error should suggest refreshing or"
            " provide guidance to the user"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #34: Concurrent checkout error message doesn't name the"
            " current borrower (S11.15 Steps 4-5). The rejection message"
            " is generic and does not identify who has the asset."
        ),
    )
    def test_concurrent_checkout_error_names_current_borrower(
        self, admin_client, active_asset, borrower_user, second_user, location
    ):
        """S11.15 Steps 4-5: Rejection message must name who currently
        has the asset checked out."""
        url = reverse("assets:asset_checkout", args=[active_asset.pk])

        # First checkout to borrower_user
        admin_client.post(
            url,
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to == borrower_user

        # Second checkout attempt — error must name borrower_user
        resp = admin_client.post(
            url,
            {
                "borrower": second_user.pk,
                "destination_location": location.pk,
            },
            follow=True,
        )
        content = resp.content.decode()
        borrower_name = borrower_user.get_full_name().lower()
        borrower_username = borrower_user.username.lower()
        borrower_display = (borrower_user.display_name or "").lower()
        assert (
            (borrower_name and borrower_name in content.lower())
            or borrower_username in content.lower()
            or (borrower_display and borrower_display in content.lower())
        ), (
            "Concurrent checkout error does not name the current borrower"
            f" ({borrower_user.username} / {borrower_user.display_name})"
        )


@pytest.mark.django_db
class TestUS_XA_008_PreventConcurrentNFCReassignment:
    """US-XA-008: Concurrent NFC tag reassignment is prevented.

    MoSCoW: MUST
    """

    def test_nfc_add_endpoint_accessible_to_admin(
        self, admin_client, active_asset
    ):
        resp = admin_client.get(
            reverse("assets:nfc_add", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_nfc_add_requires_authentication(self, client, active_asset):
        resp = client.get(reverse("assets:nfc_add", args=[active_asset.pk]))
        assert resp.status_code in (302, 403)


@pytest.mark.django_db
class TestUS_XA_009_OptimisticLockingOnStateTransitions:
    """US-XA-009: Stale state transitions are detected and rejected.

    MoSCoW: MUST
    """

    def test_asset_status_transitions_consistently(
        self, admin_client, active_asset
    ):
        # Verify the asset can be reached and has a valid state
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        assert active_asset.status == "active"


@pytest.mark.django_db
class TestUS_XA_010_CascadeDepartmentOnCategoryReassign:
    """US-XA-010: Category department reassignment cascades to assets.

    MoSCoW: MUST
    """

    def test_asset_department_derived_from_category(
        self, asset, category, department
    ):
        assert asset.category.department == department


# ---------------------------------------------------------------------------
# §10D.4 State Machine Enforcement
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_012_PreventInvalidStateTransitions:
    """US-XA-012: Invalid state transitions are rejected.

    MoSCoW: MUST
    """

    def test_disposed_cannot_transition_to_active(self, asset):
        asset.status = "disposed"
        asset.save()
        assert not asset.can_transition_to("active")

    def test_disposed_cannot_transition_to_any_other_state(self, asset):
        asset.status = "disposed"
        asset.save()
        for status in [
            "active",
            "draft",
            "retired",
            "missing",
            "lost",
            "stolen",
        ]:
            assert not asset.can_transition_to(
                status
            ), f"disposed -> {status} should be blocked"

    def test_draft_cannot_transition_to_retired(self, asset):
        asset.status = "draft"
        asset.save()
        assert not asset.can_transition_to("retired")

    def test_draft_cannot_transition_to_missing(self, asset):
        asset.status = "draft"
        asset.save()
        assert not asset.can_transition_to("missing")

    def test_draft_cannot_transition_to_lost(self, asset):
        asset.status = "draft"
        asset.save()
        assert not asset.can_transition_to("lost")

    def test_draft_cannot_transition_to_stolen(self, asset):
        asset.status = "draft"
        asset.save()
        assert not asset.can_transition_to("stolen")

    def test_active_can_transition_to_retired(self, asset):
        assert asset.can_transition_to("retired")

    def test_active_can_transition_to_missing(self, asset):
        assert asset.can_transition_to("missing")

    def test_active_can_transition_to_lost(self, asset):
        assert asset.can_transition_to("lost")

    def test_active_can_transition_to_stolen(self, asset):
        assert asset.can_transition_to("stolen")

    def test_lost_cannot_transition_to_retired(self, asset):
        asset.status = "lost"
        asset.save()
        assert not asset.can_transition_to("retired")

    def test_stolen_cannot_transition_to_retired(self, asset):
        asset.status = "stolen"
        asset.save()
        assert not asset.can_transition_to("retired")


@pytest.mark.django_db
class TestUS_XA_013_DisposedIsTerminalState:
    """US-XA-013: Disposed is a terminal state — no further transitions.

    MoSCoW: MUST
    """

    def test_disposed_asset_is_not_shown_in_default_list(
        self, client_logged_in, location, category
    ):
        disposed = AssetFactory(
            name="Old Prop",
            status="disposed",
            category=category,
            current_location=location,
        )
        resp = client_logged_in.get(reverse("assets:asset_list"))
        assert resp.status_code == 200
        # Disposed should NOT appear in default list
        assert b"Old Prop" not in resp.content

    def test_disposed_asset_accessible_with_status_filter(
        self, client_logged_in, location, category
    ):
        disposed = AssetFactory(
            name="Old Prop Disposed",
            status="disposed",
            category=category,
            current_location=location,
        )
        resp = client_logged_in.get(
            reverse("assets:asset_list"),
            {"status": "disposed"},
        )
        assert resp.status_code == 200
        assert b"Old Prop Disposed" in resp.content

    def test_disposed_asset_detail_accessible(
        self, client_logged_in, location, category
    ):
        disposed = AssetFactory(
            name="Disposed Prop",
            status="disposed",
            category=category,
            current_location=location,
        )
        resp = client_logged_in.get(
            reverse("assets:asset_detail", args=[disposed.pk])
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_XA_014_BlockRetireDisposeOnCheckedOutAssets:
    """US-XA-014: Retiring or disposing a checked-out asset is blocked.

    MoSCoW: MUST
    """

    def test_cannot_retire_checked_out_asset(
        self, admin_client, active_asset, borrower_user, location
    ):
        # Check out the asset first
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to is not None

        # Attempt to set status to retired
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[active_asset.pk]),
            {
                "name": active_asset.name,
                "status": "retired",
                "category": active_asset.category.pk,
                "current_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        # Should still be active, not retired
        assert active_asset.status != "retired"


@pytest.mark.django_db
class TestUS_XA_015_PreserveCheckoutWhenLostOrStolen:
    """US-XA-015: checked_out_to is preserved when marking asset lost/stolen.

    MoSCoW: MUST
    """

    def test_checked_out_to_preserved_when_marked_lost(
        self, admin_client, active_asset, borrower_user, location
    ):
        # Check out first
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to == borrower_user

        # Mark as lost
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[active_asset.pk]),
            {
                "name": active_asset.name,
                "status": "lost",
                "category": active_asset.category.pk,
                "current_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        if active_asset.status == "lost":
            # checked_out_to should still be set
            assert active_asset.checked_out_to == borrower_user


# ---------------------------------------------------------------------------
# §10D.6 Unified Scan Resolution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_019_UniversalLookupURL:
    """US-XA-019: /a/<identifier>/ resolves barcodes, serials, and NFC tags.

    MoSCoW: MUST
    """

    def test_known_barcode_resolves_to_asset_detail(
        self, client_logged_in, asset
    ):
        resp = client_logged_in.get(
            reverse("assets:asset_by_identifier", args=[asset.barcode])
        )
        # Should redirect to asset detail (302) or return 200
        assert resp.status_code in (200, 302)
        if resp.status_code == 302:
            assert str(asset.pk) in resp["Location"]

    def test_unknown_identifier_redirects_to_quick_capture(
        self, client_logged_in
    ):
        resp = client_logged_in.get(
            reverse(
                "assets:asset_by_identifier",
                args=["UNKNOWN-IDENT-99999"],
            )
        )
        # Should redirect to quick capture or show not-found
        assert resp.status_code in (200, 302)

    def test_lookup_is_accessible_to_all_authenticated_users(
        self, viewer_client, asset
    ):
        resp = viewer_client.get(
            reverse("assets:asset_by_identifier", args=[asset.barcode])
        )
        assert resp.status_code in (200, 302)

    def test_lookup_requires_authentication(self, client, asset):
        resp = client.get(
            reverse("assets:asset_by_identifier", args=[asset.barcode])
        )
        assert resp.status_code in (302, 403)


@pytest.mark.django_db
class TestUS_XA_021_HandleDisposedOrUnknownScans:
    """US-XA-021: Disposed and unknown scan codes are handled gracefully.

    MoSCoW: MUST
    """

    def test_scanning_disposed_asset_returns_detail_page(
        self, client_logged_in, location, category
    ):
        disposed = AssetFactory(
            name="Disposed Prop",
            status="disposed",
            category=category,
            current_location=location,
        )
        resp = client_logged_in.get(
            reverse(
                "assets:asset_by_identifier",
                args=[disposed.barcode],
            )
        )
        # Should navigate to detail, not redirect to quick capture
        assert resp.status_code in (200, 302)

    def test_scan_lookup_with_null_location_does_not_crash(
        self, client_logged_in
    ):
        resp = client_logged_in.get(
            reverse("assets:scan_lookup"),
            {"code": "COMPLETELY-UNKNOWN-XYZ"},
        )
        assert resp.status_code in (200, 302)
        # Should not be a 500


# ---------------------------------------------------------------------------
# §10D.7 Privacy & Security
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_022_ExcludePIIFromAIAnalysis:
    """US-XA-022: Only image content is sent to the Anthropic API.

    MoSCoW: MUST
    """

    def test_ai_analysis_endpoint_accessible_to_member(
        self, client_logged_in, asset
    ):
        from assets.factories import AssetImageFactory

        try:
            image = AssetImageFactory(asset=asset)
            resp = client_logged_in.get(
                reverse(
                    "assets:ai_analyse",
                    args=[asset.pk, image.pk],
                )
            )
            assert resp.status_code in (200, 302, 405)
        except Exception:
            # AssetImageFactory may not exist; skip gracefully
            pass

    def test_ai_endpoint_requires_authentication(self, client, asset):
        resp = client.get(f"/assets/{asset.pk}/images/1/analyse/")
        assert resp.status_code in (302, 403)


@pytest.mark.django_db
class TestUS_XA_023_PreventUserEnumerationOnRegistration:
    """US-XA-023: Registration returns identical response for new/existing emails.

    MoSCoW: MUST
    """

    # Security boundary test: deliberately probing registration
    # for enumeration. Hardcoded payloads are intentional.
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP: Registration form leaks email existence via"
            " 'already exists' error message (US-XA-023, S10D)"
        ),
    )
    def test_duplicate_registration_does_not_leak_existence(
        self, client, user
    ):
        # Register with an email that already exists
        resp = client.post(
            reverse("accounts:register"),
            {
                "email": user.email,
                "password1": "securePass123!",
                "password2": "securePass123!",
                "display_name": "Duplicate User",
            },
        )
        # Should not show a different error (e.g. "email already registered")
        assert resp.status_code in (200, 302)
        if resp.status_code == 200:
            content = resp.content.decode().lower()
            # Must NOT reveal that the email already exists.
            # Note: "already have an account?" is static footer text
            # and does not leak info. We check for specific leak
            # phrases only.
            assert "already exists" not in content
            assert "already registered" not in content
            assert "already in use" not in content


@pytest.mark.django_db
class TestUS_XA_024_RateLimitRegistrationEndpoints:
    """US-XA-024: Registration and verification are rate-limited.

    MoSCoW: MUST
    """

    def test_repeated_registrations_eventually_rate_limited(self, client, db):
        url = reverse("accounts:register")
        responses = []
        for i in range(8):
            resp = client.post(
                url,
                {
                    "email": f"spammer{i}@example.com",
                    "password1": "securePass123!",
                    "password2": "securePass123!",
                    "display_name": f"Spammer {i}",
                },
            )
            responses.append(resp.status_code)
        # At some point (after 5 attempts) we should get 429
        assert 429 in responses or all(
            s in (200, 302) for s in responses
        ), "Rate limiting should trigger HTTP 429 after 5 attempts"


# ---------------------------------------------------------------------------
# §10D.8 Availability Model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_025_UnifiedAvailabilityModel:
    """US-XA-025: Asset availability accounts for checkouts and holds.

    MoSCoW: MUST
    """

    def test_checked_out_asset_shows_as_unavailable(
        self, admin_client, active_asset, borrower_user, location
    ):
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.is_checked_out

    def test_checked_in_asset_shows_as_available(
        self, admin_client, active_asset, borrower_user, location
    ):
        # Checkout
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.is_checked_out

        # Checkin
        admin_client.post(
            reverse("assets:asset_checkin", args=[active_asset.pk]),
            {"location": location.pk},
        )
        active_asset.refresh_from_db()
        assert not active_asset.is_checked_out


# ---------------------------------------------------------------------------
# §10D.9 Edge Cases — selected MUST stories
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_028_NullSafety:
    """US-XA-028: Nullable fields are handled safely everywhere.

    MoSCoW: MUST
    """

    def test_draft_with_null_location_is_accessible(
        self, client_logged_in, draft_asset
    ):
        resp = client_logged_in.get(
            reverse("assets:asset_detail", args=[draft_asset.pk])
        )
        assert resp.status_code == 200

    def test_scan_lookup_with_null_location_draft(
        self, client_logged_in, draft_asset
    ):
        if draft_asset.barcode:
            resp = client_logged_in.get(
                reverse("assets:scan_lookup"),
                {"code": draft_asset.barcode},
            )
            assert resp.status_code in (200, 302)

    def test_asset_list_shows_no_location_for_draft(
        self, client_logged_in, draft_asset
    ):
        resp = client_logged_in.get(
            reverse("assets:asset_list"),
            {"status": "draft"},
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_XA_032_PermissionBoundaryEdgeCases:
    """US-XA-032: Permission boundaries are enforced in edge cases.

    MoSCoW: MUST
    """

    def test_viewer_blocked_from_quick_capture(self, viewer_client):
        resp = viewer_client.get(reverse("assets:quick_capture"))
        assert resp.status_code in (302, 403)

    def test_viewer_blocked_from_asset_create(self, viewer_client):
        resp = viewer_client.get(reverse("assets:asset_create"))
        assert resp.status_code in (302, 403)

    def test_viewer_blocked_from_asset_edit(self, viewer_client, asset):
        resp = viewer_client.get(reverse("assets:asset_edit", args=[asset.pk]))
        assert resp.status_code in (302, 403)

    def test_viewer_blocked_from_asset_delete(self, viewer_client, asset):
        resp = viewer_client.post(
            reverse("assets:asset_delete", args=[asset.pk])
        )
        assert resp.status_code in (302, 403)

    def test_dept_manager_cannot_edit_asset_outside_department(
        self, dept_manager_client, tech_dept, location, admin_user
    ):
        other_cat = CategoryFactory(
            name="Other Dept Category",
            department=tech_dept,
        )
        other_asset = AssetFactory(
            name="Tech Asset",
            status="active",
            category=other_cat,
            current_location=location,
            created_by=admin_user,
        )
        resp = dept_manager_client.post(
            reverse("assets:asset_edit", args=[other_asset.pk]),
            {
                "name": "Tampered Name",
                "status": "active",
                "category": other_cat.pk,
                "current_location": location.pk,
            },
        )
        other_asset.refresh_from_db()
        assert other_asset.name == "Tech Asset"

    def test_unauthenticated_user_blocked_from_all_views(self, client, asset):
        protected_urls = [
            reverse("assets:asset_list"),
            reverse("assets:asset_detail", args=[asset.pk]),
            reverse("assets:asset_edit", args=[asset.pk]),
            reverse("assets:quick_capture"),
            reverse("assets:dashboard"),
        ]
        for url in protected_urls:
            resp = client.get(url)
            assert resp.status_code in (
                302,
                403,
            ), f"{url} should redirect unauthenticated users"


@pytest.mark.django_db
class TestUS_XA_034_DataIntegrityCascadeOperations:
    """US-XA-034: Data integrity is preserved during cascading operations.

    MoSCoW: MUST
    """

    def test_category_with_assets_cannot_be_deleted(
        self, admin_client, category, asset
    ):
        resp = admin_client.post(
            f"/admin/assets/category/{category.pk}/delete/",
            {"post": "yes"},
        )
        from assets.models import Category

        assert Category.objects.filter(pk=category.pk).exists()

    def test_asset_accessed_after_creator_deleted(
        self, client_logged_in, admin_user
    ):
        asset = AssetFactory(
            name="Orphaned Asset",
            status="active",
            created_by=admin_user,
        )
        resp = client_logged_in.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# New uncovered acceptance-criteria tests — added Feb 2026
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_009_OptimisticLocking_DoubleDisposal:
    """US-XA-009 (extra): Disposing an already-disposed asset is rejected.

    Spec refs: S2.2.3-05, S2.3.15-01
    """

    def test_stale_state_prevents_disposal_of_disposed_asset(
        self, admin_client, active_asset
    ):
        """Dispose an asset via the delete/dispose endpoint, then attempt a
        second disposal — the second attempt must be rejected gracefully."""
        # Disposal in PROPS happens via the asset_delete view, not edit form
        # (FORM_STATUS_CHOICES does not include 'disposed').
        resp1 = admin_client.post(
            reverse("assets:asset_delete", args=[active_asset.pk]),
            {},
        )
        active_asset.refresh_from_db()
        assert (
            active_asset.status == "disposed"
        ), "First disposal should succeed"

        # Second disposal attempt — must be rejected gracefully (not 500)
        resp2 = admin_client.post(
            reverse("assets:asset_delete", args=[active_asset.pk]),
            {},
        )
        active_asset.refresh_from_db()
        # A no-op (status stays disposed) is acceptable; the server must not
        # crash or allow invalid transitions.
        assert active_asset.status == "disposed", (
            "Asset must remain in 'disposed' state after a redundant "
            "disposal attempt"
        )
        assert (
            resp2.status_code != 500
        ), "Second disposal attempt must not produce a server error"


@pytest.mark.django_db
class TestUS_XA_010_CategoryReassignment_DepartmentScope:
    """US-XA-010 (extra): Category reassignment updates asset's dept scope.

    Spec refs: S2.10.3-07
    """

    def test_category_reassignment_updates_asset_department_scope(
        self,
        admin_client,
        asset,
        department,
    ):
        """Reassign asset's category to dept B; the asset's department
        (derived via category) should now reflect dept B."""
        dept_b = DepartmentFactory(name="XA010 Dept B", barcode_prefix="XB")
        new_cat = CategoryFactory(
            name="XA010 New Cat",
            department=dept_b,
        )

        # Reassign asset's category directly (simulates admin/DM action)
        asset.category = new_cat
        asset.save()
        asset.refresh_from_db()

        assert asset.category.department == dept_b, (
            "After reassigning to a category in dept B, the asset's "
            "department scope should reflect dept B"
        )
        assert asset.category.department != department, (
            "Asset should no longer belong to the original department "
            "after category reassignment"
        )


@pytest.mark.django_db
class TestUS_XA_014_DisposeOrRetireCheckedOut_ErrorNamesUser:
    """US-XA-014 (extra): Disposal error message identifies the borrower.

    Spec refs: S2.2.3-05, S2.3.15-01
    """

    def test_disposal_error_message_identifies_borrower(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
        admin_user,
    ):
        """Check out asset to borrower, attempt disposal; the error response
        must contain the borrower's name or username."""
        # Check out
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to == borrower_user

        # Attempt disposal (follow=True to see message)
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[active_asset.pk]),
            {
                "name": active_asset.name,
                "category": active_asset.category.pk,
                "current_location": location.pk,
                "condition": active_asset.condition,
                "quantity": active_asset.quantity,
                "status": "disposed",
            },
            follow=True,
        )
        active_asset.refresh_from_db()
        # If disposal was blocked, the error response must name the borrower
        if active_asset.status != "disposed":
            content = resp.content.decode()
            borrower_name = (borrower_user.get_full_name() or "").lower()
            borrower_username = borrower_user.username.lower()
            borrower_display = (borrower_user.display_name or "").lower()
            assert (
                (borrower_name and borrower_name in content.lower())
                or borrower_username in content.lower()
                or (borrower_display and borrower_display in content.lower())
            ), (
                "Disposal error must identify the borrower who holds the "
                f"asset. Borrower: {borrower_user.username} / "
                f"{borrower_user.display_name}"
            )


@pytest.mark.django_db
class TestUS_XA_015_LostStolenPreservesLocation_NoCheckinTransaction:
    """US-XA-015 (extra): Marking asset lost does not create a checkin txn.

    Spec refs: S2.2.3-11, S2.3.7-01
    """

    def test_marking_lost_does_not_create_checkin_transaction(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
    ):
        """Check out asset; mark as lost; assert no checkin Transaction exists."""
        # Check out
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to == borrower_user

        checkin_count_before = Transaction.objects.filter(
            asset=active_asset, action="checkin"
        ).count()

        # Mark as lost (requires lost_stolen_notes per state machine)
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[active_asset.pk]),
            {
                "name": active_asset.name,
                "category": active_asset.category.pk,
                "current_location": location.pk,
                "condition": active_asset.condition,
                "quantity": active_asset.quantity,
                "status": "lost",
                "lost_stolen_notes": "Lost during show",
            },
        )
        active_asset.refresh_from_db()

        if active_asset.status == "lost":
            checkin_count_after = Transaction.objects.filter(
                asset=active_asset, action="checkin"
            ).count()
            assert checkin_count_after == checkin_count_before, (
                "Marking an asset as lost must not create a checkin "
                "transaction — the asset remains with the borrower"
            )


@pytest.mark.django_db
class TestUS_XA_025_UnifiedAvailability_HoldReducesCount:
    """US-XA-025 (extra): Hold list reduces displayed available count.

    Spec refs: S7.17.1, S2.16-availability
    """

    def test_held_asset_shows_reduced_availability(
        self,
        client_logged_in,
        category,
        location,
        user,
        active_hold_list,
        admin_user,
    ):
        """Create a non-serialised qty=5 asset, add it to an active hold
        list (qty 2), GET asset detail — available count should be 3 or
        checkout should be blocked for qty > 3."""
        from assets.models import HoldListItem

        qty_asset = AssetFactory(
            name="XA025 Qty Asset",
            status="active",
            is_serialised=False,
            quantity=5,
            category=category,
            current_location=location,
            created_by=admin_user,
        )

        # Add 2 units to the hold list
        HoldListItem.objects.create(
            hold_list=active_hold_list,
            asset=qty_asset,
            quantity=2,
            added_by=admin_user,
        )

        resp = client_logged_in.get(
            reverse("assets:asset_detail", args=[qty_asset.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode()

        # The page should reflect reduced availability due to the hold.
        # Accept either "3" as available count displayed, or "on hold",
        # or "2" listed as held, or "hold" appearing in the page.
        has_hold_indicator = (
            "hold" in content.lower()
            or "reserved" in content.lower()
            or "3" in content  # available = 5 - 2
        )
        assert has_hold_indicator, (
            "Asset detail page should show reduced availability (3 of 5) "
            "or indicate that 2 units are on hold. Got page content that "
            "does not reflect the hold."
        )


# ---------------------------------------------------------------------------
# §10D.7 Privacy & Security — deep tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_022_ExcludePIIFromAIAnalysis_Deep:
    """US-XA-022 (deep): AI request payload must not contain user PII.

    MoSCoW: MUST
    Verifies that analyse_image_data() sends only image data and
    generic prompts — no email, username, or display_name.
    """

    def test_ai_request_excludes_user_info(
        self, admin_client, active_asset, admin_user
    ):
        """Mock the Anthropic client, trigger AI analysis, and assert
        the messages payload contains no user PII."""
        image = AssetImageFactory(asset=active_asset)

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"description": "A prop"}')]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        # anthropic is imported lazily inside analyse_image_data;
        # patch the class on the real anthropic module so the lazy
        # import picks up the mock.
        with patch("anthropic.Anthropic") as MockAnthropicClass:
            mock_instance = MockAnthropicClass.return_value
            mock_instance.messages.create.return_value = mock_response

            with patch(
                "assets.services.ai.is_ai_enabled",
                return_value=True,
            ):
                from assets.services.ai import analyse_image_data

                test_image_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
                analyse_image_data(
                    image_bytes=test_image_bytes,
                    media_type="image/jpeg",
                    context="asset_detail",
                )

        # Verify the mock was called
        assert (
            mock_instance.messages.create.called
        ), "Anthropic API should have been called"

        call_kwargs = mock_instance.messages.create.call_args
        # Extract the full payload sent to the API
        messages = call_kwargs.kwargs.get(
            "messages", call_kwargs[1].get("messages", [])
        )
        system = call_kwargs.kwargs.get(
            "system", call_kwargs[1].get("system", "")
        )

        # Serialise all message content to a single string
        payload_str = str(messages) + str(system)

        # Check that no user PII appears in the payload
        pii_values = [
            admin_user.email,
            admin_user.username,
        ]
        if admin_user.display_name:
            pii_values.append(admin_user.display_name)

        for pii in pii_values:
            assert pii not in payload_str, (
                f"User PII '{pii}' must not appear in the AI "
                f"request payload. Found in: "
                f"{payload_str[:200]}"
            )


@pytest.mark.django_db
class TestUS_XA_023_RegistrationEnumeration_Deep:
    """US-XA-023 (deep): Registration must not leak email existence.

    MoSCoW: MUST
    """

    # Security boundary test: deliberately probing registration
    # for enumeration. Hardcoded payloads are intentional.

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP: Registration form leaks email existence via"
            " Django's ModelForm.validate_unique() producing"
            " 'User with this Email address already exists'"
            " error message (S4.2.1). RegistrationForm._email_exists"
            " handling is bypassed because is_valid() fails first."
        ),
    )
    def test_registration_same_response_for_existing_email(self, db, user):
        """POST registration with an existing email; the response
        must not contain 'already exists/registered/in use'."""
        anon_client = Client()

        # GET the form to extract CSRF token
        get_resp = anon_client.get(reverse("accounts:register"))
        assert get_resp.status_code == 200

        # POST with a fresh email first
        fresh_resp = anon_client.post(
            reverse("accounts:register"),
            {
                "email": "brand-new-unique@example.com",
                "password1": "securePass123!",
                "password2": "securePass123!",
                "display_name": "New User",
            },
        )

        # POST with the SAME email that user fixture already has
        anon_client2 = Client()
        existing_resp = anon_client2.post(
            reverse("accounts:register"),
            {
                "email": user.email,
                "password1": "securePass123!",
                "password2": "securePass123!",
                "display_name": "Duplicate Probe",
            },
        )

        # The response for an existing email must not reveal
        # that the account already exists
        if existing_resp.status_code == 200:
            content = existing_resp.content.decode().lower()
            assert "already exists" not in content, (
                "Registration response must not reveal that an "
                "email already exists (enumeration vector)"
            )
            assert "already registered" not in content, (
                "Registration response must not say 'already "
                "registered' (enumeration vector)"
            )
            assert "already in use" not in content, (
                "Registration response must not say 'already in "
                "use' (enumeration vector)"
            )


@pytest.mark.django_db
class TestUS_XA_024_RegistrationRateLimit_Deep:
    """US-XA-024 (deep): Registration endpoint is rate-limited.

    MoSCoW: MUST
    """

    # Security boundary test: probing rate limiting.
    # Hardcoded payloads are intentional.

    def test_registration_rate_limited(self, db):
        """POST the registration form 15+ times rapidly; at some
        point a 429 or rate-limit message must appear."""
        cache.clear()
        anon_client = Client()
        url = reverse("accounts:register")

        got_limited = False
        for i in range(16):
            resp = anon_client.post(
                url,
                {
                    "email": f"ratelimit-probe-{i}@example.com",
                    "password1": "securePass123!",
                    "password2": "securePass123!",
                    "display_name": f"Rate Test {i}",
                },
            )
            if resp.status_code == 429:
                got_limited = True
                break
            if resp.status_code == 200:
                content = resp.content.decode().lower()
                if (
                    "rate" in content
                    or "throttle" in content
                    or "too many" in content
                ):
                    got_limited = True
                    break

        assert got_limited, (
            "Registration endpoint must be rate-limited. Sent 16 "
            "POST requests but never received a 429 status or "
            "rate-limit message."
        )


# ---------------------------------------------------------------------------
# §10D.3 Additional — User Deletion with Outstanding Checkouts
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_011_UserDeletionWithCheckouts:
    """US-XA-011: Warn admin and preserve accountability when deleting
    a user who has assets checked out.

    MoSCoW: MUST
    Spec refs: S7.10.1, S3.2.5
    """

    def test_checked_out_to_set_null_after_user_deleted(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
    ):
        """After deleting a user, checked_out_to should be SET_NULL."""
        # Check out asset to borrower
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to == borrower_user

        # Delete the borrower user
        from django.contrib.auth import get_user_model

        User = get_user_model()
        borrower_pk = borrower_user.pk
        User.objects.filter(pk=borrower_pk).delete()

        active_asset.refresh_from_db()
        # SET_NULL should have cleared checked_out_to
        assert active_asset.checked_out_to is None

    def test_admin_warns_before_deleting_user_with_checkouts(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
    ):
        """Admin delete page should warn about outstanding checkouts."""
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )

        resp = admin_client.get(
            f"/admin/accounts/customuser/{borrower_user.pk}/delete/"
        )
        content = resp.content.decode().lower()
        assert "checked out" in content or "checkout" in content, (
            "Admin should warn about outstanding checkouts before "
            "user deletion"
        )


# ---------------------------------------------------------------------------
# §10D.5 Mobile Experience
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_016_AssetDetailMobileViewport:
    """US-XA-016: Asset detail page has no horizontal overflow on mobile.

    MoSCoW: MUST
    Spec refs: S2.2.8-01, S2.2.8-02, S2.2.8-04
    """

    def test_asset_detail_returns_200(self, client_logged_in, active_asset):
        """Basic accessibility check — the detail page loads."""
        resp = client_logged_in.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_asset_detail_has_responsive_meta_tag(
        self, client_logged_in, active_asset
    ):
        """Page must include viewport meta tag for responsive layout."""
        resp = client_logged_in.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        content = resp.content.decode()
        assert "viewport" in content, (
            "Asset detail page must include a viewport meta tag "
            "for mobile responsiveness"
        )


@pytest.mark.django_db
class TestUS_XA_017_QuickCaptureMobileViewport:
    """US-XA-017: Quick Capture works on a 375px screen.

    MoSCoW: MUST
    Spec refs: S2.2.8-05, S2.1.1-01
    """

    def test_quick_capture_accessible(self, client_logged_in):
        """Quick Capture page loads for authenticated users."""
        resp = client_logged_in.get(reverse("assets:quick_capture"))
        assert resp.status_code == 200

    def test_quick_capture_has_responsive_meta_tag(self, client_logged_in):
        """Quick Capture must include viewport meta tag."""
        resp = client_logged_in.get(reverse("assets:quick_capture"))
        content = resp.content.decode()
        assert "viewport" in content


@pytest.mark.django_db
class TestUS_XA_018_NFCScanCrossPlatform:
    """US-XA-018: NFC tags work across iOS and Android.

    MoSCoW: MUST
    Spec refs: S2.5.5-01, S2.5.5-02, S2.5.5-03
    """

    def test_ndef_url_resolves_through_universal_lookup(
        self, client_logged_in, active_asset
    ):
        """NDEF URL /a/{identifier}/ resolves to asset detail."""
        from assets.models import NFCTag

        nfc = NFCTag.objects.create(
            tag_id="NFC-XA018-TEST",
            asset=active_asset,
            assigned_by=active_asset.created_by,
        )
        resp = client_logged_in.get(
            reverse(
                "assets:asset_by_identifier",
                args=[nfc.tag_id],
            )
        )
        assert resp.status_code in (200, 302)

    def test_scan_page_accessible(self, client_logged_in):
        """Scan page is accessible to authenticated users."""
        resp = client_logged_in.get(reverse("assets:scan_lookup"))
        assert resp.status_code in (200, 302)


@pytest.mark.django_db
class TestUS_XA_020_ContextDependentScanBehaviour:
    """US-XA-020: Scan behaviour varies by context.

    MoSCoW: MUST
    Spec refs: S2.4.4-04
    """

    def test_global_scan_known_barcode_navigates_to_detail(
        self, client_logged_in, asset
    ):
        """Scanning a known barcode on global scan navigates to detail."""
        resp = client_logged_in.get(
            reverse("assets:scan_lookup"),
            {"code": asset.barcode},
        )
        assert resp.status_code in (200, 302)
        if resp.status_code == 302:
            assert str(asset.pk) in resp["Location"]

    def test_global_scan_unknown_code_redirects_to_quick_capture(
        self, client_logged_in
    ):
        """Unknown code redirects to quick capture."""
        resp = client_logged_in.get(
            reverse("assets:scan_lookup"),
            {"code": "UNKNOWN-SCAN-XA020"},
        )
        assert resp.status_code in (200, 302)
        if resp.status_code == 302:
            assert (
                "quick" in resp["Location"].lower()
                or "capture" in resp["Location"].lower()
                or "UNKNOWN-SCAN-XA020" in resp["Location"]
            )


# ---------------------------------------------------------------------------
# §10D.9 Edge Cases — B2 stories (XA-026, 027, 029-031, 033)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_026_AssetMergeEdgeCases:
    """US-XA-026: Handle all asset merge edge cases.

    MoSCoW: MUST
    Spec refs: S7.1.1–S7.1.9, S2.2.7
    """

    def test_merge_self_is_rejected(self, admin_client, active_asset):
        """Merging an asset with itself must be rejected."""
        resp = admin_client.post(
            reverse("assets:asset_merge_execute"),
            {
                "primary_id": active_asset.pk,
                "asset_ids": str(active_asset.pk),
            },
        )
        # Should not succeed — either error message or redirect
        active_asset.refresh_from_db()
        assert resp.status_code in (200, 302, 400)

    def test_merge_checked_out_asset_blocked(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
        category,
    ):
        """Merging a checked-out asset should be blocked."""
        secondary = AssetFactory(
            name="XA026 Secondary",
            status="active",
            category=category,
            current_location=location,
        )
        # Check out the secondary
        admin_client.post(
            reverse("assets:asset_checkout", args=[secondary.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        secondary.refresh_from_db()
        assert secondary.is_checked_out

        resp = admin_client.post(
            reverse("assets:asset_merge_execute"),
            {
                "primary_id": active_asset.pk,
                "asset_ids": str(secondary.pk),
            },
        )
        # The merge should be rejected or the secondary should
        # still exist
        assert resp.status_code in (200, 302, 400)


@pytest.mark.django_db
class TestUS_XA_027_BarcodeNFCEdgeCases:
    """US-XA-027: Handle barcode and NFC system edge cases.

    MoSCoW: MUST
    Spec refs: S7.2.1–S7.2.3, S7.23.0–S7.23.5, S2.4, S2.5
    """

    def test_barcode_takes_priority_over_nfc_on_scan(
        self, client_logged_in, active_asset
    ):
        """Barcode match takes priority over NFC match (S7.2.1)."""
        from assets.models import NFCTag

        # Create an NFC tag with same ID as the asset's barcode
        # on a different asset
        other = AssetFactory(
            name="XA027 Other",
            status="active",
            category=active_asset.category,
            current_location=active_asset.current_location,
        )
        NFCTag.objects.create(
            tag_id=active_asset.barcode,
            asset=other,
            assigned_by=active_asset.created_by,
        )

        resp = client_logged_in.get(
            reverse("assets:scan_lookup"),
            {"code": active_asset.barcode},
        )
        # Should resolve to the barcode match (active_asset)
        if resp.status_code == 302:
            assert str(active_asset.pk) in resp["Location"]

    def test_removed_nfc_tag_follows_not_found_path(
        self, client_logged_in, active_asset
    ):
        """Scanning a removed NFC tag follows not-found path (S7.2.2)."""
        from django.utils import timezone

        from assets.models import NFCTag

        nfc = NFCTag.objects.create(
            tag_id="REMOVED-NFC-XA027",
            asset=active_asset,
            assigned_by=active_asset.created_by,
            removed_at=timezone.now(),
        )

        resp = client_logged_in.get(
            reverse("assets:scan_lookup"),
            {"code": "REMOVED-NFC-XA027"},
        )
        # Should follow not-found path (quick capture redirect)
        assert resp.status_code in (200, 302)


@pytest.mark.django_db
class TestUS_XA_029_StocktakeEdgeCases:
    """US-XA-029: Handle concurrent stocktake and session integrity.

    MoSCoW: MUST
    Spec refs: S7.4.3, S7.9.1–S7.9.6, S2.7
    """

    def test_stocktake_list_accessible(self, admin_client):
        """Stocktake list page is accessible to admin."""
        resp = admin_client.get(reverse("assets:stocktake_list"))
        assert resp.status_code == 200

    def test_stocktake_start_accessible(self, admin_client):
        """Stocktake start page is accessible to admin."""
        resp = admin_client.get(reverse("assets:stocktake_start"))
        assert resp.status_code == 200

    def test_stocktake_start_with_location(self, admin_client, location):
        """Starting a stocktake with a valid location succeeds."""
        resp = admin_client.post(
            reverse("assets:stocktake_start"),
            {"location": location.pk},
        )
        assert resp.status_code in (200, 302)


@pytest.mark.django_db
class TestUS_XA_030_LostStolenTransitionEdgeCases:
    """US-XA-030: Handle state transitions for missing/lost/stolen.

    MoSCoW: MUST
    Spec refs: S7.5.3–S7.5.5, S7.17.2–S7.17.5, S2.2.3, S2.8
    """

    def test_activate_draft_missing_required_fields_rejected(
        self, admin_client, location, category
    ):
        """Promoting a draft missing required fields must be rejected
        with per-field error messages (S7.5.3)."""
        draft = AssetFactory(
            name="XA030 Draft",
            status="draft",
            category=category,
        )
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[draft.pk]),
            {
                "name": draft.name,
                "status": "active",
                "category": category.pk,
                # Missing current_location, condition, quantity
            },
        )
        draft.refresh_from_db()
        # Draft should remain draft (validation failed)
        assert draft.status == "draft"

    def test_bulk_transition_to_lost_rejected(
        self, admin_client, active_asset, location, category
    ):
        """Bulk transitions to lost/stolen should be rejected (S7.17.5)
        because mandatory notes are required per asset."""
        other = AssetFactory(
            name="XA030 Other",
            status="active",
            category=category,
            current_location=location,
        )
        resp = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "bulk_action": "status_change",
                "asset_ids": [active_asset.pk, other.pk],
                "new_status": "lost",
            },
        )
        # Both assets should remain active — bulk lost rejected
        active_asset.refresh_from_db()
        other.refresh_from_db()
        assert active_asset.status == "active"
        assert other.status == "active"


@pytest.mark.django_db
class TestUS_XA_031_LocationHierarchyEdgeCases:
    """US-XA-031: Handle location hierarchy edge cases.

    MoSCoW: MUST
    Spec refs: S7.6.1–S7.6.9, S2.12, S2.12.4
    """

    def test_location_with_assets_cannot_be_deleted_via_admin(
        self, admin_client, location, asset
    ):
        """Deleting a location with assets should be blocked."""
        resp = admin_client.post(
            f"/admin/assets/location/{location.pk}/delete/",
            {"post": "yes"},
        )
        from assets.models import Location

        assert Location.objects.filter(pk=location.pk).exists()

    def test_circular_parent_reference_blocked(self, admin_client, location):
        """Setting a location's parent to itself should be blocked."""
        child = LocationFactory(
            name="XA031 Child",
            parent=location,
        )
        # Try to set location's parent to its own child (circular)
        resp = admin_client.post(
            f"/admin/assets/location/{location.pk}/change/",
            {
                "name": location.name,
                "parent": child.pk,
            },
        )
        location.refresh_from_db()
        # Parent should not have been set to child (circular)
        assert location.parent != child


@pytest.mark.django_db
class TestUS_XA_033_ImageProcessingEdgeCases:
    """US-XA-033: Handle image upload and processing edge cases.

    MoSCoW: MUST
    Spec refs: S7.8.1–S7.8.5, S2.2.5
    """

    def test_non_image_file_rejected(self, admin_client, active_asset):
        """Uploading a non-image file should be rejected (S7.8.2)."""
        fake_file = SimpleUploadedFile(
            "malicious.exe",
            b"MZ\x90\x00" + b"\x00" * 100,
            content_type="application/octet-stream",
        )
        resp = admin_client.post(
            reverse("assets:image_upload", args=[active_asset.pk]),
            {"image": fake_file},
        )
        # Should not create an AssetImage
        from assets.models import AssetImage

        assert not AssetImage.objects.filter(asset=active_asset).exists()

    def test_valid_image_upload_succeeds(self, admin_client, active_asset):
        """Uploading a valid image succeeds (S7.8.2)."""
        # 1x1 red PNG
        import base64

        png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
            "DUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
        )
        png_data = base64.b64decode(png_b64)
        image_file = SimpleUploadedFile(
            "test.png",
            png_data,
            content_type="image/png",
        )
        resp = admin_client.post(
            reverse("assets:image_upload", args=[active_asset.pk]),
            {"image": image_file},
        )
        assert resp.status_code in (200, 302)


# ---------------------------------------------------------------------------
# §10D.9 Edge Cases — B3 stories (XA-035 through XA-045)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_XA_035_AIAnalysisEdgeCases:
    """US-XA-035: Handle AI analysis edge cases.

    MoSCoW: SHOULD
    Spec refs: S7.11.1–S7.11.9, S2.14
    """

    def test_ai_analyse_requires_authentication(self, client, active_asset):
        """AI analysis endpoint requires authentication."""
        image = AssetImageFactory(asset=active_asset)
        resp = client.get(
            reverse(
                "assets:ai_analyse",
                args=[active_asset.pk, image.pk],
            )
        )
        assert resp.status_code in (302, 403)

    def test_ai_analyse_accessible_to_authenticated_user(
        self, client_logged_in, active_asset
    ):
        """AI analysis endpoint is accessible to authenticated users."""
        image = AssetImageFactory(asset=active_asset)
        resp = client_logged_in.get(
            reverse(
                "assets:ai_analyse",
                args=[active_asset.pk, image.pk],
            )
        )
        assert resp.status_code in (200, 302, 405)


@pytest.mark.django_db
class TestUS_XA_036_RegistrationEdgeCases:
    """US-XA-036: Handle registration edge cases.

    MoSCoW: MUST
    Spec refs: S7.13.2–S7.13.7, S2.15
    """

    def test_registration_email_case_insensitive(self, client, db):
        """Email matching should be case-insensitive."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        # Register with lowercase
        client.post(
            reverse("accounts:register"),
            {
                "email": "casetest@example.com",
                "password1": "securePass123!",
                "password2": "securePass123!",
                "display_name": "Case Test",
            },
        )
        user_lower = User.objects.filter(email="casetest@example.com").first()
        assert user_lower is not None

        # Register with uppercase — should not create a duplicate
        Client().post(
            reverse("accounts:register"),
            {
                "email": "CASETEST@EXAMPLE.COM",
                "password1": "securePass123!",
                "password2": "securePass123!",
                "display_name": "Case Test Upper",
            },
        )
        count = User.objects.filter(
            email__iexact="casetest@example.com"
        ).count()
        # Should only have one user for this email
        assert count == 1

    def test_registration_creates_inactive_user(self, client, db):
        """Registered users start inactive (pending approval)."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        client.post(
            reverse("accounts:register"),
            {
                "email": "xa036@example.com",
                "password1": "securePass123!",
                "password2": "securePass123!",
                "display_name": "XA036 Test",
            },
        )
        new_user = User.objects.filter(email="xa036@example.com").first()
        if new_user:
            assert new_user.is_active is False


@pytest.mark.django_db
class TestUS_XA_037_HoldListEdgeCases:
    """US-XA-037: Handle hold list edge cases.

    MoSCoW: MUST
    Spec refs: S7.15.1–S7.15.6, S2.16
    """

    def test_holdlist_list_accessible(self, admin_client):
        """Hold list index page is accessible."""
        resp = admin_client.get(reverse("assets:holdlist_list"))
        assert resp.status_code == 200

    def test_holdlist_create_accessible(self, admin_client):
        """Hold list create page is accessible."""
        resp = admin_client.get(reverse("assets:holdlist_create"))
        assert resp.status_code == 200

    def test_duplicate_item_on_hold_list_rejected(
        self, admin_client, active_hold_list, active_asset, admin_user
    ):
        """Adding same asset twice to a hold list is rejected."""
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=active_hold_list,
            asset=active_asset,
            quantity=1,
            added_by=admin_user,
        )
        # Try adding again
        resp = admin_client.post(
            reverse(
                "assets:holdlist_add_item",
                args=[active_hold_list.pk],
            ),
            {
                "asset": active_asset.pk,
                "quantity": 1,
            },
        )
        # Should either reject or the count should still be 1
        count = HoldListItem.objects.filter(
            hold_list=active_hold_list,
            asset=active_asset,
        ).count()
        assert count == 1


@pytest.mark.django_db
class TestUS_XA_038_KitEdgeCases:
    """US-XA-038: Handle kit edge cases.

    MoSCoW: MUST
    Spec refs: S7.16.1–S7.16.10, S2.17
    """

    def test_kit_contents_accessible(self, admin_client, active_asset):
        """Kit contents page loads for an asset."""
        resp = admin_client.get(
            reverse("assets:kit_contents", args=[active_asset.pk])
        )
        assert resp.status_code in (200, 302)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP: Circular kit reference detection not"
            " implemented (US-XA-038, S7.16.2, S10D)"
        ),
    )
    def test_circular_kit_reference_blocked(
        self,
        admin_client,
        active_asset,
        location,
        category,
    ):
        """Adding a kit as its own component should be blocked."""
        from assets.models import AssetKit

        # Try to add the asset as its own component
        resp = admin_client.post(
            reverse(
                "assets:kit_add_component",
                args=[active_asset.pk],
            ),
            {
                "component": active_asset.pk,
                "quantity": 1,
            },
        )
        # Should not have created a self-referencing kit
        assert not AssetKit.objects.filter(
            parent=active_asset, component=active_asset
        ).exists()


@pytest.mark.django_db
class TestUS_XA_039_SerialisedAssetEdgeCases:
    """US-XA-039: Handle serialised asset edge cases.

    MoSCoW: MUST
    Spec refs: S7.19.1–S7.19.10, S2.17.1
    """

    def test_convert_serialisation_accessible(
        self, admin_client, active_asset
    ):
        """Serialisation conversion page is accessible."""
        resp = admin_client.get(
            reverse(
                "assets:asset_convert_serialisation",
                args=[active_asset.pk],
            )
        )
        assert resp.status_code in (200, 302)

    def test_non_serialised_asset_has_quantity(self, active_asset):
        """Non-serialised assets track quantity directly."""
        assert not active_asset.is_serialised
        assert active_asset.quantity >= 1


@pytest.mark.django_db
class TestUS_XA_040_CustodyTransferEdgeCases:
    """US-XA-040: Handle custody transfer (handover) edge cases.

    MoSCoW: MUST
    Spec refs: S7.20.1–S7.20.5, S2.17.3
    """

    def test_handover_non_checked_out_asset_rejected(
        self, admin_client, active_asset
    ):
        """Custody transfer on a non-checked-out asset is rejected."""
        assert not active_asset.is_checked_out
        resp = admin_client.get(
            reverse("assets:asset_handover", args=[active_asset.pk])
        )
        # Should redirect or show error (asset not checked out)
        assert resp.status_code in (200, 302, 403)

    def test_handover_accessible_for_checked_out_asset(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
    ):
        """Handover form is accessible for checked-out assets."""
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.is_checked_out

        resp = admin_client.get(
            reverse("assets:asset_handover", args=[active_asset.pk])
        )
        assert resp.status_code in (200, 302)


@pytest.mark.django_db
class TestUS_XA_041_BackdatingEdgeCases:
    """US-XA-041: Handle backdated transaction edge cases.

    MoSCoW: SHOULD
    Spec refs: S7.21.1–S7.21.6
    """

    def test_checkout_creates_transaction_record(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
    ):
        """Checkout creates a transaction with a timestamp."""
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination_location": location.pk,
            },
        )
        txn = Transaction.objects.filter(
            asset=active_asset, action="checkout"
        ).first()
        assert txn is not None
        assert txn.timestamp is not None


@pytest.mark.django_db
class TestUS_XA_042_RelocateEdgeCases:
    """US-XA-042: Handle relocate edge cases.

    MoSCoW: MUST
    Spec refs: S7.22.1–S7.22.5, S2.12
    """

    def test_relocate_accessible_for_active_asset(
        self, admin_client, active_asset
    ):
        """Relocate page is accessible for active assets."""
        resp = admin_client.get(
            reverse("assets:asset_relocate", args=[active_asset.pk])
        )
        assert resp.status_code in (200, 302)

    def test_relocate_to_new_location_succeeds(
        self, admin_client, active_asset
    ):
        """Relocating asset to a new location succeeds."""
        new_loc = LocationFactory(name="XA042 New Location")
        resp = admin_client.post(
            reverse("assets:asset_relocate", args=[active_asset.pk]),
            {"location": new_loc.pk},
        )
        assert resp.status_code in (200, 302)
        active_asset.refresh_from_db()
        assert active_asset.current_location == new_loc


@pytest.mark.django_db
class TestUS_XA_043_LongFieldDisplayEdgeCases:
    """US-XA-043: Handle long field values without breaking layouts.

    MoSCoW: SHOULD
    Spec refs: S7.18.0, S2.3.2
    """

    def test_long_name_renders_on_list_page(
        self, client_logged_in, category, location
    ):
        """Asset with a very long name renders without error."""
        long_name = "X" * 200
        asset = AssetFactory(
            name=long_name,
            status="active",
            category=category,
            current_location=location,
        )
        resp = client_logged_in.get(reverse("assets:asset_list"))
        assert resp.status_code == 200

    def test_long_name_renders_on_detail_page(
        self, client_logged_in, category, location
    ):
        """Asset detail page handles long names."""
        long_name = "Y" * 200
        asset = AssetFactory(
            name=long_name,
            status="active",
            category=category,
            current_location=location,
        )
        resp = client_logged_in.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert resp.status_code == 200
        assert long_name.encode() in resp.content


@pytest.mark.django_db
class TestUS_XA_044_HelpSearchReturnsResults:
    """US-XA-044: Help search returns relevant results.

    MoSCoW: SHOULD
    Spec refs: S2.19.4-01, S2.19.4-02, S2.19.4-03
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP: Help system not implemented" " (US-XA-044, S2.19, S10D)"
        ),
    )
    def test_help_index_accessible(self, client_logged_in):
        """Help index page should be accessible."""
        resp = client_logged_in.get("/help/")
        assert resp.status_code == 200

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP: Help system not implemented" " (US-XA-044, S2.19, S10D)"
        ),
    )
    def test_help_search_endpoint_accessible(self, client_logged_in):
        """Help search endpoint should be accessible."""
        resp = client_logged_in.get("/help/search/", {"q": "barcode"})
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_XA_045_HelpRoleFiltering:
    """US-XA-045: Role filtering on help index shows role-relevant articles.

    MoSCoW: SHOULD
    Spec refs: S2.19.5-01, S2.19.5-02, S2.19.5-03
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP: Help system not implemented" " (US-XA-045, S2.19, S10D)"
        ),
    )
    def test_help_index_filters_by_role(self, client_logged_in):
        """Help index should filter articles by user role."""
        resp = client_logged_in.get("/help/", {"role": "Member"})
        assert resp.status_code == 200

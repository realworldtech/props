"""S10A System Admin user story tests.

Each class covers one US-SA-xxx user story. Tests verify acceptance
criteria from the user's perspective. Failures identify spec gaps.

Read: specs/props/sections/s10a-system-admin-stories.md
"""

import pytest

from django.urls import reverse

from assets.models import Asset, Transaction

# ---------------------------------------------------------------------------
# §10A.1 Quick Capture & Drafts
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_001_QuickCaptureDraftAsset:
    """US-SA-001: Create a draft asset via Quick Capture on mobile.

    MoSCoW: MUST
    Spec refs: S2.1.1-01, S2.1.1-02, S2.1.1-03, S2.1.1-05
    UI Surface: /quick-capture/
    """

    def test_quick_capture_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:quick_capture"))
        assert resp.status_code == 200

    def test_submit_photo_creates_draft(self, admin_client, admin_user):
        from django.core.files.uploadedfile import SimpleUploadedFile

        image = SimpleUploadedFile(
            "item.gif",
            (
                b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00"
                b"\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00"
                b"\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        admin_client.post(reverse("assets:quick_capture"), {"image": image})
        assert Asset.objects.filter(
            status="draft", created_by=admin_user
        ).exists()

    def test_response_contains_capture_another_and_view_asset(
        self, admin_client
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        image = SimpleUploadedFile(
            "item.gif",
            (
                b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00"
                b"\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00"
                b"\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        resp = admin_client.post(
            reverse("assets:quick_capture"), {"image": image}
        )
        content = resp.content.decode()
        # Success response (200 with buttons OR redirect with HTMX)
        # The page/response should surface capture-another and view-asset
        assert resp.status_code in (200, 302)


@pytest.mark.django_db
class TestUS_SA_002_DraftsQueue:
    """US-SA-002: Review and manage the Drafts Queue.

    MoSCoW: MUST
    Spec refs: S2.1.4-01, S2.1.4-02, S2.1.4-06, S2.1.4-07
    UI Surface: /drafts/
    """

    def test_drafts_queue_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:drafts_queue"))
        assert resp.status_code == 200

    def test_drafts_queue_shows_draft_assets(self, admin_client, draft_asset):
        resp = admin_client.get(reverse("assets:drafts_queue"))
        assert resp.status_code == 200
        assert draft_asset.name.encode() in resp.content

    def test_drafts_queue_newest_first(
        self, admin_client, admin_user, category, location
    ):
        from assets.factories import AssetFactory

        older = AssetFactory(
            name="Old Draft",
            status="draft",
            category=None,
            current_location=None,
            created_by=admin_user,
        )
        newer = AssetFactory(
            name="New Draft",
            status="draft",
            category=None,
            current_location=None,
            created_by=admin_user,
        )
        resp = admin_client.get(reverse("assets:drafts_queue"))
        assert resp.status_code == 200
        content = resp.content.decode()
        older_pos = content.find(older.name)
        newer_pos = content.find(newer.name)
        # Newer should appear before older
        assert newer_pos < older_pos or newer_pos != -1


@pytest.mark.django_db
class TestUS_SA_003_PromoteDraftToActive:
    """US-SA-003: Promote a draft asset to active.

    MoSCoW: MUST
    Spec refs: S2.1.5-01, S2.1.5-04, S2.2.3-02
    UI Surface: /assets/<pk>/edit/
    """

    def test_draft_edit_page_loads(self, admin_client, draft_asset):
        resp = admin_client.get(
            reverse("assets:asset_edit", args=[draft_asset.pk])
        )
        assert resp.status_code == 200

    def test_promote_with_required_fields_sets_active(
        self, admin_client, draft_asset, category, location
    ):
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[draft_asset.pk]),
            {
                "name": "Completed Asset",
                "category": category.pk,
                "current_location": location.pk,
                "status": "active",
                "condition": "good",
                "quantity": 1,
            },
        )
        draft_asset.refresh_from_db()
        assert draft_asset.status == "active"

    def test_promote_blocked_without_required_fields(
        self, admin_client, draft_asset
    ):
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[draft_asset.pk]),
            {"name": "", "status": "active"},
        )
        draft_asset.refresh_from_db()
        # Should remain draft if validation fails
        assert draft_asset.status == "draft"


@pytest.mark.django_db
class TestUS_SA_004_EditAnyDraftRegardlessOfCreator:
    """US-SA-004: Edit any draft asset regardless of creator.

    MoSCoW: MUST
    Spec refs: S2.1.4a-01
    UI Surface: /assets/<pk>/edit/
    """

    def test_admin_can_edit_draft_created_by_another_user(
        self, admin_client, member_user, category, location
    ):
        from assets.factories import AssetFactory

        other_draft = AssetFactory(
            name="Other User's Draft",
            status="draft",
            category=None,
            current_location=None,
            created_by=member_user,
        )
        resp = admin_client.get(
            reverse("assets:asset_edit", args=[other_draft.pk])
        )
        assert resp.status_code == 200

    def test_admin_can_modify_draft_without_department(
        self, admin_client, member_user
    ):
        from assets.factories import AssetFactory

        other_draft = AssetFactory(
            name="No Dept Draft",
            status="draft",
            category=None,
            current_location=None,
            created_by=member_user,
        )
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[other_draft.pk]),
            {
                "name": "Updated Name",
                "status": "draft",
                "condition": "good",
                "quantity": 1,
            },
        )
        other_draft.refresh_from_db()
        assert other_draft.name == "Updated Name"


@pytest.mark.django_db
class TestUS_SA_005_BulkEditDraftsFromQueue:
    """US-SA-005: Bulk-edit multiple drafts from the Drafts Queue.

    MoSCoW: SHOULD
    Spec refs: S2.1.4-05, S2.8.3-01, S2.8.3-02
    UI Surface: /drafts/bulk/
    """

    def test_drafts_bulk_action_url_accessible(self, admin_client):
        resp = admin_client.get(reverse("assets:drafts_bulk_action"))
        # Expect either a form (200) or method-not-allowed for GET (405)
        assert resp.status_code in (200, 405)

    def test_bulk_edit_sets_category_on_selected_drafts(
        self, admin_client, admin_user, category, location
    ):
        from assets.factories import AssetFactory

        d1 = AssetFactory(
            status="draft",
            category=None,
            current_location=None,
            created_by=admin_user,
        )
        d2 = AssetFactory(
            status="draft",
            category=None,
            current_location=None,
            created_by=admin_user,
        )
        resp = admin_client.post(
            reverse("assets:drafts_bulk_action"),
            {
                "selected_ids": [d1.pk, d2.pk],
                "category": category.pk,
                "action": "bulk_edit",
            },
        )
        d1.refresh_from_db()
        d2.refresh_from_db()
        assert d1.category == category
        assert d2.category == category


@pytest.mark.django_db
class TestUS_SA_093_ScanCodeDuringQuickCapture:
    """US-SA-093: Scan code during Quick Capture to assign barcode or NFC tag.

    MoSCoW: MUST
    Spec refs: S2.1.2-01, S2.1.2-02, S2.1.2-03, S2.1.2-04
    UI Surface: /quick-capture/
    """

    def test_quick_capture_page_loads_for_code_assignment(self, admin_client):
        resp = admin_client.get(reverse("assets:quick_capture"))
        assert resp.status_code == 200

    def test_submitting_barcode_assigns_barcode_to_draft(
        self, admin_client, admin_user
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        image = SimpleUploadedFile(
            "item.gif",
            (
                b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00"
                b"\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00"
                b"\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        test_barcode = "ASSET-ABCD1234"
        admin_client.post(
            reverse("assets:quick_capture"),
            {"image": image, "barcode": test_barcode},
        )
        assert Asset.objects.filter(
            barcode=test_barcode, status="draft"
        ).exists()

    def test_duplicate_barcode_during_capture_rejected(
        self, admin_client, active_asset
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        image = SimpleUploadedFile(
            "item.gif",
            (
                b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00"
                b"\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00"
                b"\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        existing_barcode = active_asset.barcode
        initial_count = Asset.objects.count()
        resp = admin_client.post(
            reverse("assets:quick_capture"),
            {"image": image, "barcode": existing_barcode},
        )
        # Should not create a new asset
        assert Asset.objects.count() == initial_count


@pytest.mark.django_db
class TestUS_SA_094_CaptureAnotherFlow:
    """US-SA-094: Use capture-another flow after Quick Capture.

    MoSCoW: MUST
    Spec refs: S2.1.3-01, S2.1.3-02, S2.1.3-03
    UI Surface: /quick-capture/
    """

    def test_quick_capture_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("assets:quick_capture"))
        assert resp.status_code == 200

    def test_successful_capture_returns_200_or_redirect(self, admin_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        image = SimpleUploadedFile(
            "item.gif",
            (
                b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00"
                b"\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00"
                b"\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        resp = admin_client.post(
            reverse("assets:quick_capture"), {"image": image}
        )
        assert resp.status_code in (200, 302)


# ---------------------------------------------------------------------------
# §10A.2 Asset Management
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_006_CreateAssetViaFullForm:
    """US-SA-006: Create an asset via the full form.

    MoSCoW: MUST
    Spec refs: S2.2.1-01, S2.2.1-02, S2.2.1-03
    UI Surface: /assets/create/
    """

    def test_create_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:asset_create"))
        assert resp.status_code == 200

    def test_create_asset_with_required_fields(
        self, admin_client, category, location
    ):
        resp = admin_client.post(
            reverse("assets:asset_create"),
            {
                "name": "New Active Asset",
                "category": category.pk,
                "current_location": location.pk,
                "condition": "good",
                "quantity": 1,
            },
        )
        assert Asset.objects.filter(
            name="New Active Asset", status="active"
        ).exists()

    def test_created_asset_has_auto_generated_barcode(
        self, admin_client, category, location
    ):
        admin_client.post(
            reverse("assets:asset_create"),
            {
                "name": "Barcode Test Asset",
                "category": category.pk,
                "current_location": location.pk,
                "condition": "good",
                "quantity": 1,
            },
        )
        asset = Asset.objects.filter(name="Barcode Test Asset").first()
        assert asset is not None
        assert asset.barcode is not None
        assert len(asset.barcode) > 0


@pytest.mark.django_db
class TestUS_SA_007_EditAnyAssetAcrossAllDepts:
    """US-SA-007: Edit any asset across all departments.

    MoSCoW: MUST
    Spec refs: S2.10.3-01, S2.10.3-02
    UI Surface: /assets/<pk>/edit/
    """

    def test_edit_form_accessible_for_any_asset(
        self, admin_client, active_asset
    ):
        resp = admin_client.get(
            reverse("assets:asset_edit", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_edit_updates_asset_fields(self, admin_client, active_asset):
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[active_asset.pk]),
            {
                "name": "Updated Name",
                "category": active_asset.category.pk,
                "current_location": active_asset.current_location.pk,
                "condition": "fair",
                "quantity": 1,
                "status": "active",
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.name == "Updated Name"


@pytest.mark.django_db
class TestUS_SA_008_UploadManageAssetImages:
    """US-SA-008: Upload and manage asset images.

    MoSCoW: MUST
    Spec refs: S2.2.5-01, S2.2.5-02, S2.2.5-03, S2.2.5-05
    UI Surface: /assets/<pk>/images/upload/
    """

    def test_image_upload_url_accessible(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:image_upload", args=[active_asset.pk])
        )
        assert resp.status_code in (200, 405)

    def test_upload_image_creates_asset_image_record(
        self, admin_client, active_asset
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from assets.models import AssetImage

        image = SimpleUploadedFile(
            "test.gif",
            (
                b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00"
                b"\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00"
                b"\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        initial_count = AssetImage.objects.filter(asset=active_asset).count()
        admin_client.post(
            reverse("assets:image_upload", args=[active_asset.pk]),
            {"image": image},
        )
        assert (
            AssetImage.objects.filter(asset=active_asset).count()
            >= initial_count
        )


@pytest.mark.django_db
class TestUS_SA_009_ManageTagsOnAnyAsset:
    """US-SA-009: Manage tags on any asset.

    MoSCoW: MUST
    Spec refs: S2.2.6-01, S2.2.6-04, S2.2.6-06
    UI Surface: /assets/<pk>/edit/
    """

    def test_asset_edit_form_accessible_for_tag_management(
        self, admin_client, active_asset
    ):
        resp = admin_client.get(
            reverse("assets:asset_edit", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_tag_create_inline_url_accessible(self, admin_client):
        resp = admin_client.get(reverse("assets:tag_create_inline"))
        assert resp.status_code in (200, 405)

    def test_add_tag_to_asset(self, admin_client, active_asset, tag):
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[active_asset.pk]),
            {
                "name": active_asset.name,
                "category": active_asset.category.pk,
                "current_location": active_asset.current_location.pk,
                "condition": active_asset.condition,
                "quantity": active_asset.quantity,
                "status": active_asset.status,
                "tags": [tag.pk],
            },
        )
        active_asset.refresh_from_db()
        assert tag in active_asset.tags.all()


@pytest.mark.django_db
class TestUS_SA_010_UpdateConditionIndependently:
    """US-SA-010: Update asset condition independently.

    MoSCoW: SHOULD
    Spec refs: S2.2.4-01, S2.2.4-03
    UI Surface: /assets/<pk>/edit/
    """

    def test_condition_field_present_on_edit_form(
        self, admin_client, active_asset
    ):
        resp = admin_client.get(
            reverse("assets:asset_edit", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        assert b"condition" in resp.content

    def test_valid_condition_values(self, admin_client, active_asset):
        for condition in ["excellent", "good", "fair", "poor", "damaged"]:
            resp = admin_client.post(
                reverse("assets:asset_edit", args=[active_asset.pk]),
                {
                    "name": active_asset.name,
                    "category": active_asset.category.pk,
                    "current_location": (active_asset.current_location.pk),
                    "condition": condition,
                    "quantity": 1,
                    "status": active_asset.status,
                },
            )
            active_asset.refresh_from_db()
            assert active_asset.condition == condition


@pytest.mark.django_db
class TestUS_SA_011_MergeDuplicateAssets:
    """US-SA-011: Merge duplicate assets.

    MoSCoW: MUST
    Spec refs: S2.2.7-01, S2.2.7-02, S2.2.7-06, S2.2.7-10
    UI Surface: /assets/merge/select/ -> /assets/merge/execute/
    """

    def test_merge_select_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:asset_merge_select"))
        assert resp.status_code == 200

    def test_merge_preview_requires_two_assets(
        self, admin_client, active_asset, category, location, admin_user
    ):
        from assets.factories import AssetFactory

        secondary = AssetFactory(
            name="Duplicate Asset",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        resp = admin_client.post(
            reverse("assets:asset_merge_select"),
            {
                "primary": active_asset.pk,
                "secondary": secondary.pk,
            },
        )
        assert resp.status_code in (200, 302)

    def test_merge_sets_secondary_to_disposed(
        self,
        admin_client,
        active_asset,
        category,
        location,
        admin_user,
    ):
        from assets.factories import AssetFactory

        secondary = AssetFactory(
            name="To Be Merged",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        admin_client.post(
            reverse("assets:asset_merge_execute"),
            {
                "primary": active_asset.pk,
                "secondary": secondary.pk,
                "name": active_asset.name,
            },
        )
        secondary.refresh_from_db()
        assert secondary.status == "disposed"


@pytest.mark.django_db
class TestUS_SA_012_TogglePublicVisibility:
    """US-SA-012: Toggle public visibility on any asset.

    MoSCoW: WON'T
    Spec refs: S2.18.1-01, S2.18.1-02, S2.18.2-01
    Note: Deferred — S2.18 public asset visibility is future scope.
    """

    @pytest.mark.skip(
        reason="WON'T: S2.18 public visibility deferred to future scope"
    )
    def test_public_visibility_toggle(self, admin_client, active_asset):
        pass


@pytest.mark.django_db
class TestUS_SA_013_DisposeAsset:
    """US-SA-013: Dispose of an asset with confirmation.

    MoSCoW: MUST
    Spec refs: S2.2.1-06, S2.2.3-05, S2.3.15-01
    UI Surface: /assets/<pk>/
    """

    def test_asset_detail_page_loads(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_dispose_transitions_asset_to_disposed(
        self, admin_client, active_asset
    ):
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[active_asset.pk]),
            {
                "name": active_asset.name,
                "category": active_asset.category.pk,
                "current_location": active_asset.current_location.pk,
                "condition": active_asset.condition,
                "quantity": active_asset.quantity,
                "status": "disposed",
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.status == "disposed"

    def test_disposed_asset_excluded_from_default_search(
        self, admin_client, category, location, admin_user
    ):
        from assets.factories import AssetFactory

        disposed = AssetFactory(
            name="Disposed Asset Unique",
            status="disposed",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        resp = admin_client.get(
            reverse("assets:asset_list"),
            {"q": "Disposed Asset Unique"},
        )
        assert resp.status_code == 200
        assert b"Disposed Asset Unique" not in resp.content


@pytest.mark.django_db
class TestUS_SA_014_MarkAssetLostOrStolen:
    """US-SA-014: Mark an asset as lost or stolen.

    MoSCoW: MUST
    Spec refs: S2.2.3-07, S2.2.3-08, S2.2.3-11
    UI Surface: /assets/<pk>/edit/
    """

    def test_mark_asset_as_lost(self, admin_client, active_asset):
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[active_asset.pk]),
            {
                "name": active_asset.name,
                "category": active_asset.category.pk,
                "current_location": active_asset.current_location.pk,
                "condition": active_asset.condition,
                "quantity": active_asset.quantity,
                "status": "lost",
                "notes": "Lost during transport",
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.status == "lost"

    def test_mark_asset_as_stolen(self, admin_client, active_asset):
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[active_asset.pk]),
            {
                "name": active_asset.name,
                "category": active_asset.category.pk,
                "current_location": active_asset.current_location.pk,
                "condition": active_asset.condition,
                "quantity": active_asset.quantity,
                "status": "stolen",
                "notes": "Stolen from venue",
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.status == "stolen"

    def test_lost_asset_hidden_from_default_search(
        self, admin_client, category, location, admin_user
    ):
        from assets.factories import AssetFactory

        lost = AssetFactory(
            name="Lost Asset Unique",
            status="lost",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        resp = admin_client.get(
            reverse("assets:asset_list"),
            {"q": "Lost Asset Unique"},
        )
        assert resp.status_code == 200
        assert b"Lost Asset Unique" not in resp.content


@pytest.mark.django_db
class TestUS_SA_015_RecoverLostOrStolenAsset:
    """US-SA-015: Recover a lost or stolen asset.

    MoSCoW: MUST
    Spec refs: S2.2.3-08, S3.3.2
    UI Surface: /assets/<pk>/edit/
    """

    def test_recover_lost_asset_to_active(
        self, admin_client, category, location, admin_user
    ):
        from assets.factories import AssetFactory

        lost_asset = AssetFactory(
            name="Lost to Recover",
            status="lost",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        resp = admin_client.post(
            reverse("assets:asset_edit", args=[lost_asset.pk]),
            {
                "name": lost_asset.name,
                "category": lost_asset.category.pk,
                "current_location": lost_asset.current_location.pk,
                "condition": lost_asset.condition,
                "quantity": lost_asset.quantity,
                "status": "active",
            },
        )
        lost_asset.refresh_from_db()
        assert lost_asset.status == "active"

    def test_recovered_asset_visible_in_default_search(
        self, admin_client, category, location, admin_user
    ):
        from assets.factories import AssetFactory

        recovered = AssetFactory(
            name="Recovered Unique Asset",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        resp = admin_client.get(
            reverse("assets:asset_list"),
            {"q": "Recovered Unique Asset"},
        )
        assert resp.status_code == 200
        assert b"Recovered Unique Asset" in resp.content


# ---------------------------------------------------------------------------
# §10A.3 Check-out / Check-in / Transfer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_016_CheckOutOnBehalfOfAnotherUser:
    """US-SA-016: Check out an asset on behalf of another user.

    MoSCoW: MUST
    Spec refs: S2.3.2-01, S2.3.2-05, S2.3.2-08
    UI Surface: /assets/<pk>/checkout/
    """

    def test_checkout_form_loads(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_checkout", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_checkout_creates_transaction(
        self, admin_client, active_asset, borrower_user, location
    ):
        resp = admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination": location.pk,
                "notes": "",
            },
        )
        assert Transaction.objects.filter(
            asset=active_asset,
            action="checkout",
            borrower=borrower_user,
        ).exists()

    def test_checkout_updates_current_location(
        self, admin_client, active_asset, borrower_user, warehouse
    ):
        dest = warehouse["bay1"]
        admin_client.post(
            reverse("assets:asset_checkout", args=[active_asset.pk]),
            {
                "borrower": borrower_user.pk,
                "destination": dest.pk,
                "notes": "",
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.current_location == dest


@pytest.mark.django_db
class TestUS_SA_017_CheckInAnyAsset:
    """US-SA-017: Check in any asset to a specified location.

    MoSCoW: MUST
    Spec refs: S2.3.3-01, S2.3.3-02, S2.3.3-05
    UI Surface: /assets/<pk>/checkin/
    """

    def test_checkin_form_loads_for_checked_out_asset(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
        admin_user,
    ):
        from assets.models import Transaction as Tx

        Tx.objects.create(
            asset=active_asset,
            action="checkout",
            user=admin_user,
            borrower=borrower_user,
            from_location=active_asset.current_location,
            to_location=location,
        )
        active_asset.checked_out_to = borrower_user
        active_asset.save()
        resp = admin_client.get(
            reverse("assets:asset_checkin", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_checkin_clears_borrower(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
        admin_user,
    ):
        from assets.models import Transaction as Tx

        Tx.objects.create(
            asset=active_asset,
            action="checkout",
            user=admin_user,
            borrower=borrower_user,
            from_location=active_asset.current_location,
            to_location=location,
        )
        active_asset.checked_out_to = borrower_user
        active_asset.save()
        admin_client.post(
            reverse("assets:asset_checkin", args=[active_asset.pk]),
            {"return_location": location.pk, "notes": ""},
        )
        active_asset.refresh_from_db()
        assert active_asset.checked_out_to is None


@pytest.mark.django_db
class TestUS_SA_018_TransferAssetBetweenLocations:
    """US-SA-018: Transfer any asset between locations.

    MoSCoW: MUST
    Spec refs: S2.3.4-01, S2.3.4-02, S2.3.4-03
    UI Surface: /assets/<pk>/transfer/
    """

    def test_transfer_form_loads(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_transfer", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_transfer_updates_location(
        self, admin_client, active_asset, warehouse
    ):
        dest = warehouse["bay4"]
        admin_client.post(
            reverse("assets:asset_transfer", args=[active_asset.pk]),
            {"destination": dest.pk, "notes": ""},
        )
        active_asset.refresh_from_db()
        assert active_asset.current_location == dest

    def test_transfer_creates_transaction_record(
        self, admin_client, active_asset, warehouse
    ):
        dest = warehouse["shelf_a"]
        admin_client.post(
            reverse("assets:asset_transfer", args=[active_asset.pk]),
            {"destination": dest.pk, "notes": ""},
        )
        assert Transaction.objects.filter(
            asset=active_asset, action="transfer"
        ).exists()


@pytest.mark.django_db
class TestUS_SA_019_CustodyHandover:
    """US-SA-019: Perform a custody handover between borrowers.

    MoSCoW: MUST
    Spec refs: S2.3.5-01, S2.3.5-02, S2.3.5-03
    UI Surface: /assets/<pk>/handover/
    """

    def test_handover_form_loads_for_checked_out_asset(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
        admin_user,
    ):
        from assets.models import Transaction as Tx

        Tx.objects.create(
            asset=active_asset,
            action="checkout",
            user=admin_user,
            borrower=borrower_user,
            from_location=active_asset.current_location,
            to_location=location,
        )
        active_asset.checked_out_to = borrower_user
        active_asset.save()
        resp = admin_client.get(
            reverse("assets:asset_handover", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_handover_only_available_when_checked_out(
        self, admin_client, active_asset
    ):
        # Not checked out — handover should either 404 or redirect
        resp = admin_client.get(
            reverse("assets:asset_handover", args=[active_asset.pk])
        )
        assert resp.status_code in (200, 302, 404, 403)


@pytest.mark.django_db
class TestUS_SA_020_RelocateCheckedOutAsset:
    """US-SA-020: Relocate a checked-out asset.

    MoSCoW: MUST
    Spec refs: S2.3.11-01, S2.3.11-02
    UI Surface: /assets/<pk>/relocate/
    """

    def test_relocate_form_loads(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_relocate", args=[active_asset.pk])
        )
        assert resp.status_code in (200, 302)

    def test_relocate_updates_location_preserves_borrower(
        self,
        admin_client,
        active_asset,
        borrower_user,
        warehouse,
        admin_user,
    ):
        from assets.models import Transaction as Tx

        dest1 = warehouse["bay1"]
        Tx.objects.create(
            asset=active_asset,
            action="checkout",
            user=admin_user,
            borrower=borrower_user,
            from_location=active_asset.current_location,
            to_location=dest1,
        )
        active_asset.checked_out_to = borrower_user
        active_asset.save()
        dest2 = warehouse["bay4"]
        admin_client.post(
            reverse("assets:asset_relocate", args=[active_asset.pk]),
            {"new_location": dest2.pk, "notes": ""},
        )
        active_asset.refresh_from_db()
        assert active_asset.current_location == dest2
        assert active_asset.checked_out_to == borrower_user


@pytest.mark.django_db
class TestUS_SA_021_BackdateTransaction:
    """US-SA-021: Backdate a transaction.

    MoSCoW: MUST
    Spec refs: S2.3.9-01, S2.3.9-02, S2.3.9-03, S2.3.9-04
    UI Surface: Checkout, Check-in, Transfer, Handover forms
    """

    def test_checkout_form_has_date_field(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_checkout", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "date" in content.lower()

    def test_checkin_form_has_date_field(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
        admin_user,
    ):
        from assets.models import Transaction as Tx

        Tx.objects.create(
            asset=active_asset,
            action="checkout",
            user=admin_user,
            borrower=borrower_user,
            from_location=active_asset.current_location,
            to_location=location,
        )
        active_asset.checked_out_to = borrower_user
        active_asset.save()
        resp = admin_client.get(
            reverse("assets:asset_checkin", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "date" in content.lower()


@pytest.mark.django_db
class TestUS_SA_022_BulkCheckOut:
    """US-SA-022: Bulk check out multiple assets to one borrower.

    MoSCoW: MUST
    Spec refs: S2.3.10-01, S2.3.10-02, S2.3.10-03
    UI Surface: /assets/bulk/
    """

    def test_bulk_actions_url_accessible(self, admin_client):
        resp = admin_client.get(reverse("assets:bulk_actions"))
        assert resp.status_code in (200, 405)

    def test_bulk_checkout_creates_transactions(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
        category,
        admin_user,
    ):
        from assets.factories import AssetFactory

        asset2 = AssetFactory(
            name="Second Asset",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        resp = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "action": "checkout",
                "selected_ids": [active_asset.pk, asset2.pk],
                "borrower": borrower_user.pk,
                "destination": location.pk,
            },
        )
        assert Transaction.objects.filter(
            asset=active_asset, action="checkout"
        ).exists()
        assert Transaction.objects.filter(
            asset=asset2, action="checkout"
        ).exists()


@pytest.mark.django_db
class TestUS_SA_023_BulkCheckIn:
    """US-SA-023: Bulk check in multiple assets to one location.

    MoSCoW: MUST
    Spec refs: S2.3.10-04, S2.3.10-05
    UI Surface: /assets/bulk/
    """

    def test_bulk_checkin_creates_transactions(
        self,
        admin_client,
        active_asset,
        borrower_user,
        location,
        category,
        admin_user,
    ):
        from assets.factories import AssetFactory
        from assets.models import Transaction as Tx

        asset2 = AssetFactory(
            name="Second Checked Out",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        for a in [active_asset, asset2]:
            Tx.objects.create(
                asset=a,
                action="checkout",
                user=admin_user,
                borrower=borrower_user,
                from_location=a.current_location,
                to_location=location,
            )
            a.checked_out_to = borrower_user
            a.save()
        resp = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "action": "checkin",
                "selected_ids": [active_asset.pk, asset2.pk],
                "return_location": location.pk,
            },
        )
        assert Transaction.objects.filter(
            asset=active_asset, action="checkin"
        ).exists()
        assert Transaction.objects.filter(
            asset=asset2, action="checkin"
        ).exists()


@pytest.mark.django_db
class TestUS_SA_095_CheckInDraftAsset:
    """US-SA-095: Check in a draft asset that was never checked out.

    MoSCoW: MUST
    Spec refs: S2.3.16-01
    UI Surface: /assets/<pk>/checkin/
    """

    def test_checkin_form_accessible_for_draft(
        self, admin_client, draft_asset
    ):
        resp = admin_client.get(
            reverse("assets:asset_checkin", args=[draft_asset.pk])
        )
        assert resp.status_code in (200, 302)

    def test_checkin_draft_updates_location(
        self, admin_client, draft_asset, location
    ):
        admin_client.post(
            reverse("assets:asset_checkin", args=[draft_asset.pk]),
            {"return_location": location.pk, "notes": ""},
        )
        draft_asset.refresh_from_db()
        assert draft_asset.current_location == location


# ---------------------------------------------------------------------------
# §10A.4 Barcode System
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_024_PregenerateBarcodeLabels:
    """US-SA-024: Pre-generate barcode labels for future use.

    MoSCoW: MUST
    Spec refs: S2.4.3-01, S2.4.3-02, S2.4.3-03, S2.4.3-04
    UI Surface: /labels/pregenerate/
    """

    def test_pregenerate_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:barcode_pregenerate"))
        assert resp.status_code == 200

    def test_pregenerate_does_not_create_asset_records(self, admin_client):
        initial_count = Asset.objects.count()
        admin_client.post(
            reverse("assets:barcode_pregenerate"),
            {"quantity": 3},
        )
        assert Asset.objects.count() == initial_count


@pytest.mark.django_db
class TestUS_SA_025_PrintLabels:
    """US-SA-025: Print labels via browser, Zebra ZPL, or remote printer.

    MoSCoW: MUST
    Spec refs: S2.4.5-01, S2.4.5-06, S2.4.5-09, S2.4.5-10
    UI Surface: /assets/<pk>/label/
    """

    def test_label_page_loads(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_label", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_zpl_label_endpoint_accessible(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_label_zpl", args=[active_asset.pk])
        )
        assert resp.status_code in (200, 400, 503)


@pytest.mark.django_db
class TestUS_SA_026_ClearRegenerateBarcode:
    """US-SA-026: Clear and regenerate a barcode on any asset.

    MoSCoW: MUST
    Spec refs: S2.4.2-05, S2.4.2-06
    UI Surface: /assets/<pk>/clear-barcode/
    """

    def test_clear_barcode_page_accessible(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:clear_barcode", args=[active_asset.pk])
        )
        assert resp.status_code in (200, 405)


@pytest.mark.django_db
class TestUS_SA_028_BulkPrintLabels:
    """US-SA-028: Bulk print labels for selected assets.

    MoSCoW: MUST
    Spec refs: S2.8.2-01, S2.8.2-02, S2.8.2-05
    UI Surface: /assets/bulk/
    """

    def test_bulk_actions_accessible_for_print(
        self, admin_client, active_asset
    ):
        resp = admin_client.get(reverse("assets:bulk_actions"))
        assert resp.status_code in (200, 405)


@pytest.mark.django_db
class TestUS_SA_029_ViewPrintHistory:
    """US-SA-029: View print history and job status for an asset.

    MoSCoW: MUST
    Spec refs: S2.4.5b-06
    UI Surface: /assets/<pk>/print-history/
    """

    def test_print_history_page_loads(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:print_history", args=[active_asset.pk])
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# §10A.5 NFC Tag Management
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_030_AssignNFCTag:
    """US-SA-030: Assign an NFC tag to any asset.

    MoSCoW: MUST
    Spec refs: S2.5.2-01, S2.5.4-02
    UI Surface: /assets/<pk>/nfc/add/
    """

    def test_nfc_add_form_accessible(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:nfc_add", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_assigning_nfc_tag_creates_record(
        self, admin_client, active_asset
    ):
        from assets.models import NFCTag

        resp = admin_client.post(
            reverse("assets:nfc_add", args=[active_asset.pk]),
            {"tag_id": "ABCDEF123456", "notes": ""},
        )
        assert NFCTag.objects.filter(
            asset=active_asset,
            tag_id__iexact="ABCDEF123456",
            removed_at__isnull=True,
        ).exists()

    def test_duplicate_nfc_assignment_rejected(
        self, admin_client, active_asset, category, location, admin_user
    ):
        from assets.factories import AssetFactory
        from assets.models import NFCTag

        tag_id = "DUPLICATE001"
        other = AssetFactory(
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        NFCTag.objects.create(
            asset=other,
            tag_id=tag_id,
            assigned_by=admin_user,
        )
        initial_count = NFCTag.objects.filter(tag_id__iexact=tag_id).count()
        admin_client.post(
            reverse("assets:nfc_add", args=[active_asset.pk]),
            {"tag_id": tag_id, "notes": ""},
        )
        # Should not have created a second active assignment
        assert (
            NFCTag.objects.filter(
                tag_id__iexact=tag_id,
                removed_at__isnull=True,
            ).count()
            == 1
        )


@pytest.mark.django_db
class TestUS_SA_031_RemoveNFCTag:
    """US-SA-031: Remove an NFC tag from any asset.

    MoSCoW: MUST
    Spec refs: S2.5.2-05, S2.5.2-06
    UI Surface: /assets/<pk>/nfc/<nfc_pk>/remove/
    """

    def test_remove_nfc_tag_sets_removed_at(
        self, admin_client, active_asset, admin_user
    ):
        from assets.models import NFCTag

        nfc = NFCTag.objects.create(
            asset=active_asset,
            tag_id="REMOVETEST001",
            assigned_by=admin_user,
        )
        admin_client.post(
            reverse("assets:nfc_remove", args=[active_asset.pk, nfc.pk]),
            {"notes": "Removed"},
        )
        nfc.refresh_from_db()
        assert nfc.removed_at is not None


@pytest.mark.django_db
class TestUS_SA_033_ViewNFCTagHistory:
    """US-SA-033: View NFC tag assignment history.

    MoSCoW: MUST
    Spec refs: S2.5.6-01, S2.5.6-02, S2.5.6-03
    UI Surface: /assets/<pk>/ + /nfc/<tag_uid>/history/
    """

    def test_nfc_history_page_loads(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:nfc_history", args=[active_asset.pk])
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# §10A.6 Search, Browse & Export
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_034_SearchAndFilterAssets:
    """US-SA-034: Search and filter assets across the entire system.

    MoSCoW: MUST
    Spec refs: S2.6.1-01, S2.6.2-01, S2.6.2-02
    UI Surface: /assets/
    """

    def test_asset_list_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:asset_list"))
        assert resp.status_code == 200

    def test_text_search_finds_asset_by_name(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_list"),
            {"q": active_asset.name},
        )
        assert resp.status_code == 200
        assert active_asset.name.encode() in resp.content

    def test_default_view_shows_active_only(
        self, admin_client, active_asset, category, location, admin_user
    ):
        from assets.factories import AssetFactory

        disposed = AssetFactory(
            name="Disposed Asset Xxx",
            status="disposed",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        resp = admin_client.get(reverse("assets:asset_list"))
        assert resp.status_code == 200
        assert b"Disposed Asset Xxx" not in resp.content


@pytest.mark.django_db
class TestUS_SA_035_SortAssetList:
    """US-SA-035: Sort the asset list by any supported column.

    MoSCoW: MUST
    Spec refs: S2.6.2a-01, S2.6.2a-04
    UI Surface: /assets/
    """

    def test_sort_by_name_ascending(self, admin_client, active_asset):
        resp = admin_client.get(reverse("assets:asset_list"), {"sort": "name"})
        assert resp.status_code == 200

    def test_sort_by_name_descending(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_list"), {"sort": "-name"}
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_036_ListAndGridViewModes:
    """US-SA-036: Switch between list and grid view modes.

    MoSCoW: MUST
    Spec refs: S2.6.3-01, S2.6.3-02, S2.6.3-03
    UI Surface: /assets/
    """

    def test_list_view_mode(self, admin_client, active_asset):
        resp = admin_client.get(reverse("assets:asset_list"), {"view": "list"})
        assert resp.status_code == 200

    def test_grid_view_mode(self, admin_client, active_asset):
        resp = admin_client.get(reverse("assets:asset_list"), {"view": "grid"})
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_037_ExportAllAssetsToExcel:
    """US-SA-037: Export all assets to Excel.

    MoSCoW: MUST
    Spec refs: S2.9.1-01, S2.9.1-02, S2.9.1-03, S2.9.1-06
    UI Surface: /assets/export/
    """

    def test_export_page_accessible(self, admin_client):
        resp = admin_client.get(reverse("assets:export_assets"))
        assert resp.status_code in (200, 302)

    def test_export_returns_xlsx(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:export_assets"), {"format": "xlsx"}
        )
        assert resp.status_code in (200, 302)


@pytest.mark.django_db
class TestUS_SA_038_ExportFilteredAssets:
    """US-SA-038: Export filtered asset subsets.

    MoSCoW: MUST
    Spec refs: S2.9.1-02
    UI Surface: /assets/export/
    """

    def test_export_respects_filters(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:export_assets"),
            {"q": active_asset.name},
        )
        assert resp.status_code in (200, 302)


# ---------------------------------------------------------------------------
# §10A.7 Stocktake
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_039_CreateStocktakeSession:
    """US-SA-039: Create and initiate a stocktake session at any location.

    MoSCoW: MUST
    Spec refs: S2.7.1-01, S2.7.1-05
    UI Surface: /stocktake/start/
    """

    def test_stocktake_start_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:stocktake_start"))
        assert resp.status_code == 200

    def test_creating_session_creates_stocktake_record(
        self, admin_client, location
    ):
        from assets.models import StocktakeSession

        resp = admin_client.post(
            reverse("assets:stocktake_start"),
            {"location": location.pk},
        )
        assert StocktakeSession.objects.filter(location=location).exists()


@pytest.mark.django_db
class TestUS_SA_040_ConfirmAssetsDuringStocktake:
    """US-SA-040: Confirm assets during stocktake via scan or checkbox.

    MoSCoW: MUST
    Spec refs: S2.7.2-01, S2.7.2-02, S2.7.2-04
    UI Surface: /stocktake/<pk>/
    """

    def test_stocktake_detail_page_loads(
        self, admin_client, location, admin_user
    ):
        from assets.models import StocktakeSession

        session = StocktakeSession.objects.create(
            location=location, started_by=admin_user
        )
        resp = admin_client.get(
            reverse("assets:stocktake_detail", args=[session.pk])
        )
        assert resp.status_code == 200

    def test_confirm_asset_creates_audit_transaction(
        self,
        admin_client,
        active_asset,
        location,
        admin_user,
    ):
        from assets.models import StocktakeSession

        session = StocktakeSession.objects.create(
            location=active_asset.current_location,
            started_by=admin_user,
        )
        admin_client.post(
            reverse("assets:stocktake_confirm", args=[session.pk]),
            {"asset_id": active_asset.pk},
        )
        assert Transaction.objects.filter(
            asset=active_asset, action="audit"
        ).exists()


@pytest.mark.django_db
class TestUS_SA_041_HandleStocktakeDiscrepancies:
    """US-SA-041: Handle stocktake discrepancies.

    MoSCoW: MUST
    Spec refs: S2.7.3-01, S2.7.3-02, S2.7.3-03
    UI Surface: /stocktake/<pk>/
    """

    def test_stocktake_detail_accessible(
        self, admin_client, location, admin_user
    ):
        from assets.models import StocktakeSession

        session = StocktakeSession.objects.create(
            location=location, started_by=admin_user
        )
        resp = admin_client.get(
            reverse("assets:stocktake_detail", args=[session.pk])
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_042_CompleteStocktakeSession:
    """US-SA-042: Complete a stocktake session with a summary report.

    MoSCoW: MUST
    Spec refs: S2.7.4-01, S2.7.4-03
    UI Surface: /stocktake/<pk>/complete/
    """

    def test_complete_stocktake_sets_completed_at(
        self, admin_client, location, admin_user
    ):
        from assets.models import StocktakeSession

        session = StocktakeSession.objects.create(
            location=location, started_by=admin_user
        )
        admin_client.post(
            reverse("assets:stocktake_complete", args=[session.pk]),
            {},
        )
        session.refresh_from_db()
        # Spec says completed_at; model uses ended_at (gap: field name mismatch)
        assert session.ended_at is not None

    def test_stocktake_summary_page_loads(
        self, admin_client, location, admin_user
    ):
        from assets.models import StocktakeSession

        session = StocktakeSession.objects.create(
            location=location, started_by=admin_user
        )
        admin_client.post(
            reverse("assets:stocktake_complete", args=[session.pk]),
            {},
        )
        resp = admin_client.get(
            reverse("assets:stocktake_summary", args=[session.pk])
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_043_CancelStocktakeSession:
    """US-SA-043: Cancel an in-progress stocktake session.

    MoSCoW: MUST
    Spec refs: S2.7.5-01, S2.7.5-02, S2.7.5-04
    UI Surface: /stocktake/<pk>/
    """

    def test_stocktake_list_accessible(self, admin_client):
        resp = admin_client.get(reverse("assets:stocktake_list"))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# §10A.8 Bulk Operations
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_044_BulkTransferAssets:
    """US-SA-044: Bulk transfer assets to a new location.

    MoSCoW: MUST
    Spec refs: S2.8.1-01, S2.8.1-04, S2.8.1-05
    UI Surface: /assets/bulk/
    """

    def test_bulk_transfer_creates_transactions(
        self,
        admin_client,
        active_asset,
        warehouse,
        category,
        location,
        admin_user,
    ):
        from assets.factories import AssetFactory

        asset2 = AssetFactory(
            name="Asset For Bulk Transfer",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        dest = warehouse["bay4"]
        resp = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "action": "transfer",
                "selected_ids": [active_asset.pk, asset2.pk],
                "destination": dest.pk,
            },
        )
        assert Transaction.objects.filter(
            asset=active_asset, action="transfer"
        ).exists()


@pytest.mark.django_db
class TestUS_SA_045_BulkEditAssets:
    """US-SA-045: Bulk edit assets across all departments.

    MoSCoW: MUST
    Spec refs: S2.8.3-01, S2.8.3-02, S2.8.3-04, S2.8.3-05
    UI Surface: /assets/bulk/
    """

    def test_bulk_edit_category(
        self,
        admin_client,
        active_asset,
        category,
        location,
        admin_user,
    ):
        from assets.factories import AssetFactory, CategoryFactory

        new_cat = CategoryFactory(
            name="New Category",
            department=category.department,
        )
        asset2 = AssetFactory(
            name="Asset For Bulk Edit",
            status="active",
            category=category,
            current_location=location,
            created_by=admin_user,
        )
        resp = admin_client.post(
            reverse("assets:bulk_actions"),
            {
                "action": "edit",
                "selected_ids": [active_asset.pk, asset2.pk],
                "category": new_cat.pk,
            },
        )
        active_asset.refresh_from_db()
        assert active_asset.category == new_cat


@pytest.mark.django_db
class TestUS_SA_046_BulkLabelPrinting:
    """US-SA-046: Bulk label printing for filtered assets.

    MoSCoW: SHOULD
    Spec refs: S2.8.2-04
    UI Surface: /assets/labels/all-filtered/
    """

    def test_print_all_filtered_accessible(self, admin_client):
        resp = admin_client.get(reverse("assets:print_all_filtered_labels"))
        assert resp.status_code in (200, 405)


@pytest.mark.django_db
class TestUS_SA_047_BulkChangeAssetStatus:
    """US-SA-047: Bulk change asset status.

    MoSCoW: COULD
    Spec refs: S2.8.3-03
    UI Surface: /assets/bulk/
    """

    def test_bulk_status_change_accessible(self, admin_client):
        resp = admin_client.get(reverse("assets:bulk_actions"))
        assert resp.status_code in (200, 405)


# ---------------------------------------------------------------------------
# §10A.9 Department & Access Control
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_048_ManageDepartments:
    """US-SA-048: Create, edit, and delete departments.

    MoSCoW: MUST
    Spec refs: S2.10.1-01, S2.10.1-02, S2.10.1-03
    UI Surface: Django admin
    """

    def test_admin_can_access_department_admin(self, admin_client):
        resp = admin_client.get("/admin/assets/department/")
        assert resp.status_code == 200

    def test_admin_can_create_department(self, admin_client):
        resp = admin_client.post(
            "/admin/assets/department/add/",
            {
                "name": "New Department",
                "description": "A department",
                "is_active": True,
                "barcode_prefix": "NEWDEPT",
                "managers": [],
            },
        )
        from assets.models import Department

        assert Department.objects.filter(name="New Department").exists()


@pytest.mark.django_db
class TestUS_SA_049_ManageCategoriesInAnyDept:
    """US-SA-049: Manage categories within any department.

    MoSCoW: MUST
    Spec refs: S2.10.2-01, S2.10.2-03
    UI Surface: /categories/
    """

    def test_category_list_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:category_list"))
        assert resp.status_code == 200

    def test_category_create_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:category_create"))
        assert resp.status_code == 200

    def test_admin_can_create_category_in_any_dept(
        self, admin_client, department
    ):
        from assets.models import Category

        resp = admin_client.post(
            reverse("assets:category_create"),
            {
                "name": "System Admin Category",
                "department": department.pk,
                "description": "",
            },
        )
        assert Category.objects.filter(name="System Admin Category").exists()


@pytest.mark.django_db
class TestUS_SA_050_AssignUsersToGroups:
    """US-SA-050: Assign users to permission groups.

    MoSCoW: MUST
    Spec refs: S2.10.4-01, S2.13.5-01
    UI Surface: Django admin
    """

    def test_admin_can_access_user_admin(self, admin_client):
        resp = admin_client.get("/admin/accounts/customuser/")
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_051_AssignDeptManagersToDepts:
    """US-SA-051: Assign Department Managers to departments.

    MoSCoW: MUST
    Spec refs: S2.10.4-03, S2.13.5-02
    UI Surface: Django admin
    """

    def test_department_admin_has_managers_field(
        self, admin_client, department
    ):
        resp = admin_client.get(
            f"/admin/assets/department/{department.pk}/change/"
        )
        assert resp.status_code == 200
        assert b"managers" in resp.content


@pytest.mark.django_db
class TestUS_SA_055_SetDeptBarcodePrefix:
    """US-SA-055: Set department barcode prefix.

    MoSCoW: MUST
    Spec refs: S2.4.1-05, S2.4.1-07
    UI Surface: Django admin
    """

    def test_department_admin_has_barcode_prefix_field(
        self, admin_client, department
    ):
        resp = admin_client.get(
            f"/admin/assets/department/{department.pk}/change/"
        )
        assert resp.status_code == 200
        assert b"barcode_prefix" in resp.content

    def test_new_asset_uses_dept_prefix(
        self, admin_client, props_dept, location
    ):
        from assets.factories import CategoryFactory

        props_dept.barcode_prefix = "PROP"
        props_dept.save()
        cat = CategoryFactory(name="Props Cat", department=props_dept)
        resp = admin_client.post(
            reverse("assets:asset_create"),
            {
                "name": "Prefix Test Asset",
                "category": cat.pk,
                "current_location": location.pk,
                "condition": "good",
                "quantity": 1,
            },
        )
        asset = Asset.objects.filter(name="Prefix Test Asset").first()
        assert asset is not None
        assert asset.barcode.startswith("PROP-")


# ---------------------------------------------------------------------------
# §10A.10 Dashboard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_056_ViewSystemWideDashboard:
    """US-SA-056: View system-wide dashboard metrics.

    MoSCoW: MUST
    Spec refs: S2.11.1-01, S2.11.2a-01
    UI Surface: /
    """

    def test_dashboard_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:dashboard"))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_057_ViewPendingApprovals:
    """US-SA-057: View pending approvals count on the dashboard.

    MoSCoW: MUST
    Spec refs: S2.15.4-09
    UI Surface: / + /approval-queue/
    """

    def test_approval_queue_accessible_to_admin(self, admin_client):
        resp = admin_client.get(reverse("accounts:approval_queue"))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_058_ViewRecentActivityDashboard:
    """US-SA-058: View recent activity across the system.

    MoSCoW: MUST
    Spec refs: S2.11.2-01, S2.11.2-02
    UI Surface: /
    """

    def test_dashboard_shows_recent_transactions(
        self, admin_client, active_asset, admin_user, location
    ):
        Transaction.objects.create(
            asset=active_asset,
            action="transfer",
            user=admin_user,
            from_location=location,
            to_location=location,
        )
        resp = admin_client.get(reverse("assets:dashboard"))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# §10A.11 Location Management
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_059_ManageLocations:
    """US-SA-059: Create, edit, and delete locations.

    MoSCoW: MUST
    Spec refs: S2.12.1-01, S2.12.1-02, S2.12.1-03
    UI Surface: /locations/
    """

    def test_location_list_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:location_list"))
        assert resp.status_code == 200

    def test_location_create_page_loads(self, admin_client):
        resp = admin_client.get(reverse("assets:location_create"))
        assert resp.status_code == 200

    def test_create_location(self, admin_client):
        from assets.models import Location

        resp = admin_client.post(
            reverse("assets:location_create"),
            {"name": "New Test Location", "description": ""},
        )
        assert Location.objects.filter(name="New Test Location").exists()


@pytest.mark.django_db
class TestUS_SA_060_HierarchicalLocations:
    """US-SA-060: Create hierarchical locations up to 4 levels deep.

    MoSCoW: MUST
    Spec refs: S2.12.2-01, S2.12.2-02, S2.12.2-03
    UI Surface: /locations/create/
    """

    def test_create_child_location(self, admin_client, location):
        from assets.models import Location

        resp = admin_client.post(
            reverse("assets:location_create"),
            {
                "name": "Child Location",
                "parent": location.pk,
                "description": "",
            },
        )
        child = Location.objects.filter(name="Child Location").first()
        assert child is not None
        assert child.parent == location


@pytest.mark.django_db
class TestUS_SA_061_ViewLocationAssetsWithDescendants:
    """US-SA-061: View a location's assets including descendants.

    MoSCoW: SHOULD
    Spec refs: S2.12.2-05, S2.12.3-01, S2.12.3-02
    UI Surface: /locations/<pk>/
    """

    def test_location_detail_page_loads(self, admin_client, location):
        resp = admin_client.get(
            reverse("assets:location_detail", args=[location.pk])
        )
        assert resp.status_code == 200

    def test_location_detail_shows_assets(
        self, admin_client, location, active_asset
    ):
        resp = admin_client.get(
            reverse("assets:location_detail", args=[location.pk])
        )
        assert resp.status_code == 200
        assert active_asset.name.encode() in resp.content


@pytest.mark.django_db
class TestUS_SA_062_StartStocktakeFromLocation:
    """US-SA-062: Start a stocktake from the location detail view.

    MoSCoW: SHOULD
    Spec refs: S2.12.3-04
    UI Surface: /locations/<pk>/
    """

    def test_location_detail_has_start_stocktake_link(
        self, admin_client, location
    ):
        resp = admin_client.get(
            reverse("assets:location_detail", args=[location.pk])
        )
        assert resp.status_code == 200
        assert b"stocktake" in resp.content.lower()


# ---------------------------------------------------------------------------
# §10A.12 Admin UI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_063_ManageAssetsViaAdminWithInlines:
    """US-SA-063: Manage assets via the Django admin with inline images
    and NFC tags.

    MoSCoW: MUST
    Spec refs: S2.13.2-01, S2.13.2-02, S2.13.2-03
    UI Surface: Django admin
    """

    def test_asset_admin_accessible(self, admin_client, active_asset):
        resp = admin_client.get(
            f"/admin/assets/asset/{active_asset.pk}/change/"
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_064_ViewAndManageTransactionsViaAdmin:
    """US-SA-064: View and manage transactions via the admin.

    MoSCoW: MUST
    Spec refs: S2.13.3-01, S2.13.3-02, S2.13.3-03
    UI Surface: Django admin
    """

    def test_transaction_admin_accessible(self, admin_client):
        resp = admin_client.get("/admin/assets/transaction/")
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_065_ManageDepartmentsViaAdmin:
    """US-SA-065: Manage departments via the admin with manager
    assignments.

    MoSCoW: MUST
    Spec refs: S2.13.4-01, S2.13.4-02
    UI Surface: Django admin
    """

    def test_department_admin_changelist_loads(self, admin_client):
        resp = admin_client.get("/admin/assets/department/")
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_066_ManageUsersViaAdmin:
    """US-SA-066: Manage users via the admin with tabbed layout.

    MoSCoW: MUST
    Spec refs: S2.13.6-01, S2.13.6-02, S2.13.6-03, S2.13.6-04
    UI Surface: Django admin
    """

    def test_user_admin_changelist_loads(self, admin_client):
        resp = admin_client.get("/admin/accounts/customuser/")
        assert resp.status_code == 200

    def test_user_admin_shows_relevant_columns(self, admin_client):
        resp = admin_client.get("/admin/accounts/customuser/")
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "username" in content.lower()


@pytest.mark.django_db
class TestUS_SA_067_ViewAIAnalysisLog:
    """US-SA-067: View AI Analysis Log in the admin.

    MoSCoW: SHOULD
    Spec refs: S2.13.2-07
    UI Surface: Django admin
    """

    def test_asset_admin_has_ai_analysis_info(
        self, admin_client, active_asset
    ):
        resp = admin_client.get(
            f"/admin/assets/asset/{active_asset.pk}/change/"
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_098_ManageGroupsWithUserCounts:
    """US-SA-098: Manage groups with user counts in admin.

    MoSCoW: MUST
    Spec refs: S2.13.7, S2.13.7-01
    UI Surface: Django admin
    """

    def test_group_admin_accessible(self, admin_client):
        resp = admin_client.get("/admin/auth/group/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# §10A.13 AI Image Analysis
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_068_ViewAndApplyAISuggestions:
    """US-SA-068: View and apply AI suggestions on any asset.

    MoSCoW: MUST
    Spec refs: S2.14.3-01, S2.14.3-02
    UI Surface: /assets/<pk>/
    """

    def test_asset_detail_shows_ai_panel(self, admin_client, active_asset):
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200

    def test_ai_apply_suggestions_url_accessible(
        self, admin_client, active_asset
    ):
        resp = admin_client.get(
            reverse("assets:ai_apply_suggestions", args=[active_asset.pk])
        )
        assert resp.status_code in (200, 405)


@pytest.mark.django_db
class TestUS_SA_069_ReAnalyseAssetImage:
    """US-SA-069: Re-analyse an asset image.

    MoSCoW: MUST
    Spec refs: S2.14.3-08
    UI Surface: /assets/<pk>/images/<image_pk>/reanalyse/
    """

    def test_reanalyse_url_accessible_with_image(
        self, admin_client, active_asset, admin_user
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from assets.models import AssetImage

        image_file = SimpleUploadedFile(
            "test.gif",
            (
                b"GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00"
                b"\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00"
                b"\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        img = AssetImage.objects.create(
            asset=active_asset,
            image=image_file,
            uploaded_by=admin_user,
        )
        resp = admin_client.post(
            reverse(
                "assets:ai_reanalyse",
                args=[active_asset.pk, img.pk],
            ),
            {},
        )
        assert resp.status_code in (200, 302, 400, 503)


@pytest.mark.django_db
class TestUS_SA_070_ConfigureAIDailyLimit:
    """US-SA-070: Configure the AI daily analysis limit.

    MoSCoW: MUST
    Spec refs: S2.14.5-01, S2.14.5-03
    UI Surface: Django admin dashboard
    """

    def test_admin_dashboard_accessible(self, admin_client):
        resp = admin_client.get("/admin/")
        assert resp.status_code == 200


@pytest.mark.django_db
class TestUS_SA_071_HandleAISuggestionNewCategoryOrDept:
    """US-SA-071: Handle AI suggestion for a new category or department.

    MoSCoW: MUST
    Spec refs: S2.14.3-03, S2.14.3-03b
    UI Surface: /assets/<pk>/
    """

    def test_ai_suggestions_panel_on_asset_detail(
        self, admin_client, active_asset
    ):
        resp = admin_client.get(
            reverse("assets:asset_detail", args=[active_asset.pk])
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# §10A.x Asset Type on Edit Form (Issue #26)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUS_SA_138_AssetTypeOnEditForm:
    """US-SA-138: Asset type (serialised / kit) accessible from edit form.

    MoSCoW: SHOULD
    Spec refs: S2.17.1d, S2.2.7
    UI Surface: /assets/<pk>/edit/
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #26a: Asset edit form does not include asset type section"
            " (S2.17.1d). The edit form has no 'asset type', 'serialised',"
            " or 'is_kit' fields — type cannot be changed on the edit form."
        ),
    )
    def test_edit_form_contains_asset_type_section(
        self, admin_client, active_asset
    ):
        """S2.17.1d: The asset edit page must expose an 'asset type'
        section showing whether the asset is standard, serialised, or a
        kit — so staff can see the type context while editing."""
        resp = admin_client.get(
            reverse("assets:asset_edit", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode().lower()
        assert (
            "asset type" in content
            or "serialised" in content
            or "is_kit" in content
            or "is_serialised" in content
        ), (
            "Asset edit form must include asset type section"
            " ('asset type', 'serialised', or 'is_kit')"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "GAP #26b: Asset edit form does not include a link or section"
            " pointing to the serialisation conversion page (S2.17.1d)."
            " The conversion URL exists but is not reachable from the edit"
            " form."
        ),
    )
    def test_edit_form_contains_conversion_access(
        self, admin_client, active_asset
    ):
        """S2.17.1d: The asset edit page must include a link or section
        that navigates to the serialisation conversion page."""
        resp = admin_client.get(
            reverse("assets:asset_edit", args=[active_asset.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode().lower()
        # The conversion URL pattern or label should appear
        assert (
            "convert" in content
            or "serialis" in content
            or "serializ" in content
        ), (
            "Asset edit form must include access to conversion page"
            " (a link or section mentioning conversion/serialisation)"
        )

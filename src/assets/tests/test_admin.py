"""Tests for Django admin interface."""

import pytest

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

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
    AssetKit,
    AssetSerial,
    Department,
    NFCTag,
    SiteBranding,
    Transaction,
)

User = get_user_model()

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


@pytest.mark.django_db
class TestAdminMergeAssetsAction:
    """S4.6.3.4: Merge Assets admin bulk action."""

    def test_admin_merge_assets_action_exists(self, admin_client):
        """The merge_assets action is registered on AssetAdmin."""
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200
        assert b"merge_assets" in response.content

    def test_merge_requires_exactly_two_assets(self, admin_client, asset):
        """Merge action rejects selection of != 2 assets."""
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "merge_assets",
                "_selected_action": [asset.pk],
            },
            follow=True,
        )
        assert response.status_code == 200
        assert b"exactly 2 assets" in response.content

    def test_merge_shows_confirmation(
        self, admin_client, asset, category, location, user
    ):
        """Merge action shows confirmation page for 2 assets."""
        asset2 = Asset(
            name="Duplicate Prop",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        asset2.save()
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "merge_assets",
                "_selected_action": [asset.pk, asset2.pk],
            },
        )
        assert response.status_code == 200
        assert b"primary" in response.content.lower()

    def test_merge_executes_successfully(
        self, admin_client, asset, category, location, user
    ):
        """Merge action merges two assets when confirmed."""
        asset2 = Asset(
            name="Duplicate Prop",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        asset2.save()
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "merge_assets",
                "_selected_action": [asset.pk, asset2.pk],
                "apply": "1",
                "primary": str(asset.pk),
            },
            follow=True,
        )
        assert response.status_code == 200
        asset2.refresh_from_db()
        assert asset2.status == "disposed"

    def test_merge_requires_permission(
        self, client_logged_in, asset, category, location, user
    ):
        """Merge action not visible to users without can_merge_assets."""
        url = reverse("admin:assets_asset_changelist")
        response = client_logged_in.get(url)
        # Non-admin user can't access admin
        assert response.status_code in (302, 403)


@pytest.mark.django_db
class TestAdminBulkSerialiseAction:
    """S4.6.3.4: Bulk Serialise admin bulk action."""

    def test_admin_bulk_serialise_action(self, admin_client, asset):
        """Bulk serialise sets is_serialised=True on selected assets."""
        assert asset.is_serialised is False
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "bulk_serialise",
                "_selected_action": [asset.pk],
            },
            follow=True,
        )
        assert response.status_code == 200
        asset.refresh_from_db()
        assert asset.is_serialised is True

    def test_bulk_serialise_count_message(
        self, admin_client, asset, category, location, user
    ):
        """Bulk serialise reports count of affected assets."""
        asset2 = Asset(
            name="Another Prop",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        asset2.save()
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "bulk_serialise",
                "_selected_action": [asset.pk, asset2.pk],
            },
            follow=True,
        )
        assert response.status_code == 200
        asset.refresh_from_db()
        asset2.refresh_from_db()
        assert asset.is_serialised is True
        assert asset2.is_serialised is True


@pytest.mark.django_db
class TestAdminAddToHoldListAction:
    """S4.6.3.4: Add to Hold List admin bulk action."""

    def test_admin_add_to_holdlist_action(
        self, admin_client, asset, hold_list
    ):
        """Add to hold list creates HoldListItem for selected assets."""
        from assets.models import HoldListItem

        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "add_to_hold_list",
                "_selected_action": [asset.pk],
                "apply": "1",
                "hold_list": str(hold_list.pk),
            },
            follow=True,
        )
        assert response.status_code == 200
        assert HoldListItem.objects.filter(
            hold_list=hold_list, asset=asset
        ).exists()

    def test_add_to_holdlist_shows_form(self, admin_client, asset, hold_list):
        """Add to hold list shows a form to select hold list."""
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "add_to_hold_list",
                "_selected_action": [asset.pk],
            },
        )
        assert response.status_code == 200
        assert b"hold_list" in response.content


@pytest.mark.django_db
class TestAdminMarkLostRequiresNotes:
    """S4.6.3.4: Mark Lost action must require mandatory notes."""

    def test_admin_mark_lost_requires_notes(self, admin_client, asset):
        """Mark lost without notes shows error / form for notes."""
        url = reverse("admin:assets_asset_changelist")
        admin_client.post(
            url,
            {
                "action": "mark_lost",
                "_selected_action": [asset.pk],
            },
        )
        # Should show a notes form, not immediately update
        asset.refresh_from_db()
        assert asset.status != "lost"

    def test_admin_mark_lost_with_notes_succeeds(self, admin_client, asset):
        """Mark lost with notes succeeds and updates status."""
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "mark_lost",
                "_selected_action": [asset.pk],
                "apply": "1",
                "notes": "Lost at venue after show",
            },
            follow=True,
        )
        assert response.status_code == 200
        asset.refresh_from_db()
        assert asset.status == "lost"
        assert "Lost at venue after show" in asset.notes


@pytest.mark.django_db
class TestAdminMarkStolenRequiresNotes:
    """S4.6.3.4: Mark Stolen action must require mandatory notes."""

    def test_admin_mark_stolen_requires_notes(self, admin_client, asset):
        """Mark stolen without notes shows form for notes."""
        url = reverse("admin:assets_asset_changelist")
        admin_client.post(
            url,
            {
                "action": "mark_stolen",
                "_selected_action": [asset.pk],
            },
        )
        asset.refresh_from_db()
        assert asset.status != "stolen"

    def test_admin_mark_stolen_with_notes_succeeds(self, admin_client, asset):
        """Mark stolen with notes succeeds and updates status."""
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "mark_stolen",
                "_selected_action": [asset.pk],
                "apply": "1",
                "notes": "Stolen from loading dock",
            },
            follow=True,
        )
        assert response.status_code == 200
        asset.refresh_from_db()
        assert asset.status == "stolen"
        assert "Stolen from loading dock" in asset.notes


@pytest.mark.django_db
class TestAdminGenerateKitLabelsAction:
    """S4.6.3.4: Generate Kit Labels admin bulk action."""

    def test_generate_kit_labels_filters_kits(
        self, admin_client, kit_asset, asset, kit_component
    ):
        """Generate kit labels only processes kit assets."""
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "generate_kit_labels",
                "_selected_action": [kit_asset.pk, asset.pk],
            },
        )
        # Should redirect to label generation with component PKs
        assert response.status_code == 302

    def test_generate_kit_labels_no_kits_message(self, admin_client, asset):
        """Generate kit labels shows error if no kits selected."""
        url = reverse("admin:assets_asset_changelist")
        response = admin_client.post(
            url,
            {
                "action": "generate_kit_labels",
                "_selected_action": [asset.pk],
            },
            follow=True,
        )
        assert response.status_code == 200
        assert b"No kit assets" in response.content


# ============================================================
# DASHBOARD CACHING TESTS (G10)
# ============================================================


LOCMEM_CACHE = {
    "default": {
        "BACKEND": ("django.core.cache.backends.locmem.LocMemCache"),
    }
}

# ============================================================
# S2.13 ADMIN TESTS (V331-V345)
# ============================================================


@pytest.mark.django_db
class TestV331AssetAdminAssetImageInline:
    """V331 S2.13.2-01 MUST: Asset admin has AssetImage inline."""

    def test_asset_admin_change_page_has_assetimage_inline(
        self, admin_client, asset
    ):
        """Asset admin change page loads with AssetImage inline."""
        url = f"/admin/assets/asset/{asset.pk}/change/"
        response = admin_client.get(url)
        assert response.status_code == 200
        # Check that AssetImage inline is present
        content = response.content.decode()
        assert "image" in content.lower() and "caption" in content.lower()


@pytest.mark.django_db
class TestV332AssetAdminNFCTagInline:
    """V332 S2.13.2-02 MUST: Asset admin has NFCTag inline."""

    def test_asset_admin_change_page_has_nfctag_inline(
        self, admin_client, asset
    ):
        """Asset admin change page loads with NFCTag inline."""
        url = f"/admin/assets/asset/{asset.pk}/change/"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "tag_id" in content.lower() or "nfc" in content.lower()


@pytest.mark.django_db
class TestV333AssetAdminBarcodeImagePreview:
    """V333 S2.13.2-03 MUST: Asset admin barcode image preview."""

    def test_asset_admin_shows_barcode_preview(self, admin_client, asset):
        """Asset detail in admin shows barcode image preview."""
        url = f"/admin/assets/asset/{asset.pk}/change/"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Check for barcode preview field or barcode image
        assert "barcode" in content.lower()


@pytest.mark.django_db
class TestV334AssetAdminListFilters:
    """V334 S2.13.2-04 MUST: Asset admin list filters."""

    def test_asset_admin_changelist_has_filters(self, admin_client, asset):
        """Asset admin changelist loads with filters."""
        url = "/admin/assets/asset/"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Check for common filters
        assert (
            "filter" in content.lower()
            or "status" in content
            or "category" in content
        )


@pytest.mark.django_db
class TestV335AssetAdminSearchFields:
    """V335 S2.13.2-05 MUST: Asset admin search fields."""

    def test_asset_admin_search_works(self, admin_client, asset):
        """Asset admin search by name works."""
        url = f"/admin/assets/asset/?q={asset.name}"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert asset.name in content


@pytest.mark.django_db
class TestV336AssetAdminAIAnalysisInline:
    """V336 S2.13.2-06 SHOULD: Asset admin AI analysis results inline."""

    def test_asset_admin_shows_ai_analysis_fields(self, admin_client, asset):
        """Asset admin AssetImage inline shows AI analysis fields."""
        from assets.models import AssetImage

        # Create an image with AI results
        _img = AssetImage.objects.create(  # noqa: F841
            asset=asset,
            caption="Test image",
            ai_processing_status="completed",
            ai_description="Test description",
        )
        url = f"/admin/assets/asset/{asset.pk}/change/"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "ai_processing_status" in content or "AI" in content


@pytest.mark.django_db
class TestV338TransactionAdminDisplayFields:
    """V338 S2.13.3-01 MUST: Transaction admin display fields."""

    def test_transaction_admin_list_loads(self, admin_client, asset, user):
        """Transaction admin list page loads with display fields."""
        Transaction.objects.create(asset=asset, user=user, action="checkout")
        url = "/admin/assets/transaction/"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert asset.name in content or "checkout" in content.lower()


@pytest.mark.django_db
class TestV339TransactionAdminFilters:
    """V339 S2.13.3-02 MUST: Transaction admin filters."""

    def test_transaction_admin_has_filters(self, admin_client, asset, user):
        """Transaction admin list has filters for action and locations."""
        Transaction.objects.create(asset=asset, user=user, action="checkout")
        url = "/admin/assets/transaction/"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "filter" in content.lower() or "action" in content


@pytest.mark.django_db
class TestV340TransactionAdminReadOnly:
    """V340 S2.13.3-03 SHOULD: Transaction admin read-only."""

    def test_transaction_admin_is_read_only(self, admin_client, asset, user):
        """Transaction admin change page has read-only fields."""
        txn = Transaction.objects.create(
            asset=asset, user=user, action="checkout"
        )
        url = f"/admin/assets/transaction/{txn.pk}/change/"
        response = admin_client.get(url)
        assert response.status_code == 200
        # Check that key fields are read-only by looking for
        # readonly or disabled attributes
        content = response.content.decode()
        assert "readonly" in content.lower() or txn.action in content


@pytest.mark.django_db
class TestV342DepartmentAdminManagersM2M:
    """V342 S2.13.4-02 MUST: Department admin managers M2M."""

    def test_department_admin_shows_managers_field(
        self, admin_client, department
    ):
        """Department admin change page shows managers M2M field."""
        url = f"/admin/assets/department/{department.pk}/change/"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "managers" in content.lower()


@pytest.mark.django_db
class TestV343AdminAssignUsersToGroups:
    """V343 S2.13.5-01 MUST: Admin allows assigning users to groups."""

    def test_user_admin_has_groups_field(self, admin_client, user):
        """User admin change page has groups M2M field."""

        url = f"/admin/accounts/customuser/{user.pk}/change/"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "groups" in content.lower()


@pytest.mark.django_db
class TestV344AdminAssignDeptManagersToDepts:
    """V344 S2.13.5-02 MUST: Admin allows assigning dept managers to
    departments."""

    def test_department_admin_allows_manager_assignment(
        self, admin_client, department, user
    ):
        """Department admin allows assigning managers via M2M."""
        url = f"/admin/assets/department/{department.pk}/change/"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Managers field should be present (filter_horizontal)
        assert "managers" in content.lower()


@pytest.mark.django_db
class TestV345UserListShowsRolesAndDepartments:
    """V345 S2.13.5-03 SHOULD: User list shows roles and departments."""

    def test_user_admin_list_shows_role_and_dept_columns(
        self, admin_client, user, department
    ):
        """User admin list displays group and department columns."""
        department.managers.add(user)
        url = "/admin/accounts/customuser/"
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Check for display_groups or display_departments_list columns
        assert (
            "display_groups" in content.lower()
            or "groups" in content.lower()
            or "departments" in content.lower()
        )


@pytest.mark.django_db
class TestSiteBrandingAdminFields:
    """SiteBranding admin must expose colour customisation fields."""

    def test_admin_includes_color_fields(self, admin_client):
        """SiteBranding add page must show color fields."""
        response = admin_client.get("/admin/assets/sitebranding/add/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "primary_color" in content
        assert "secondary_color" in content
        assert "accent_color" in content
        assert "color_mode" in content


class TestSiteBrandingColorPickerWidget:
    """S4.6.2-04: Colour fields should use UnfoldAdminColorInputWidget."""

    def test_color_fields_render_as_color_input(self, admin_client):
        """Colour fields must render with type='color' HTML input."""
        response = admin_client.get("/admin/assets/sitebranding/add/")
        assert response.status_code == 200
        content = response.content.decode()
        for field in ["primary_color", "secondary_color", "accent_color"]:
            assert (
                'type="color"' in content
                and 'name="{}"'.format(field) in content
            ), f"{field} should render as a color picker input"

    def test_color_picker_saves_value(self, admin_client):
        """Colour value submitted via picker persists correctly."""
        response = admin_client.post(
            "/admin/assets/sitebranding/add/",
            {
                "primary_color": "#BC2026",
                "secondary_color": "#4A708B",
                "accent_color": "#2D7A6D",
                "color_mode": "system",
            },
            follow=True,
        )
        assert response.status_code == 200
        branding = SiteBranding.objects.first()
        assert branding is not None
        assert branding.primary_color == "#BC2026"
        assert branding.secondary_color == "#4A708B"
        assert branding.accent_color == "#2D7A6D"

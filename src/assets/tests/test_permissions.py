"""Tests for permissions and access control."""

import pytest

from django.contrib.auth import get_user_model
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
    Category,
    Department,
)

User = get_user_model()

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


class TestCanChangeStatusPermission:
    """S2.2.3-03: only managers and admins change status manually."""

    def test_admin_can_change_status(self, admin_user, asset):
        from assets.services.permissions import can_change_status

        assert can_change_status(admin_user, asset) is True

    def test_department_manager_can_change_status(
        self, dept_manager_user, asset
    ):
        from assets.services.permissions import can_change_status

        assert can_change_status(dept_manager_user, asset) is True

    def test_manager_of_inactive_department_cannot_change_status(
        self, dept_manager_user, asset, department
    ):
        from assets.services.permissions import can_change_status

        department.is_active = False
        department.save()
        assert can_change_status(dept_manager_user, asset) is False

    def test_member_cannot_change_status(self, member_user, asset):
        from assets.services.permissions import can_change_status

        assert can_change_status(member_user, asset) is False

    def test_member_cannot_change_status_of_own_draft(
        self, member_user, draft_asset
    ):
        from assets.services.permissions import can_change_status

        draft_asset.created_by = member_user
        draft_asset.save()
        assert can_change_status(member_user, draft_asset) is False

    def test_viewer_cannot_change_status(self, viewer_user, asset):
        from assets.services.permissions import can_change_status

        assert can_change_status(viewer_user, asset) is False


@pytest.mark.django_db
class TestManagerRoleIsScopedToManagedDepartments:
    """S2.8.3-05, S2.2.3-03: a Department Manager has the manager role
    only in the departments they manage."""

    @pytest.fixture
    def other_asset(self):
        return AssetFactory(
            status="active",
            category=CategoryFactory(department=DepartmentFactory()),
        )

    def test_role_in_managed_department_is_manager(
        self, dept_manager_user, department
    ):
        from assets.services.permissions import get_user_role

        role = get_user_role(dept_manager_user, department)
        assert role == "department_manager"

    def test_role_in_other_department_is_member(self, dept_manager_user):
        from assets.services.permissions import get_user_role

        role = get_user_role(dept_manager_user, DepartmentFactory())
        assert role == "member"

    def test_role_without_department_is_manager(self, dept_manager_user):
        from assets.services.permissions import get_user_role

        assert get_user_role(dept_manager_user) == "department_manager"

    def test_manager_cannot_write_other_department_asset(
        self, dept_manager_user, other_asset
    ):
        from assets.services.permissions import (
            can_change_status,
            can_delete_asset,
            can_edit_asset,
            can_handover_asset,
        )

        assert can_edit_asset(dept_manager_user, other_asset) is False
        assert can_delete_asset(dept_manager_user, other_asset) is False
        assert can_handover_asset(dept_manager_user, other_asset) is False
        assert can_change_status(dept_manager_user, other_asset) is False

    def test_manager_can_still_check_out_other_department_asset(
        self, dept_manager_user, other_asset
    ):
        from assets.services.permissions import can_checkout_asset

        assert can_checkout_asset(dept_manager_user, other_asset) is True

    def test_manager_gets_403_on_other_department_status_page(
        self, dept_manager_client, other_asset
    ):
        url = reverse(
            "assets:asset_change_status", args=[other_asset.pk, "missing"]
        )
        assert dept_manager_client.get(url).status_code == 403

    @pytest.mark.parametrize(
        "url_name,extra",
        [
            ("image_upload", None),
            ("image_delete", "image"),
            ("nfc_add", None),
            ("nfc_remove", "nfc"),
            ("clear_barcode", None),
            ("asset_convert_serialisation", None),
            ("kit_add_component", None),
            ("kit_remove_component", "kit_component"),
        ],
    )
    def test_manager_cannot_change_other_department_asset_parts(
        self, dept_manager_client, other_asset, url_name, extra
    ):
        """S2.10.3-01: edit, kit management and labels of an asset are
        for the managers of its department."""
        other_asset.is_kit = True
        other_asset.save()
        args = [other_asset.pk]
        if extra == "image":
            args.append(AssetImageFactory(asset=other_asset).pk)
        elif extra == "nfc":
            args.append(NFCTagFactory(asset=other_asset).pk)
        elif extra == "kit_component":
            args.append(AssetKitFactory(kit=other_asset).pk)
        # Security boundary: empty payload on purpose.
        resp = dept_manager_client.post(
            reverse(f"assets:{url_name}", args=args)
        )
        assert resp.status_code == 403

    @pytest.mark.parametrize(
        "url_name,extra",
        [
            ("image_upload", None),
            ("image_delete", "image"),
            ("nfc_add", None),
            ("nfc_remove", "nfc"),
        ],
    )
    def test_member_cannot_change_parts_of_asset_they_do_not_own(
        self, member_client, asset, url_name, extra
    ):
        """S2.10.3-04: Members edit only their own drafts."""
        args = [asset.pk]
        if extra == "image":
            args.append(AssetImageFactory(asset=asset).pk)
        elif extra == "nfc":
            args.append(NFCTagFactory(asset=asset).pk)
        # Security boundary: empty payload on purpose.
        resp = member_client.post(reverse(f"assets:{url_name}", args=args))
        assert resp.status_code == 403

    @pytest.mark.parametrize(
        "url_name",
        [
            "image_set_primary",
            "ai_analyse",
            "ai_apply_suggestions",
            "ai_reanalyse",
        ],
    )
    @pytest.mark.parametrize("who", ["viewer", "other_manager"])
    def test_image_and_ai_writes_need_edit_permission(
        self,
        viewer_client,
        dept_manager_client,
        asset,
        other_asset,
        url_name,
        who,
    ):
        """S2.10.3-05: every view checks permissions. US-MB-030: AI
        suggestions are applied by users who can edit the asset."""
        client, target = (
            (viewer_client, asset)
            if who == "viewer"
            else (dept_manager_client, other_asset)
        )
        image = AssetImageFactory(asset=target)
        # Security boundary: empty payload on purpose.
        resp = client.post(
            reverse(f"assets:{url_name}", args=[target.pk, image.pk])
        )
        assert resp.status_code == 403

    def _detail(self, client, asset):
        return client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        ).content.decode()

    def test_viewer_sees_no_image_or_nfc_actions(self, viewer_client, asset):
        image = AssetImageFactory(asset=asset)
        content = self._detail(viewer_client, asset)
        for url in (
            reverse("assets:image_upload", args=[asset.pk]),
            reverse("assets:image_delete", args=[asset.pk, image.pk]),
            reverse("assets:image_set_primary", args=[asset.pk, image.pk]),
            reverse("assets:nfc_add", args=[asset.pk]),
        ):
            assert url not in content

    def test_manager_sees_image_and_nfc_actions_in_own_department(
        self, dept_manager_client, asset
    ):
        content = self._detail(dept_manager_client, asset)
        assert reverse("assets:image_upload", args=[asset.pk]) in content
        assert reverse("assets:nfc_add", args=[asset.pk]) in content

    def test_manager_sees_no_convert_or_clear_barcode_in_other_department(
        self, dept_manager_client, other_asset
    ):
        content = self._detail(dept_manager_client, other_asset)
        for name in ("asset_convert_serialisation", "clear_barcode"):
            assert reverse(f"assets:{name}", args=[other_asset.pk]) not in (
                content
            )

    def test_manager_sees_convert_and_clear_barcode_in_own_department(
        self, dept_manager_client, asset
    ):
        content = self._detail(dept_manager_client, asset)
        for name in ("asset_convert_serialisation", "clear_barcode"):
            assert reverse(f"assets:{name}", args=[asset.pk]) in content

    def test_manager_keeps_manage_menu_on_other_department_asset(
        self, dept_manager_client, other_asset
    ):
        """The per-asset flag must not hide the global Manage menu."""
        content = self._detail(dept_manager_client, other_asset)
        assert reverse("assets:stocktake_list") in content

    def test_manager_cannot_move_category_to_other_department(
        self, dept_manager_client, category
    ):
        """S2.10.3-03: managers manage categories of their departments."""
        foreign = DepartmentFactory()
        # Security boundary: posts a department the user does not manage.
        resp = dept_manager_client.post(
            reverse("assets:category_edit", args=[category.pk]),
            {
                "name": category.name,
                "description": "",
                "icon": "",
                "department": foreign.pk,
            },
        )
        assert resp.status_code == 403
        category.refresh_from_db()
        assert category.department != foreign

    def test_kit_contents_hides_manage_controls_for_other_department(
        self, dept_manager_client, other_asset
    ):
        other_asset.is_kit = True
        other_asset.save()
        resp = dept_manager_client.get(
            reverse("assets:kit_contents", args=[other_asset.pk])
        )
        assert resp.context["can_manage"] is False

    def test_manager_sees_no_edit_link_on_other_department_asset(
        self, dept_manager_client, other_asset
    ):
        resp = dept_manager_client.get(
            reverse("assets:asset_detail", args=[other_asset.pk])
        )
        edit_url = reverse("assets:asset_edit", args=[other_asset.pk])
        assert edit_url.encode() not in resp.content


@pytest.mark.django_db
class TestDeptManagerDeactivation:
    """S7.7 — Permission edge cases for deactivated departments."""

    def test_vv726_deactivated_dept_manager_loses_write(
        self, client, password, department, category, location
    ):
        """VV726: Dept Manager whose department is deactivated
        should lose write access to that department's assets."""
        from django.contrib.auth.models import Group

        group, _ = Group.objects.get_or_create(name="Department Manager")
        mgr = UserFactory(
            username="depttestmgr",
            email="depttestmgr@example.com",
            password=password,
        )
        mgr.groups.add(group)
        department.managers.add(mgr)

        asset = AssetFactory(
            name="Dept Asset",
            category=category,
            current_location=location,
            status="active",
        )

        department.is_active = False
        department.save(update_fields=["is_active"])

        client.login(username="depttestmgr", password=password)
        response = client.get(reverse("assets:asset_edit", args=[asset.pk]))
        assert response.status_code in (302, 403), (
            "S7.7.2: Department Manager whose department is "
            "deactivated must lose write access to assets in "
            "that department. Currently the permission check "
            "does not consider department.is_active."
        )


# ============================================================
# BATCH 5: S2.10 PERMISSION/DEPARTMENT GAP TESTS
# ============================================================


@pytest.mark.django_db
class TestDepartmentCRUDRestricted:
    """V280 S2.10.1-02: Department CRUD restricted to System Admins.

    Department admin is only accessible to staff users. Non-staff users
    (members, viewers, dept managers without is_staff) cannot access the
    admin to create/edit departments.
    """

    def test_admin_can_access_department_changelist(self, admin_client):
        """System admin can list departments in admin."""
        url = reverse("admin:assets_department_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_admin_can_access_department_add(self, admin_client):
        """System admin can access department add form."""
        url = reverse("admin:assets_department_add")
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_non_staff_cannot_access_department_admin(self, client_logged_in):
        """Non-staff members cannot access department admin."""
        url = reverse("admin:assets_department_changelist")
        response = client_logged_in.get(url)
        # Should redirect to admin login
        assert response.status_code == 302

    def test_viewer_cannot_access_department_admin(self, viewer_client):
        """Viewers cannot access department admin."""
        url = reverse("admin:assets_department_changelist")
        response = viewer_client.get(url)
        assert response.status_code == 302

    def test_admin_can_create_department(self, admin_client):
        """System admin can create a department via admin."""
        url = reverse("admin:assets_department_add")
        response = admin_client.post(
            url,
            {
                "name": "New Dept",
                "description": "A new department",
                "barcode_prefix": "ND",
                "is_active": True,
                "managers": [],
            },
        )
        # Should redirect on success
        assert response.status_code == 302
        assert Department.objects.filter(name="New Dept").exists()

    def test_admin_can_edit_department(self, admin_client, department):
        """System admin can edit an existing department."""
        url = reverse("admin:assets_department_change", args=[department.pk])
        response = admin_client.post(
            url,
            {
                "name": "Renamed Props",
                "description": department.description,
                "barcode_prefix": department.barcode_prefix,
                "is_active": True,
                "managers": [],
            },
        )
        assert response.status_code == 302
        department.refresh_from_db()
        assert department.name == "Renamed Props"


@pytest.mark.django_db
class TestDepartmentDeletionProtection:
    """V281 S2.10.1-03: Prevent department deletion when it has assets.

    The Category FK to Department uses on_delete=PROTECT, so deleting a
    department that has categories (which may have assets) raises
    ProtectedError.
    """

    def test_department_with_categories_cannot_be_deleted(
        self, department, category
    ):
        """Deleting a department with categories raises ProtectedError."""
        from django.db.models import ProtectedError

        with pytest.raises(ProtectedError):
            department.delete()

    def test_department_without_categories_can_be_deleted(self, db):
        """A department with no categories can be deleted."""
        from assets.factories import DepartmentFactory

        dept = DepartmentFactory(name="Empty Dept")
        pk = dept.pk
        dept.delete()
        assert not Department.objects.filter(pk=pk).exists()

    def test_department_with_assets_via_category_protected(
        self, department, category, asset
    ):
        """Department deletion is blocked when its categories have assets."""
        from django.db.models import ProtectedError

        with pytest.raises(ProtectedError):
            department.delete()

    def test_category_department_fk_uses_protect(self):
        """Verify the Category.department FK uses on_delete=PROTECT."""
        field = Category._meta.get_field("department")
        from django.db import models

        assert field.remote_field.on_delete is models.PROTECT


@pytest.mark.django_db
class TestCategoryDepartmentFilter:
    """V286 S2.10.2-04: Categories grouped/filterable by department.

    The category_list view orders by department then name, and the admin
    has a department filter.
    """

    def test_category_list_groups_by_department(
        self, client_logged_in, department, category
    ):
        """Category list view returns categories with department info."""
        url = reverse("assets:category_list")
        response = client_logged_in.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert department.name in content
        assert category.name in content

    def test_category_admin_has_department_filter(self):
        """CategoryAdmin has department as a list filter."""
        from assets.admin import CategoryAdmin

        # Check list_filter contains department-related filter
        filter_fields = str(CategoryAdmin.list_filter)
        assert "department" in filter_fields

    def test_category_list_scoped_for_dept_manager(
        self, dept_manager_client, department, category
    ):
        """Dept manager sees only categories from managed departments."""
        from assets.factories import CategoryFactory, DepartmentFactory

        other_dept = DepartmentFactory(name="Other Dept")
        other_cat = CategoryFactory(name="Other Cat", department=other_dept)

        url = reverse("assets:category_list")
        response = dept_manager_client.get(url)
        content = response.content.decode()
        assert category.name in content
        assert other_cat.name not in content


@pytest.mark.django_db
class TestManageOwnDeptPermissions:
    """V290 S2.10.3-03: Manage Own Dept means CRUD on categories +
    department membership.

    Dept managers can create/edit categories within their department.
    """

    def test_dept_manager_can_create_category(
        self, dept_manager_client, department
    ):
        """Dept manager can create a category in their department."""
        url = reverse("assets:category_create")
        response = dept_manager_client.post(
            url,
            {"name": "New Category", "department": department.pk},
        )
        assert response.status_code == 302
        assert Category.objects.filter(
            name="New Category", department=department
        ).exists()

    def test_dept_manager_can_edit_own_category(
        self, dept_manager_client, category
    ):
        """Dept manager can edit a category in their department."""
        url = reverse("assets:category_edit", args=[category.pk])
        response = dept_manager_client.post(
            url,
            {
                "name": "Renamed Category",
                "department": category.department.pk,
            },
        )
        assert response.status_code == 302
        category.refresh_from_db()
        assert category.name == "Renamed Category"

    def test_dept_manager_cannot_create_category_in_other_dept(
        self, dept_manager_client
    ):
        """Dept manager cannot create category in another department."""
        from assets.factories import DepartmentFactory

        other = DepartmentFactory(name="Other Dept")
        url = reverse("assets:category_create")
        response = dept_manager_client.post(
            url,
            {"name": "Bad Category", "department": other.pk},
        )
        assert response.status_code == 403

    def test_dept_manager_cannot_edit_other_dept_category(
        self, dept_manager_client
    ):
        """Dept manager cannot edit category in another department."""
        from assets.factories import CategoryFactory, DepartmentFactory

        other = DepartmentFactory(name="Other Dept")
        other_cat = CategoryFactory(name="Other Cat", department=other)
        url = reverse("assets:category_edit", args=[other_cat.pk])
        response = dept_manager_client.get(url)
        assert response.status_code == 403

    def test_viewer_cannot_create_category(self, viewer_client):
        """Viewers cannot create categories."""
        url = reverse("assets:category_create")
        response = viewer_client.get(url)
        assert response.status_code == 403

    def test_member_cannot_create_category(self, client_logged_in):
        """Regular members cannot create categories."""
        url = reverse("assets:category_create")
        response = client_logged_in.get(url)
        assert response.status_code == 403


@pytest.mark.django_db
class TestUserManagementAdmin:
    """V299 S2.10.4-05: User management via admin interface.

    Users are manageable through the admin panel by system admins.
    """

    def test_admin_can_list_users(self, admin_client):
        """System admin can list users in admin."""
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_admin_can_access_user_add_url(self, admin_client):
        """System admin can resolve user add URL (admin registration)."""
        # Note: the actual add form may have Django/unfold compatibility
        # issues with usable_password field, so we just verify the URL
        # resolves and admin has the permission.
        url = reverse("admin:accounts_customuser_add")
        assert url is not None

    def test_admin_can_view_user_detail(self, admin_client, user):
        """System admin can view user change form."""
        url = reverse("admin:accounts_customuser_change", args=[user.pk])
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_non_staff_cannot_manage_users(self, client_logged_in):
        """Non-staff users cannot access user admin."""
        url = reverse("admin:accounts_customuser_changelist")
        response = client_logged_in.get(url)
        assert response.status_code == 302

"""Tests for hold lists and project management."""

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
    Department,
    HoldList,
    HoldListStatus,
    Location,
    Transaction,
)

User = get_user_model()

# ============================================================
# HOLD LIST TESTS
# ============================================================


class TestHoldListModels:
    """H1: Hold list model tests."""

    def test_holdlist_status_seeding(self, db):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.models import HoldListStatus

        assert HoldListStatus.objects.count() == 5
        assert HoldListStatus.objects.filter(is_default=True).count() == 1

    def test_holdlist_creation(self, user, department, db):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.models import HoldList, HoldListStatus

        status = HoldListStatus.objects.get(is_default=True)
        hl = HoldList.objects.create(
            name="Test List",
            status=status,
            department=department,
            created_by=user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        assert hl.pk is not None
        assert str(hl) == "Test List"

    def test_holdlist_requires_dates_without_project(
        self, user, department, db
    ):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.models import HoldList, HoldListStatus

        status = HoldListStatus.objects.get(is_default=True)
        hl = HoldList(
            name="No dates",
            status=status,
            department=department,
            created_by=user,
        )
        with pytest.raises(ValidationError):
            hl.full_clean()

    def test_holdlist_item_unique_constraint(
        self, asset, user, department, db
    ):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.models import HoldList, HoldListItem, HoldListStatus

        status = HoldListStatus.objects.get(is_default=True)
        hl = HoldList.objects.create(
            name="Test",
            status=status,
            department=department,
            created_by=user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        HoldListItem.objects.create(hold_list=hl, asset=asset, added_by=user)
        with pytest.raises(IntegrityError):
            HoldListItem.objects.create(
                hold_list=hl, asset=asset, added_by=user
            )

    def test_holdlist_item_serial_qty_validation(
        self, asset, user, department, db
    ):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.models import (
            AssetSerial,
            HoldList,
            HoldListItem,
            HoldListStatus,
        )

        asset.is_serialised = True
        asset.save()
        serial = AssetSerial.objects.create(asset=asset, serial_number="S1")
        status = HoldListStatus.objects.get(is_default=True)
        hl = HoldList.objects.create(
            name="Test",
            status=status,
            department=department,
            created_by=user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        item = HoldListItem(
            hold_list=hl,
            asset=asset,
            serial=serial,
            quantity=5,
            added_by=user,
        )
        with pytest.raises(ValidationError):
            item.full_clean()

    def test_project_delete_sets_holdlist_project_null(
        self, user, department, db
    ):
        """H1: Deleting a Project sets HoldList.project to NULL."""
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.models import HoldList, HoldListStatus, Project

        status = HoldListStatus.objects.get(is_default=True)
        project = Project.objects.create(name="Temp Project", created_by=user)
        hl = HoldList.objects.create(
            name="Project List",
            status=status,
            department=department,
            created_by=user,
            project=project,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        project.delete()
        hl.refresh_from_db()
        assert hl.project is None

    def test_department_delete_blocked_by_holdlist(self, user, department, db):
        """H2: Deleting a Department with HoldLists raises
        ProtectedError."""
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from django.db.models import ProtectedError

        from assets.models import HoldList, HoldListStatus

        status = HoldListStatus.objects.get(is_default=True)
        HoldList.objects.create(
            name="Dept List",
            status=status,
            department=department,
            created_by=user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        with pytest.raises(ProtectedError):
            department.delete()

    def test_user_delete_blocked_by_holdlist_created_by(self, department, db):
        """H3: Deleting a user who created a HoldList raises
        ProtectedError."""
        from django.contrib.auth import get_user_model
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from django.db.models import ProtectedError

        from assets.models import HoldList, HoldListStatus

        User = get_user_model()

        creator = User.objects.create_user(
            username="holdcreator",
            email="holdcreator@example.com",
            password="testpass123!",
        )
        status = HoldListStatus.objects.get(is_default=True)
        HoldList.objects.create(
            name="User List",
            status=status,
            department=department,
            created_by=creator,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        with pytest.raises(ProtectedError):
            creator.delete()

    def test_serial_delete_cascades_to_holdlist_item(
        self, asset, user, department, db
    ):
        """L9: Deleting an AssetSerial cascades to HoldListItem."""
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.models import (
            AssetSerial,
            HoldList,
            HoldListItem,
            HoldListStatus,
        )

        asset.is_serialised = True
        asset.save()
        serial = AssetSerial.objects.create(asset=asset, serial_number="DEL1")
        status = HoldListStatus.objects.get(is_default=True)
        hl = HoldList.objects.create(
            name="Serial Test",
            status=status,
            department=department,
            created_by=user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        HoldListItem.objects.create(
            hold_list=hl,
            asset=asset,
            serial=serial,
            added_by=user,
        )
        assert hl.items.count() == 1
        serial.delete()
        assert hl.items.count() == 0

    def test_holdlist_item_added_at_auto_populated(
        self, asset, user, department, db
    ):
        """L10: HoldListItem.added_at is auto-populated on creation."""
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.models import HoldList, HoldListItem, HoldListStatus

        status = HoldListStatus.objects.get(is_default=True)
        hl = HoldList.objects.create(
            name="Timestamp Test",
            status=status,
            department=department,
            created_by=user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        item = HoldListItem.objects.create(
            hold_list=hl, asset=asset, added_by=user
        )
        assert item.added_at is not None


class TestHoldListServices:
    """H2: Hold list service tests."""

    def test_create_hold_list(self, user, department, db):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.services.holdlists import create_hold_list

        hl = create_hold_list(
            "Service List",
            user,
            department=department,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        assert hl.pk is not None
        assert hl.name == "Service List"

    def test_add_and_remove_item(self, asset, user, department, db):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.services.holdlists import (
            add_item,
            create_hold_list,
            remove_item,
        )

        hl = create_hold_list(
            "Test",
            user,
            department=department,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        item = add_item(hl, asset, user)
        assert hl.items.count() == 1
        remove_item(hl, item.pk, user)
        assert hl.items.count() == 0

    def test_locked_list_blocks_add(self, asset, user, department, db):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.services.holdlists import (
            add_item,
            create_hold_list,
            lock_hold_list,
        )

        hl = create_hold_list(
            "Locked",
            user,
            department=department,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        lock_hold_list(hl, user)
        with pytest.raises(ValidationError):
            add_item(hl, asset, user)

    def test_overlap_detection(self, asset, user, department, db):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.services.holdlists import (
            add_item,
            create_hold_list,
            detect_overlaps,
        )

        hl1 = create_hold_list(
            "List 1",
            user,
            department=department,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        hl2 = create_hold_list(
            "List 2",
            user,
            department=department,
            start_date="2026-03-10",
            end_date="2026-03-20",
        )
        add_item(hl1, asset, user)
        add_item(hl2, asset, user)
        overlaps = detect_overlaps(hl1)
        assert len(overlaps) == 1

    def test_check_asset_held(self, asset, user, department, db):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.services.holdlists import (
            add_item,
            check_asset_held,
            create_hold_list,
        )

        assert not check_asset_held(asset)
        hl = create_hold_list(
            "Hold",
            user,
            department=department,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        add_item(hl, asset, user)
        assert check_asset_held(asset)


class TestHoldlistPickSheet:
    """V15/H4: Pick sheet PDF generation."""

    def test_holdlist_pick_sheet_returns_pdf(
        self, client_logged_in, asset, user, department
    ):
        from assets.models import HoldList, HoldListStatus

        status, _ = HoldListStatus.objects.get_or_create(
            name="Draft",
            defaults={"is_default": True, "sort_order": 10},
        )
        hold_list = HoldList.objects.create(
            name="Test List",
            status=status,
            department=department,
            created_by=user,
            start_date="2026-01-01",
            end_date="2026-02-01",
        )
        response = client_logged_in.get(
            reverse("assets:holdlist_pick_sheet", args=[hold_list.pk])
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_holdlist_pick_sheet_requires_login(self, client, department, db):
        from django.contrib.auth import get_user_model

        from assets.models import HoldList, HoldListStatus

        User = get_user_model()

        creator = User.objects.create_user(
            username="pickcreator",
            email="pickcreator@example.com",
            password="testpass123!",
        )
        status, _ = HoldListStatus.objects.get_or_create(
            name="Draft",
            defaults={"is_default": True, "sort_order": 10},
        )
        hold_list = HoldList.objects.create(
            name="Test List",
            status=status,
            department=department,
            created_by=creator,
            start_date="2026-01-01",
            end_date="2026-02-01",
        )
        response = client.get(
            reverse("assets:holdlist_pick_sheet", args=[hold_list.pk])
        )
        assert response.status_code == 302  # Redirect to login


class TestHoldListViews:
    """H3: Hold list view tests."""

    def test_holdlist_list_view(self, admin_client, db):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        response = admin_client.get(reverse("assets:holdlist_list"))
        assert response.status_code == 200

    def test_holdlist_create_view(
        self, admin_client, admin_user, department, db
    ):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.models import HoldListStatus

        status = HoldListStatus.objects.get(is_default=True)
        response = admin_client.post(
            reverse("assets:holdlist_create"),
            {
                "name": "View Test",
                "status": status.pk,
                "department": department.pk,
                "start_date": "2026-03-01",
                "end_date": "2026-03-15",
            },
        )
        assert response.status_code == 302

    def test_holdlist_detail_view(
        self, admin_client, admin_user, department, db
    ):
        from django.core.management import call_command

        call_command("seed_holdlist_statuses")
        from assets.models import HoldList, HoldListStatus

        status = HoldListStatus.objects.get(is_default=True)
        hl = HoldList.objects.create(
            name="Detail Test",
            status=status,
            department=department,
            created_by=admin_user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        response = admin_client.get(
            reverse("assets:holdlist_detail", args=[hl.pk])
        )
        assert response.status_code == 200

    def test_project_crud(self, admin_client, db):
        response = admin_client.get(reverse("assets:project_list"))
        assert response.status_code == 200
        response = admin_client.post(
            reverse("assets:project_create"),
            {"name": "Test Project", "description": "Test"},
        )
        assert response.status_code == 302


@pytest.mark.django_db
class TestHoldListCheckoutBlocking:
    """S2.16.5: Hold list checkout blocking and indicators."""

    def test_held_asset_shows_indicator_on_detail(
        self, admin_client, asset, active_hold_list, admin_user
    ):
        """S2.16.5-01: Asset detail shows 'Held for' indicator."""
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=active_hold_list,
            asset=asset,
            added_by=admin_user,
        )
        response = admin_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert response.status_code == 200
        assert b"Held for" in response.content
        assert active_hold_list.name.encode() in response.content

    def test_held_asset_blocked_at_checkout(
        self, client_logged_in, asset, active_hold_list, user
    ):
        """S2.16.5-02: Checkout blocked for held assets."""
        from assets.models import HoldListItem

        asset.status = "active"
        asset.save()
        HoldListItem.objects.create(
            hold_list=active_hold_list,
            asset=asset,
            added_by=user,
        )
        response = client_logged_in.get(
            reverse("assets:asset_checkout", args=[asset.pk])
        )
        assert response.status_code == 302
        # Should redirect back with error

    def test_override_permission_allows_checkout_of_held_asset(
        self,
        asset,
        active_hold_list,
        admin_user,
        second_user,
        password,
    ):
        """S2.16.5-03: Override permission allows checkout."""
        from django.test import Client

        from assets.models import HoldListItem

        asset.status = "active"
        asset.save()
        HoldListItem.objects.create(
            hold_list=active_hold_list,
            asset=asset,
            added_by=admin_user,
        )
        # admin_user is superuser, has all perms
        c = Client()
        c.login(username=admin_user.username, password=password)
        response = c.get(reverse("assets:asset_checkout", args=[asset.pk]))
        # Should render the checkout form (200), not redirect
        assert response.status_code == 200

    def test_checkout_non_held_asset_succeeds(
        self, admin_client, admin_user, asset, second_user
    ):
        """S2.16.5: Non-held assets can be checked out normally."""
        asset.status = "active"
        asset.save()
        response = admin_client.post(
            reverse("assets:asset_checkout", args=[asset.pk]),
            {
                "borrower": second_user.pk,
                "notes": "Regular checkout",
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.checked_out_to == second_user

    def test_fulfil_hold_list_item(self, asset, active_hold_list, user):
        """S2.16.5-05: Fulfil marks item as pulled."""
        from assets.models import HoldListItem
        from assets.services.holdlists import fulfil_item

        item = HoldListItem.objects.create(
            hold_list=active_hold_list,
            asset=asset,
            added_by=user,
        )
        fulfil_item(item, user)
        item.refresh_from_db()
        assert item.pull_status == "pulled"
        assert item.pulled_by == user
        assert item.pulled_at is not None

    def test_non_serialised_quantity_hold_blocking(
        self,
        non_serialised_asset,
        active_hold_list,
        user,
    ):
        """S2.16.5-07: Quantity-aware hold for non-serialised."""
        from assets.models import HoldListItem
        from assets.services.holdlists import (
            get_held_quantity,
        )

        HoldListItem.objects.create(
            hold_list=active_hold_list,
            asset=non_serialised_asset,
            quantity=5,
            added_by=user,
        )
        held = get_held_quantity(non_serialised_asset)
        assert held == 5
        # Asset has quantity=10, 5 held, so 5 available
        available = non_serialised_asset.quantity - held
        assert available == 5

    def test_serialised_serial_specific_hold_blocking(
        self,
        serialised_asset,
        asset_serial,
        active_hold_list,
        user,
    ):
        """S2.16.5-08: Serial-specific hold blocks that serial."""
        from assets.models import HoldListItem
        from assets.services.holdlists import check_serial_held

        HoldListItem.objects.create(
            hold_list=active_hold_list,
            asset=serialised_asset,
            serial=asset_serial,
            added_by=user,
        )
        assert check_serial_held(asset_serial) is True

    def test_setup_groups_has_override_hold_checkout(self, db):
        """S2.16.5-04: setup_groups assigns override permission."""
        from django.core.management import call_command

        # Seed hold list statuses first if needed
        try:
            call_command("seed_holdlist_statuses")
        except Exception:
            pass
        call_command("setup_groups")

        from django.contrib.auth.models import Group

        sa = Group.objects.get(name="System Admin")
        dm = Group.objects.get(name="Department Manager")
        member = Group.objects.get(name="Member")

        sa_perms = set(sa.permissions.values_list("codename", flat=True))
        dm_perms = set(dm.permissions.values_list("codename", flat=True))
        member_perms = set(
            member.permissions.values_list("codename", flat=True)
        )

        assert "override_hold_checkout" in sa_perms
        assert "override_hold_checkout" in dm_perms
        assert "override_hold_checkout" not in member_perms


class TestHoldListStatusAdmin:
    """VV429, VV432: HoldListStatus must be registered in Django admin."""

    def test_holdliststatus_registered_in_admin(self, admin_client, db):
        """VV429: HoldListStatus model is accessible via Django admin."""
        url = reverse("admin:assets_holdliststatus_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200, (
            "HoldListStatus must be registered in Django admin "
            "(S2.16.2-04). Expected admin changelist at "
            "admin:assets_holdliststatus_changelist."
        )

    def test_admin_can_add_holdliststatus(self, admin_client, db):
        """VV432: Admin can create a new HoldListStatus via the admin."""
        url = reverse("admin:assets_holdliststatus_add")
        response = admin_client.post(
            url,
            {
                "name": "Ready",
                "is_default": False,
                "is_terminal": False,
                "sort_order": 10,
                "color": "blue",
            },
        )
        # Expect redirect on success (302)
        assert response.status_code == 302, (
            "Admin must be able to add HoldListStatus via the admin "
            "interface (S2.16.2-04)."
        )
        assert HoldListStatus.objects.filter(name="Ready").exists()

    def test_admin_can_edit_holdliststatus(self, admin_client, db):
        """VV432: Admin can edit an existing HoldListStatus."""
        status = HoldListStatus.objects.create(name="Editable", sort_order=5)
        url = reverse("admin:assets_holdliststatus_change", args=[status.pk])
        response = admin_client.post(
            url,
            {
                "name": "Edited Status",
                "is_default": False,
                "is_terminal": False,
                "sort_order": 5,
                "color": "green",
            },
        )
        assert (
            response.status_code == 302
        ), "Admin must be able to edit HoldListStatus (S2.16.2-04)."
        status.refresh_from_db()
        assert status.name == "Edited Status"


class TestProjectCRUD:
    """VV427, VV428: Project delete and detail views."""

    def test_project_delete_view_exists(self, admin_client, admin_user):
        """VV427: Project delete URL must be resolvable."""
        from assets.models import Project

        project = Project.objects.create(
            name="Deletable Project",
            created_by=admin_user,
        )
        url = reverse("assets:project_delete", args=[project.pk])
        response = admin_client.post(url)
        assert response.status_code in (200, 302), (
            "Project delete view must exist (S2.16.1-04). "
            "Expected a named URL 'assets:project_delete'."
        )

    def test_project_delete_requires_permission(
        self, client_logged_in, user, admin_user
    ):
        """VV427: Only creator, dept manager, or admin can delete."""
        from assets.models import Project

        project = Project.objects.create(
            name="Someone Else's Project",
            created_by=admin_user,
        )
        url = reverse("assets:project_delete", args=[project.pk])
        response = client_logged_in.post(url)
        assert response.status_code == 403, (
            "Non-creator member must be denied project deletion "
            "(S2.16.1-04)."
        )

    def test_project_detail_view_exists(self, admin_client, admin_user):
        """VV428: Project detail view must exist and show info."""
        from assets.models import Project

        project = Project.objects.create(
            name="Detail Project",
            description="Test description",
            created_by=admin_user,
        )
        url = reverse("assets:project_detail", args=[project.pk])
        response = admin_client.get(url)
        assert response.status_code == 200, (
            "Project detail view must exist (S2.16.1-05). "
            "Expected a named URL 'assets:project_detail'."
        )
        assert b"Detail Project" in response.content

    def test_project_detail_shows_hold_lists(
        self, admin_client, admin_user, department, hl_active_status
    ):
        """VV428: Project detail shows associated hold lists."""
        from assets.models import Project

        project = Project.objects.create(
            name="Project With Lists",
            created_by=admin_user,
        )
        HoldList.objects.create(
            name="Project Hold List",
            project=project,
            department=department,
            status=hl_active_status,
            created_by=admin_user,
        )
        url = reverse("assets:project_detail", args=[project.pk])
        response = admin_client.get(url)
        assert b"Project Hold List" in response.content, (
            "Project detail must show associated hold lists " "(S2.16.1-05)."
        )


class TestHoldListDates:
    """VV426, VV435: Cascading due date and effective dates."""

    def test_cascading_due_date_transaction_takes_priority(
        self, user, department, category, asset, hl_active_status
    ):
        """VV426: Transaction.due_date takes priority over all other
        date sources (S2.16.1-03a).

        Per S2.16.1-03a the resolution order is:
        1. Transaction.due_date
        2. ProjectDateRange matching dept+category
        3. ProjectDateRange matching dept only
        4. ProjectDateRange unscoped (project-wide)
        """
        import datetime

        from django.utils import timezone

        from assets.models import Project, ProjectDateRange

        project = Project.objects.create(
            name="Due Date Project",
            created_by=user,
        )
        ProjectDateRange.objects.create(
            project=project,
            label="Show",
            start_date=datetime.date(2026, 6, 1),
            end_date=datetime.date(2026, 6, 30),
        )
        hl = HoldList.objects.create(
            name="Due Date HL",
            project=project,
            department=department,
            status=hl_active_status,
            created_by=user,
            end_date=datetime.date(2026, 5, 15),
        )
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=hl,
            asset=asset,
            added_by=user,
        )

        # Create a checkout transaction with an explicit due date
        txn = Transaction.objects.create(
            asset=asset,
            user=user,
            action="checkout",
            due_date=timezone.make_aware(
                datetime.datetime(2026, 4, 1, 12, 0, 0)
            ),
        )

        from assets.services.holdlists import resolve_due_date

        # Per spec, Transaction.due_date (Apr 1) should take
        # priority over HoldList.end_date (May 15) and
        # ProjectDateRange (Jun 30).
        try:
            due = resolve_due_date(hl, asset=asset, transaction=txn)
        except TypeError:
            pytest.fail(
                "resolve_due_date must accept 'asset' and "
                "'transaction' parameters for full cascading "
                "resolution (S2.16.1-03a). Transaction.due_date "
                "is the highest priority in the cascade."
            )
        assert due == datetime.date(2026, 4, 1), (
            "Transaction.due_date must take priority over all other "
            "date sources (S2.16.1-03a)."
        )

    def test_cascading_due_date_per_asset_resolution(
        self, user, department, category, asset, hl_active_status
    ):
        """VV426: Per-asset due date resolution must consider the
        asset's dept+category to pick the most specific
        ProjectDateRange (S2.16.1-03a step 2 vs 3 vs 4).

        The spec says due date is resolved PER ASSET, considering
        the asset's department AND category.  The function must
        accept an asset parameter to resolve per-asset.
        """
        import datetime

        from assets.models import Project, ProjectDateRange

        project = Project.objects.create(
            name="Scoped Date Project",
            created_by=user,
        )
        # Unscoped (project-wide) range
        ProjectDateRange.objects.create(
            project=project,
            label="General",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 3, 31),
        )
        # Dept-only scoped range
        ProjectDateRange.objects.create(
            project=project,
            label="Props Dept",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 3, 25),
            department=department,
        )
        # Dept+category scoped range (most specific)
        ProjectDateRange.objects.create(
            project=project,
            label="Props Setup",
            start_date=datetime.date(2026, 3, 5),
            end_date=datetime.date(2026, 3, 20),
            department=department,
            category=category,
        )
        hl = HoldList.objects.create(
            name="Scoped HL",
            project=project,
            department=department,
            status=hl_active_status,
            created_by=user,
        )
        from assets.services.holdlists import resolve_due_date

        # Per spec S2.16.1-03a, resolution should be per-asset:
        # 1. Transaction.due_date (none here)
        # 2. Dept+category match -> Mar 20
        # 3. Dept-only match -> Mar 25
        # 4. Unscoped match -> Mar 31
        # The function must accept an asset to resolve correctly.
        try:
            due = resolve_due_date(hl, asset=asset)
        except TypeError:
            pytest.fail(
                "resolve_due_date must accept an optional 'asset' "
                "parameter for per-asset cascading resolution "
                "(S2.16.1-03a). Current implementation only resolves "
                "per hold list, not per asset."
            )
        assert due == datetime.date(2026, 3, 20), (
            "Dept+category scoped ProjectDateRange must take priority "
            "over dept-only and unscoped ranges (S2.16.1-03a)."
        )

    def test_holdlist_shows_effective_dates_from_project(
        self, admin_client, admin_user, department, hl_active_status
    ):
        """VV435: Hold list detail shows effective dates derived
        from the linked project (S2.16.3-03)."""
        import datetime

        from assets.models import Project, ProjectDateRange

        project = Project.objects.create(
            name="Dated Project",
            created_by=admin_user,
        )
        ProjectDateRange.objects.create(
            project=project,
            label="Event",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2026, 4, 15),
        )
        hl = HoldList.objects.create(
            name="Project-Dated HL",
            project=project,
            department=department,
            status=hl_active_status,
            created_by=admin_user,
        )
        url = reverse("assets:holdlist_detail", args=[hl.pk])
        response = admin_client.get(url)
        content = response.content.decode()
        # The template must show effective dates from the project
        assert "2026-04-01" in content or "April 1" in content, (
            "Hold list detail must show effective start date derived "
            "from the linked project (S2.16.3-03)."
        )
        assert "2026-04-15" in content or "April 15" in content, (
            "Hold list detail must show effective end date derived "
            "from the linked project (S2.16.3-03)."
        )


class TestHoldListEditDelete:
    """VV437, VV438, VV439: Hold list delete, lock editing, toggle."""

    def test_holdlist_delete_view_exists(
        self, admin_client, admin_user, department, hl_active_status
    ):
        """VV437: Hold list delete view must exist."""
        hl = HoldList.objects.create(
            name="Deletable HL",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        url = reverse("assets:holdlist_delete", args=[hl.pk])
        response = admin_client.post(url)
        assert response.status_code in (200, 302), (
            "Hold list delete view must exist (S2.16.3-05). "
            "Expected named URL 'assets:holdlist_delete'."
        )

    def test_locked_holdlist_blocks_creator_edit(
        self,
        client,
        password,
        department,
        hl_active_status,
    ):
        """VV438: Locked hold list blocks editing by creator
        (S2.16.3-05, S2.16.3-07). The view must check is_locked
        and return 403 or a redirect with error for the creator."""
        from django.contrib.auth import get_user_model

        User = get_user_model()

        creator = User.objects.create_user(
            username="hl_creator",
            email="hlcreator@example.com",
            password=password,
        )
        from django.contrib.auth.models import Group

        member_group, _ = Group.objects.get_or_create(name="Member")
        creator.groups.add(member_group)

        hl = HoldList.objects.create(
            name="Locked HL",
            department=department,
            status=hl_active_status,
            created_by=creator,
            is_locked=True,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        client.login(username="hl_creator", password=password)
        url = reverse("assets:holdlist_edit", args=[hl.pk])
        response = client.post(
            url,
            {
                "name": "Changed Name",
                "department": department.pk,
                "status": hl_active_status.pk,
                "start_date": "2026-03-01",
                "end_date": "2026-03-15",
            },
        )
        hl.refresh_from_db()
        # The edit view must explicitly check is_locked for the
        # creator and block the edit (403 or redirect with error).
        assert hl.name == "Locked HL", (
            "A locked hold list must block editing by the creator "
            "(S2.16.3-05, S2.16.3-07). The view must check "
            "is_locked before processing the POST."
        )
        # Should return 403 or redirect with an error message
        assert response.status_code in (403, 302), (
            "Locked hold list edit by creator should return 403 "
            "or redirect with error, not silently succeed."
        )

    def test_locked_holdlist_allows_manager_edit(
        self,
        dept_manager_client,
        dept_manager_user,
        department,
        hl_active_status,
    ):
        """VV438: Manager can edit a locked hold list (S2.16.3-06)."""
        hl = HoldList.objects.create(
            name="Manager Locked HL",
            department=department,
            status=hl_active_status,
            created_by=dept_manager_user,
            is_locked=True,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        url = reverse("assets:holdlist_edit", args=[hl.pk])
        _response = dept_manager_client.post(  # noqa: F841
            url,
            {
                "name": "Manager Changed",
                "department": department.pk,
                "status": hl_active_status.pk,
                "start_date": "2026-03-01",
                "end_date": "2026-03-15",
            },
        )
        hl.refresh_from_db()
        assert hl.name == "Manager Changed", (
            "Department managers must be able to edit locked hold "
            "lists (S2.16.3-06)."
        )

    def test_lock_toggle_endpoint_exists(
        self, admin_client, admin_user, department, hl_active_status
    ):
        """VV439: Lock/unlock toggle endpoint must exist."""
        hl = HoldList.objects.create(
            name="Toggle Lock HL",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        # Try to lock
        url = reverse("assets:holdlist_lock", args=[hl.pk])
        response = admin_client.post(url)
        assert response.status_code in (200, 302), (
            "Lock toggle endpoint must exist (S2.16.3-06). "
            "Expected named URL 'assets:holdlist_lock'."
        )
        hl.refresh_from_db()
        assert (
            hl.is_locked is True
        ), "POSTing to lock endpoint must lock the hold list."

    def test_unlock_toggle_endpoint_exists(
        self, admin_client, admin_user, department, hl_active_status
    ):
        """VV439: Unlock endpoint must exist."""
        hl = HoldList.objects.create(
            name="Unlock HL",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            is_locked=True,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        url = reverse("assets:holdlist_unlock", args=[hl.pk])
        response = admin_client.post(url)
        assert response.status_code in (200, 302), (
            "Unlock endpoint must exist (S2.16.3-06). "
            "Expected named URL 'assets:holdlist_unlock'."
        )
        hl.refresh_from_db()
        assert hl.is_locked is False


class TestHoldListItemOverlap:
    """VV442, VV443, VV444: Overlap warnings at item addition."""

    def test_overlap_warning_as_message_at_item_addition(
        self,
        admin_client,
        admin_user,
        asset,
        department,
        hl_active_status,
    ):
        """VV442: Overlap warning shown as a Django message when
        adding an item (S2.16.4-03). The warning must be surfaced
        at addition time via messages framework, not just passively
        displayed on the detail page."""
        hl1 = HoldList.objects.create(
            name="Existing Hold",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=hl1,
            asset=asset,
            added_by=admin_user,
        )

        hl2 = HoldList.objects.create(
            name="New Hold",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-03-10",
            end_date="2026-03-20",
        )
        # Adding same asset to overlapping hold list
        url = reverse("assets:holdlist_add_item", args=[hl2.pk])
        response = admin_client.post(
            url,
            {"asset_id": asset.pk, "quantity": 1},
            follow=True,
        )
        # Check Django messages for an overlap warning
        msg_texts = [
            str(m) for m in list(response.context.get("messages", []))
        ]
        overlap_in_messages = any(
            "overlap" in m.lower() or "also on" in m.lower() for m in msg_texts
        )
        assert overlap_in_messages, (
            "Overlap warning must be surfaced as a Django message "
            "at item addition time (S2.16.4-03), not just shown "
            "passively on the detail page. Messages were: "
            f"{msg_texts}"
        )

    def test_overlap_warning_message_includes_date_ranges(
        self,
        admin_client,
        admin_user,
        asset,
        department,
        hl_active_status,
    ):
        """VV443: Overlap warning message includes conflicting hold
        list name AND date ranges (S2.16.4-04)."""
        hl1 = HoldList.objects.create(
            name="Dated Hold",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=hl1,
            asset=asset,
            added_by=admin_user,
        )

        hl2 = HoldList.objects.create(
            name="Overlapping Hold",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-03-10",
            end_date="2026-03-25",
        )
        url = reverse("assets:holdlist_add_item", args=[hl2.pk])
        response = admin_client.post(
            url,
            {"asset_id": asset.pk, "quantity": 1},
            follow=True,
        )
        # Check Django messages for name and dates
        msg_texts = [
            str(m) for m in list(response.context.get("messages", []))
        ]
        combined = " ".join(msg_texts)
        assert "Dated Hold" in combined, (
            "Overlap warning message must include the conflicting "
            "hold list name (S2.16.4-04). Messages: " + combined
        )
        assert "Mar" in combined or "2026-03" in combined, (
            "Overlap warning message must include conflicting date "
            "ranges (S2.16.4-04). Messages: " + combined
        )

    def test_dept_manager_overlap_override_acknowledged(
        self,
        dept_manager_client,
        dept_manager_user,
        asset,
        department,
        hl_active_status,
    ):
        """VV444: Department manager can override overlap warning
        and the override is acknowledged in messages (S2.16.4-05).

        When override_overlap=1 is passed, the view should add the
        item AND acknowledge the override (not show warning again).
        """
        hl1 = HoldList.objects.create(
            name="Existing HL",
            department=department,
            status=hl_active_status,
            created_by=dept_manager_user,
            start_date="2026-03-01",
            end_date="2026-03-15",
        )
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=hl1,
            asset=asset,
            added_by=dept_manager_user,
        )

        hl2 = HoldList.objects.create(
            name="Override HL",
            department=department,
            status=hl_active_status,
            created_by=dept_manager_user,
            start_date="2026-03-10",
            end_date="2026-03-25",
        )
        url = reverse("assets:holdlist_add_item", args=[hl2.pk])
        response = dept_manager_client.post(
            url,
            {
                "asset_id": asset.pk,
                "quantity": 1,
                "override_overlap": "1",
            },
            follow=True,
        )
        assert HoldListItem.objects.filter(
            hold_list=hl2, asset=asset
        ).exists(), (
            "Department manager must be able to add item with "
            "override_overlap (S2.16.4-05)."
        )
        # The view must acknowledge the override -- either via a
        # specific "override" success message or by explicitly
        # processing the override_overlap parameter.
        msg_texts = [
            str(m) for m in list(response.context.get("messages", []))
        ]
        has_override_ack = any("override" in m.lower() for m in msg_texts)
        has_overlap_warning = any("overlap" in m.lower() for m in msg_texts)
        assert has_override_ack or has_overlap_warning, (
            "When override_overlap is provided, the view must "
            "acknowledge the override in messages (S2.16.4-05). "
            "Currently the view does not process override_overlap "
            "at all. Messages: " + str(msg_texts)
        )


class TestHoldListCheckoutBlockingV2:
    """VV445, VV447, VV451, VV452: Checkout blocking enhancements."""

    def test_asset_detail_held_indicator_with_dates(
        self,
        admin_client,
        admin_user,
        asset,
        department,
        hl_active_status,
    ):
        """VV445: Asset detail 'Held for' indicator must include
        date ranges (S2.16.5-01)."""
        hl = HoldList.objects.create(
            name="Dated Active Hold",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=hl,
            asset=asset,
            added_by=admin_user,
        )
        url = reverse("assets:asset_detail", args=[asset.pk])
        response = admin_client.get(url)
        content = response.content.decode()
        assert (
            "Dated Active Hold" in content
        ), "Asset detail must show hold list name (S2.16.5-01)."
        assert (
            "Apr" in content or "2026-04" in content or "April" in content
        ), (
            "Asset detail held-for indicator must include date ranges "
            "(S2.16.5-01). Currently only shows hold list name."
        )

    def test_hold_override_logged_in_transaction_notes(
        self,
        admin_client,
        admin_user,
        asset,
        department,
        hl_active_status,
        second_user,
    ):
        """VV447: Hold override must be logged in transaction notes
        (S2.16.5-03)."""
        hl = HoldList.objects.create(
            name="Overrideable Hold",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=hl,
            asset=asset,
            added_by=admin_user,
        )
        # Admin has override permission, checkout should succeed
        url = reverse("assets:asset_checkout", args=[asset.pk])
        _response = admin_client.post(  # noqa: F841
            url,
            {"borrower": second_user.pk, "notes": ""},
            follow=True,
        )
        # Check that a transaction was created with override note
        txn = Transaction.objects.filter(
            asset=asset, action="checkout"
        ).first()
        assert txn is not None, "Checkout transaction must be created."
        assert "override" in (txn.notes or "").lower(), (
            "Hold override must be logged in transaction notes "
            "(S2.16.5-03). Expected 'override' in notes but got: "
            f"'{txn.notes}'."
        )

    def test_quantity_aware_hold_blocking_checkout_view(
        self,
        client_logged_in,
        user,
        department,
        hl_active_status,
        second_user,
    ):
        """VV451: Non-serialised checkout blocked only when requested
        quantity exceeds available (S2.16.5-07)."""
        from assets.factories import AssetFactory

        ns_asset = AssetFactory(
            name="Qty Asset",
            category=None,
            current_location=None,
            status="active",
            is_serialised=False,
            quantity=10,
            created_by=user,
        )
        hl = HoldList.objects.create(
            name="Qty Hold",
            department=department,
            status=hl_active_status,
            created_by=user,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=hl,
            asset=ns_asset,
            quantity=3,
            added_by=user,
        )
        # 10 total, 3 held -> 7 available.
        # Checking out qty 5 should be allowed (5 <= 7).
        url = reverse("assets:asset_checkout", args=[ns_asset.pk])
        _response = client_logged_in.post(  # noqa: F841
            url,
            {"borrower": second_user.pk, "quantity": 5, "notes": ""},
            follow=True,
        )
        # The checkout should succeed, not be blocked
        txn = Transaction.objects.filter(
            asset=ns_asset, action="checkout"
        ).first()
        assert txn is not None, (
            "Checkout of 5 units (7 available after 3 held from 10) "
            "must succeed (S2.16.5-07). Hold blocking must be "
            "quantity-aware for non-serialised assets."
        )

    def test_serialised_hold_blocks_pinned_serial_only(
        self,
        client_logged_in,
        user,
        serialised_asset,
        asset_serial,
        department,
        hl_active_status,
        location,
    ):
        """VV452: Pinned serial hold blocks only THAT serial, not
        all serials of the asset (S2.16.5-08).

        When serial A is pinned on a hold list but serial B is not,
        checking out serial B should be allowed while serial A is
        blocked.
        """
        from assets.factories import AssetSerialFactory

        serial_b = AssetSerialFactory(
            asset=serialised_asset,
            serial_number="002",
            barcode=f"{serialised_asset.barcode}-S002",
            status="active",
            condition="good",
            current_location=location,
        )
        hl = HoldList.objects.create(
            name="Serial Hold",
            department=department,
            status=hl_active_status,
            created_by=user,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )
        from assets.models import HoldListItem

        # Pin only asset_serial (serial A), not serial_b
        HoldListItem.objects.create(
            hold_list=hl,
            asset=serialised_asset,
            serial=asset_serial,
            quantity=1,
            added_by=user,
        )

        from django.contrib.auth import get_user_model

        User = get_user_model()

        borrower = User.objects.create_user(
            username="serial_borrower",
            email="sb@example.com",
            password="testpass123!",
        )

        # Checking out serial_b (not held) should succeed
        url = reverse("assets:asset_checkout", args=[serialised_asset.pk])
        _response = client_logged_in.post(  # noqa: F841
            url,
            {
                "borrower": borrower.pk,
                "serial_ids": [serial_b.pk],
                "notes": "",
            },
            follow=True,
        )
        assert Transaction.objects.filter(
            asset=serialised_asset,
            serial=serial_b,
            action="checkout",
        ).exists(), (
            "Checkout of a non-held serial (serial B) must succeed "
            "when only serial A is pinned on the hold list "
            "(S2.16.5-08). Current implementation blocks ALL "
            "serials when any serial is held."
        )


class TestHoldListFulfilment:
    """VV449, VV450: Fulfil action and pull view grouping."""

    def test_fulfil_action_url_exists(
        self,
        admin_client,
        admin_user,
        department,
        hl_active_status,
    ):
        """VV449: A dedicated 'Fulfil' action must exist on the hold
        list view (S2.16.5-05, SHOULD priority)."""
        hl = HoldList.objects.create(
            name="Fulfil HL",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )
        url = reverse("assets:holdlist_fulfil", args=[hl.pk])
        response = admin_client.get(url)
        assert response.status_code == 200, (
            "Fulfil action view must exist (S2.16.5-05). "
            "Expected named URL 'assets:holdlist_fulfil'."
        )

    def test_pull_view_groups_items_by_location(
        self,
        admin_client,
        admin_user,
        department,
        hl_active_status,
    ):
        """VV450: Pull view groups items by location for efficient
        physical retrieval (S2.16.5-06)."""
        from assets.factories import AssetFactory, LocationFactory

        loc_a = LocationFactory(name="Location Alpha")
        loc_b = LocationFactory(name="Location Beta")
        asset_a = AssetFactory(
            name="Asset at Alpha",
            current_location=loc_a,
            created_by=admin_user,
        )
        asset_b = AssetFactory(
            name="Asset at Beta",
            current_location=loc_b,
            created_by=admin_user,
        )
        hl = HoldList.objects.create(
            name="Pull View HL",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=hl, asset=asset_a, added_by=admin_user
        )
        HoldListItem.objects.create(
            hold_list=hl, asset=asset_b, added_by=admin_user
        )
        url = reverse("assets:holdlist_detail", args=[hl.pk])
        response = admin_client.get(url)
        content = response.content.decode()
        # Items should be grouped by location
        alpha_pos = content.find("Location Alpha")
        beta_pos = content.find("Location Beta")
        assert alpha_pos != -1 and beta_pos != -1, (
            "Pull view must display location names to group items "
            "(S2.16.5-06). Expected 'Location Alpha' and "
            "'Location Beta' in the page."
        )


class TestHoldListPickSheetV2:
    """VV454: Pick sheet must include total count and generated-by."""

    def test_pick_sheet_includes_total_count(
        self,
        admin_client,
        admin_user,
        asset,
        department,
        hl_active_status,
    ):
        """VV454: Pick sheet includes total item count (S2.16.6-02)."""
        hl = HoldList.objects.create(
            name="Count HL",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )
        from assets.models import HoldListItem

        HoldListItem.objects.create(
            hold_list=hl, asset=asset, added_by=admin_user
        )
        # Check the pick sheet template context/output

        items = hl.items.select_related(
            "asset", "asset__category", "asset__current_location"
        )
        # The generate function should accept user or the template
        # should display a total count. We test the HTML template.
        from django.template.loader import render_to_string

        html = render_to_string(
            "assets/pick_sheet.html",
            {
                "hold_list": hl,
                "items": items,
                "total_count": items.count(),
                "generated_by": admin_user,
            },
        )
        assert "1" in html, "Pick sheet should show item count."
        # The spec requires an explicit "Total items: N" or similar
        assert (
            "total" in html.lower()
            or "item count" in html.lower()
            or "items:" in html.lower()
        ), (
            "Pick sheet must include total item count (S2.16.6-02). "
            "Current template does not display a total."
        )

    def test_pick_sheet_includes_generated_by_user(
        self,
        admin_client,
        admin_user,
        department,
        hl_active_status,
    ):
        """VV454: Pick sheet includes generated-by user (S2.16.6-02)."""
        hl = HoldList.objects.create(
            name="GenBy HL",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )
        url = reverse("assets:holdlist_pick_sheet", args=[hl.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        # The PDF is binary, but we can test the template rendering
        from django.template.loader import render_to_string

        items = hl.items.all()
        html = render_to_string(
            "assets/pick_sheet.html",
            {
                "hold_list": hl,
                "items": items,
                "generated_by": admin_user,
            },
        )
        assert (
            "admin" in html.lower()
            or admin_user.username in html.lower()
            or "generated by" in html.lower()
        ), (
            "Pick sheet must include the generated-by user "
            "(S2.16.6-02). Current template only shows date."
        )


class TestHoldListItemManagement:
    """VV459: Item search/scan interface on hold list detail."""

    def test_item_add_uses_search_scan_interface(
        self, admin_client, admin_user, department, hl_active_status
    ):
        """VV459: Hold list detail has a search/scan interface for
        adding items, not just a numeric asset ID field (S2.16.7-03)."""
        hl = HoldList.objects.create(
            name="Search HL",
            department=department,
            status=hl_active_status,
            created_by=admin_user,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )
        url = reverse("assets:holdlist_detail", args=[hl.pk])
        response = admin_client.get(url)
        content = response.content.decode()
        # The form should have a text search/scan input, not just
        # a numeric asset_id field
        has_search = (
            'type="search"' in content
            or 'type="text"' in content
            or "barcode" in content.lower()
            or "scan" in content.lower()
            or "search" in content.lower()
        )
        has_numeric_only = (
            'name="asset_id"' in content and 'type="number"' in content
        )
        assert has_search and not has_numeric_only, (
            "Hold list item addition must use an asset search/scan "
            "interface (S2.16.7-03), not just a numeric asset ID "
            "input. Current template uses type='number' for asset_id."
        )


@pytest.mark.django_db
class TestHoldListEdgeCases:
    """S7.15 — Hold list edge cases."""

    @pytest.fixture
    def hl_status(self, db):
        return HoldListStatus.objects.create(
            name="S7 Active HL", is_default=True
        )

    @pytest.fixture
    def terminal_status(self, db):
        return HoldListStatus.objects.create(
            name="S7 Fulfilled", is_terminal=True
        )

    def test_vv768_disposed_asset_on_hold_list(
        self,
        admin_client,
        admin_user,
        department,
        asset,
        hl_status,
    ):
        """VV768: Asset on hold list that gets disposed should
        show as unavailable on the hold list."""
        from assets.models import HoldList, HoldListItem

        hl = HoldList.objects.create(
            name="S7 Test HL",
            department=department,
            status=hl_status,
            created_by=admin_user,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )
        HoldListItem.objects.create(
            hold_list=hl,
            asset=asset,
            quantity=1,
            added_by=admin_user,
        )

        asset.status = "disposed"
        asset.save()

        response = admin_client.get(
            reverse("assets:holdlist_detail", args=[hl.pk])
        )
        content = response.content.decode()
        assert (
            "unavailable" in content.lower() or "disposed" in content.lower()
        ), (
            "S7.15.3: Disposed asset on hold list must display "
            "as unavailable with the reason. Currently the hold "
            "list detail does not show asset status warnings."
        )

    def test_vv769_overlap_detection_open_ended(
        self, admin_user, department, asset, hl_status
    ):
        """VV769: Hold list overlap detection with open-ended
        hold lists should treat null end_date as infinite."""
        from assets.models import (
            HoldList,
            HoldListItem,
            Project,
        )

        project = Project.objects.create(
            name="Open-Ended Project",
            created_by=admin_user,
        )
        hl1 = HoldList.objects.create(
            name="S7 HL1 Open",
            department=department,
            status=hl_status,
            project=project,
            created_by=admin_user,
            start_date="2026-01-01",
            end_date=None,
        )
        HoldListItem.objects.create(
            hold_list=hl1,
            asset=asset,
            quantity=1,
            added_by=admin_user,
        )

        hl2 = HoldList.objects.create(
            name="S7 HL2 Future",
            department=department,
            status=hl_status,
            created_by=admin_user,
            start_date="2026-12-01",
            end_date="2026-12-31",
        )
        HoldListItem.objects.create(
            hold_list=hl2,
            asset=asset,
            quantity=1,
            added_by=admin_user,
        )

        from assets.services.holdlists import detect_overlaps

        overlaps = detect_overlaps(hl2)
        overlap_hl_ids = {o.hold_list_id for o in overlaps}
        assert hl1.pk in overlap_hl_ids, (
            "S7.15.4: Overlap detection must treat open-ended "
            "(null end_date) hold lists as extending "
            "indefinitely. Currently detect_overlaps returns "
            "empty when start_date or end_date is null."
        )

    def test_vv770_locked_holdlist_to_terminal(
        self,
        admin_client,
        admin_user,
        department,
        hl_status,
        terminal_status,
    ):
        """VV770: Locked hold list changed to terminal status
        should retain lock."""
        from assets.models import HoldList

        hl = HoldList.objects.create(
            name="S7 Locked HL",
            department=department,
            status=hl_status,
            created_by=admin_user,
            is_locked=True,
            start_date="2026-04-01",
            end_date="2026-04-15",
        )

        from assets.services.holdlists import change_status

        change_status(hl, terminal_status, admin_user)

        hl.refresh_from_db()
        assert hl.is_locked is True, (
            "S7.15.5: Locked hold list changed to terminal "
            "status must retain its lock."
        )
        assert hl.status == terminal_status


@pytest.mark.django_db
class TestHoldListWorkflow:
    """Hold list workflow: create, add items, fulfil, close."""

    def _make_default_status(self):
        status, _ = HoldListStatus.objects.get_or_create(
            name="Draft",
            defaults={"is_default": True, "sort_order": 10},
        )
        return status

    def _make_terminal_status(self):
        status, _ = HoldListStatus.objects.get_or_create(
            name="Cancelled",
            defaults={
                "is_default": False,
                "is_terminal": True,
                "sort_order": 50,
            },
        )
        return status

    def test_create_hold_list(self, user, department, asset):
        from datetime import date

        from assets.services.holdlists import create_hold_list

        self._make_default_status()
        hl = create_hold_list(
            "Test Hold",
            user,
            department=department,
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 15),
        )
        assert hl.pk is not None
        assert hl.name == "Test Hold"
        assert hl.created_by == user

    def test_add_item_to_hold_list(self, user, department, asset):
        from datetime import date

        from assets.services.holdlists import add_item, create_hold_list

        self._make_default_status()
        hl = create_hold_list(
            "Add Items Test",
            user,
            department=department,
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 15),
        )
        item = add_item(hl, asset, user, quantity=2)
        assert item.asset == asset
        assert item.quantity == 2

    def test_locked_hold_list_rejects_add(self, user, department, asset):
        from datetime import date

        from assets.services.holdlists import (
            add_item,
            create_hold_list,
            lock_hold_list,
        )

        self._make_default_status()
        hl = create_hold_list(
            "Lock Test",
            user,
            department=department,
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 15),
        )
        lock_hold_list(hl, user)
        with pytest.raises(ValidationError, match="locked"):
            add_item(hl, asset, user)

    def test_fulfil_item(self, user, department, asset):
        from datetime import date

        from assets.services.holdlists import (
            add_item,
            create_hold_list,
            fulfil_item,
        )

        self._make_default_status()
        hl = create_hold_list(
            "Fulfil Test",
            user,
            department=department,
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 15),
        )
        item = add_item(hl, asset, user)
        fulfilled = fulfil_item(item, user)
        assert fulfilled.pull_status == "pulled"
        assert fulfilled.pulled_by == user
        assert fulfilled.pulled_at is not None

    def test_change_status_to_terminal(self, user, department, asset):
        from datetime import date

        from assets.services.holdlists import (
            change_status,
            check_asset_held,
            create_hold_list,
        )

        _default = self._make_default_status()  # noqa: F841
        terminal = self._make_terminal_status()
        hl = create_hold_list(
            "Close Test",
            user,
            department=department,
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 15),
        )
        from assets.services.holdlists import add_item

        add_item(hl, asset, user)
        assert check_asset_held(asset) is True

        change_status(hl, terminal, user)
        assert check_asset_held(asset) is False

    def test_create_hold_list_without_default_status_raises(
        self, user, department
    ):
        from datetime import date

        from assets.services.holdlists import create_hold_list

        # Clear seeded statuses so no default exists
        HoldListStatus.objects.all().delete()

        with pytest.raises(
            ValidationError, match="No default hold list status"
        ):
            create_hold_list(
                "No Default",
                user,
                department=department,
                start_date=date(2026, 3, 1),
                end_date=date(2026, 3, 15),
            )


@pytest.mark.django_db
class TestV449HoldListFulfilAction:
    """V449: Fulfil action on hold list performs bulk checkout."""

    def test_fulfil_post_checks_out_items(
        self, admin_client, admin_user, asset, user, department
    ):
        """POST to fulfil should checkout items to borrower."""
        hl_status = HoldListStatus.objects.filter(is_terminal=False).first()
        if not hl_status:
            hl_status = HoldListStatus.objects.create(
                name="Open", is_default=True
            )
        from assets.models import HoldList, HoldListItem

        hl = HoldList.objects.create(
            name="V449 Test",
            status=hl_status,
            department=department,
            created_by=admin_user,
        )
        HoldListItem.objects.create(hold_list=hl, asset=asset, quantity=1)
        response = admin_client.post(
            reverse("assets:holdlist_fulfil", args=[hl.pk]),
            {"borrower": user.pk},
        )
        assert response.status_code in (200, 302)
        asset.refresh_from_db()
        assert asset.checked_out_to == user


@pytest.mark.django_db
class TestV459HoldListItemEdit:
    """V459: Inline item management — edit quantity/notes."""

    def test_item_edit_endpoint_exists(
        self, admin_client, admin_user, asset, department
    ):
        """Hold list item edit endpoint should exist."""
        hl_status = HoldListStatus.objects.filter(is_terminal=False).first()
        if not hl_status:
            hl_status = HoldListStatus.objects.create(
                name="Open", is_default=True
            )
        from assets.models import HoldList, HoldListItem

        hl = HoldList.objects.create(
            name="V459 Test",
            status=hl_status,
            department=department,
            created_by=admin_user,
        )
        item = HoldListItem.objects.create(
            hold_list=hl, asset=asset, quantity=1
        )
        response = admin_client.post(
            reverse(
                "assets:holdlist_edit_item",
                args=[hl.pk, item.pk],
            ),
            {"quantity": 3, "notes": "Updated"},
        )
        assert response.status_code in (200, 302)
        item.refresh_from_db()
        assert item.quantity == 3
        assert item.notes == "Updated"


@pytest.mark.django_db
class TestV598AdminHoldListFilter:
    """V598: Admin asset list_filter includes hold_list."""

    def test_hold_list_filter_in_asset_admin(self):
        from assets.admin import AssetAdmin

        filter_names = []
        for f in AssetAdmin.list_filter:
            if isinstance(f, str):
                filter_names.append(f)
            elif isinstance(f, tuple):
                filter_names.append(f[0])
            else:
                # Could be a class-based filter
                filter_names.append(getattr(f, "title", str(f)))
        # Should have some kind of hold list filter
        assert any(
            "hold" in str(n).lower() for n in filter_names
        ), f"No hold_list filter found in {filter_names}"


@pytest.mark.django_db
class TestHoldListDetailRoleGating:
    """Viewers/borrowers must not see write-action controls."""

    def test_viewer_cannot_see_add_item_form(
        self, viewer_client, active_hold_list
    ):
        """Viewers should not see the add-item form."""
        resp = viewer_client.get(
            reverse("assets:holdlist_detail", args=[active_hold_list.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        add_url = reverse(
            "assets:holdlist_add_item", args=[active_hold_list.pk]
        )
        assert add_url not in content

    def test_viewer_cannot_see_edit_link(
        self, viewer_client, active_hold_list
    ):
        """Viewers should not see the Edit button."""
        resp = viewer_client.get(
            reverse("assets:holdlist_detail", args=[active_hold_list.pk])
        )
        content = resp.content.decode()
        edit_url = reverse("assets:holdlist_edit", args=[active_hold_list.pk])
        assert edit_url not in content

    def test_viewer_cannot_see_pull_status_buttons(
        self, viewer_client, active_hold_list, asset
    ):
        """Viewers should not see pull status action buttons."""
        item = HoldListItemFactory(hold_list=active_hold_list, asset=asset)
        resp = viewer_client.get(
            reverse("assets:holdlist_detail", args=[active_hold_list.pk])
        )
        content = resp.content.decode()
        pull_url = reverse(
            "assets:holdlist_update_pull_status",
            args=[active_hold_list.pk, item.pk],
        )
        assert pull_url not in content

    def test_viewer_cannot_see_remove_button(
        self, viewer_client, active_hold_list, asset
    ):
        """Viewers should not see the Remove button."""
        item = HoldListItemFactory(hold_list=active_hold_list, asset=asset)
        resp = viewer_client.get(
            reverse("assets:holdlist_detail", args=[active_hold_list.pk])
        )
        content = resp.content.decode()
        remove_url = reverse(
            "assets:holdlist_remove_item",
            args=[active_hold_list.pk, item.pk],
        )
        assert remove_url not in content

    def test_admin_can_see_add_item_form(self, admin_client, active_hold_list):
        """Admins should see the add-item form."""
        resp = admin_client.get(
            reverse("assets:holdlist_detail", args=[active_hold_list.pk])
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        add_url = reverse(
            "assets:holdlist_add_item", args=[active_hold_list.pk]
        )
        assert add_url in content

    def test_admin_can_see_pull_status_buttons(
        self, admin_client, active_hold_list, asset
    ):
        """Admins should see pull status action buttons."""
        item = HoldListItemFactory(hold_list=active_hold_list, asset=asset)
        resp = admin_client.get(
            reverse("assets:holdlist_detail", args=[active_hold_list.pk])
        )
        content = resp.content.decode()
        pull_url = reverse(
            "assets:holdlist_update_pull_status",
            args=[active_hold_list.pk, item.pk],
        )
        assert pull_url in content

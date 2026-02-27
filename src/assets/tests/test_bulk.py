"""Tests for bulk operations."""

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
    Location,
)

User = get_user_model()


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


class TestBulkCheckoutDueDate:
    """Regression: bulk_checkout must set due_date at creation time.

    Previously due_date was set via QuerySet.update() on a sliced
    queryset, which Django disallows.  Now due_date is a parameter
    to bulk_checkout and set on each Transaction at creation.
    """

    def test_due_date_set_on_transactions(self, asset, second_user, user):
        from django.utils import timezone

        from assets.models import Transaction
        from assets.services.bulk import bulk_checkout

        due = timezone.now() + timezone.timedelta(days=7)
        result = bulk_checkout(
            [asset.pk],
            second_user.pk,
            user,
            due_date=due,
        )
        assert result["checked_out"] == 1
        txn = Transaction.objects.filter(
            asset=asset, action="checkout"
        ).latest("timestamp")
        assert txn.due_date is not None
        assert txn.due_date == due

    def test_no_due_date_leaves_null(self, asset, second_user, user):
        from assets.models import Transaction
        from assets.services.bulk import bulk_checkout

        bulk_checkout([asset.pk], second_user.pk, user)
        txn = Transaction.objects.filter(
            asset=asset, action="checkout"
        ).latest("timestamp")
        assert txn.due_date is None


class TestBulkCheckinToHome:
    """Regression: bulk location check-in must return each asset to
    its own home_location, not a single shared location.
    """

    def test_assets_return_to_their_own_home_locations(
        self, user, second_user, category
    ):
        from assets.services.bulk import bulk_checkin_to_home

        home_a = Location.objects.create(name="Home A")
        home_b = Location.objects.create(name="Home B")
        elsewhere = Location.objects.create(name="Elsewhere")

        asset_a = AssetFactory(
            name="Asset A",
            status="active",
            category=category,
            current_location=elsewhere,
            home_location=home_a,
            checked_out_to=second_user,
            created_by=user,
        )
        asset_b = AssetFactory(
            name="Asset B",
            status="active",
            category=category,
            current_location=elsewhere,
            home_location=home_b,
            checked_out_to=second_user,
            created_by=user,
        )

        result = bulk_checkin_to_home([asset_a.pk, asset_b.pk], user)
        assert result["checked_in"] == 2

        asset_a.refresh_from_db()
        asset_b.refresh_from_db()
        assert asset_a.current_location == home_a
        assert asset_b.current_location == home_b
        assert asset_a.checked_out_to is None
        assert asset_b.checked_out_to is None

    def test_skips_assets_without_home_location(
        self, user, second_user, category, location
    ):
        from assets.services.bulk import bulk_checkin_to_home

        asset = AssetFactory(
            name="No Home",
            status="active",
            category=category,
            current_location=location,
            home_location=None,
            checked_out_to=second_user,
            created_by=user,
        )
        result = bulk_checkin_to_home([asset.pk], user)
        assert result["checked_in"] == 0
        assert "No Home" in result["no_home"]

    def test_skips_assets_not_checked_out(self, user, category, location):
        from assets.services.bulk import bulk_checkin_to_home

        asset = AssetFactory(
            name="Not Out",
            status="active",
            category=category,
            current_location=location,
            home_location=location,
            created_by=user,
        )
        result = bulk_checkin_to_home([asset.pk], user)
        assert result["checked_in"] == 0
        assert "Not Out" in result["skipped"]

    def test_serialised_assets_checked_in_per_serial(
        self, user, second_user, category, location
    ):
        """Serialised assets are checked in at the serial level —
        each checked-out AssetSerial gets its own transaction and
        is returned to the asset's home_location."""
        from assets.models import AssetSerial, Transaction
        from assets.services.bulk import bulk_checkin_to_home

        home = Location.objects.create(name="Serial Home")
        elsewhere = Location.objects.create(name="Elsewhere")
        asset = AssetFactory(
            name="Wireless Mic",
            status="active",
            category=category,
            current_location=elsewhere,
            home_location=home,
            checked_out_to=second_user,
            is_serialised=True,
            created_by=user,
        )
        s1 = AssetSerialFactory(
            asset=asset,
            serial_number="S001",
            barcode=f"{asset.barcode}-S001",
            status="active",
            current_location=elsewhere,
            checked_out_to=second_user,
        )
        s2 = AssetSerialFactory(
            asset=asset,
            serial_number="S002",
            barcode=f"{asset.barcode}-S002",
            status="active",
            current_location=elsewhere,
            checked_out_to=second_user,
        )

        result = bulk_checkin_to_home([asset.pk], user)
        assert result["checked_in"] == 1
        assert result["skipped"] == []

        s1.refresh_from_db()
        s2.refresh_from_db()
        assert s1.checked_out_to is None
        assert s1.current_location == home
        assert s2.checked_out_to is None
        assert s2.current_location == home

        asset.refresh_from_db()
        assert asset.checked_out_to is None
        assert asset.current_location == home

        # Verify per-serial transactions were created
        serial_txns = Transaction.objects.filter(
            asset=asset,
            action="checkin",
            serial__isnull=False,
        )
        assert serial_txns.count() == 2


# ============================================================
# BATCH F: SHOULD-IMPLEMENT MEDIUM EFFORT (V25)
# ============================================================


class TestAdminBulkActionsV25:
    """V25: Admin bulk actions."""

    def test_mark_lost_action(self, admin_client, asset, admin_user):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from assets.admin import AssetAdmin

        admin_obj = AssetAdmin(Asset, AdminSite())
        qs = Asset.objects.filter(pk=asset.pk)

        factory = RequestFactory()
        request = factory.post(
            "/admin/assets/asset/",
            {"apply": "1", "notes": "Lost during transport"},
        )
        request.user = admin_user
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        admin_obj.mark_lost(request, qs)
        asset.refresh_from_db()
        assert asset.status == "lost"

    def test_mark_stolen_action(self, admin_client, asset, admin_user):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from assets.admin import AssetAdmin

        admin_obj = AssetAdmin(Asset, AdminSite())
        qs = Asset.objects.filter(pk=asset.pk)

        factory = RequestFactory()
        request = factory.post(
            "/admin/assets/asset/",
            {"apply": "1", "notes": "Stolen from warehouse"},
        )
        request.user = admin_user
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        admin_obj.mark_stolen(request, qs)
        asset.refresh_from_db()
        assert asset.status == "stolen"

    def test_bulk_transfer_action(self, admin_client, asset, admin_user):
        new_location = Location.objects.create(name="New Warehouse")
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from assets.admin import AssetAdmin

        factory = RequestFactory()
        request = factory.post(
            "/admin/assets/asset/", {"location": new_location.pk, "apply": "1"}
        )
        request.user = admin_user
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        admin_obj = AssetAdmin(Asset, AdminSite())
        qs = Asset.objects.filter(pk=asset.pk)
        admin_obj.bulk_transfer(request, qs)
        asset.refresh_from_db()
        assert asset.current_location == new_location

    def test_bulk_change_category_action(
        self, admin_client, asset, department, admin_user
    ):
        new_category = Category.objects.create(
            name="New Category", department=department
        )
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from assets.admin import AssetAdmin

        factory = RequestFactory()
        request = factory.post(
            "/admin/assets/asset/", {"category": new_category.pk, "apply": "1"}
        )
        request.user = admin_user
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        admin_obj = AssetAdmin(Asset, AdminSite())
        qs = Asset.objects.filter(pk=asset.pk)
        admin_obj.bulk_change_category(request, qs)
        asset.refresh_from_db()
        assert asset.category == new_category

    def test_print_labels_redirects(self, admin_client, asset):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory

        from assets.admin import AssetAdmin

        factory = RequestFactory()
        request = factory.get("/admin/assets/asset/")

        admin_obj = AssetAdmin(Asset, AdminSite())
        qs = Asset.objects.filter(pk=asset.pk)
        response = admin_obj.print_labels(request, qs)
        assert response.status_code == 302
        assert "labels/pregenerate" in response.url
        assert f"ids={asset.pk}" in response.url


# ============================================================
# M2: Bulk transfer uses bulk_create
# ============================================================


class TestBulkTransferEfficiency:
    """Bulk transfer should use bulk_create for efficiency."""

    def test_bulk_transfer_creates_transactions(
        self, user, category, location
    ):
        """Verify bulk transfer works correctly with multiple assets."""
        assets = []
        for i in range(5):
            a = Asset(
                name=f"Bulk Asset {i}",
                category=category,
                current_location=location,
                status="active",
                created_by=user,
            )
            a.save()
            assets.append(a)

        from assets.services.bulk import bulk_transfer

        new_loc = Location.objects.create(name="Bulk Dest Efficient")
        result = bulk_transfer([a.pk for a in assets], new_loc.pk, user)
        assert result["transferred"] == 5
        for a in assets:
            a.refresh_from_db()
            assert a.current_location == new_loc

    def test_bulk_transfer_efficiency(self, user, category, location):
        """Verify fewer queries with bulk operations."""
        assets = []
        for i in range(5):
            a = Asset(
                name=f"Efficient Asset {i}",
                category=category,
                current_location=location,
                status="active",
                created_by=user,
            )
            a.save()
            assets.append(a)

        from assets.services.bulk import bulk_transfer

        new_loc = Location.objects.create(name="Efficient Dest")

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            bulk_transfer([a.pk for a in assets], new_loc.pk, user)

        # With bulk_create + filter().update(), the query count
        # should be constant regardless of asset count (setup +
        # 1 bulk_create + 1 update), well under the old N+1 pattern.
        assert len(ctx) < 15


# ============================================================
# HOLD LIST CHECKOUT BLOCKING TESTS (S2.16.5)
# ============================================================


@pytest.mark.django_db
class TestBulkEditDraftOnly:
    """V259 — S2.8.3-01: Bulk category edit restricted to drafts."""

    def test_bulk_category_edit_only_updates_drafts(
        self, admin_client, asset, draft_asset, category
    ):
        """Category assignment only applies to draft assets."""
        from assets.services.bulk import bulk_edit

        new_cat = Category.objects.create(
            name="New Cat",
            department=category.department,
        )
        count = bulk_edit(
            [asset.pk, draft_asset.pk],
            category_id=new_cat.pk,
        )
        # Only draft should be updated
        assert count == 1
        draft_asset.refresh_from_db()
        assert draft_asset.category == new_cat
        asset.refresh_from_db()
        assert asset.category == category  # unchanged

    def test_bulk_location_edit_applies_to_all(
        self, admin_client, asset, draft_asset, location
    ):
        """Location assignment applies to all assets."""
        from assets.services.bulk import bulk_edit

        new_loc = Location.objects.create(name="New Loc")
        count = bulk_edit(
            [asset.pk, draft_asset.pk],
            location_id=new_loc.pk,
        )
        assert count == 2
        asset.refresh_from_db()
        assert asset.current_location == new_loc
        draft_asset.refresh_from_db()
        assert draft_asset.current_location == new_loc


# ============================================================
# V264 (S2.8.2-01): Bulk status change
# ============================================================


@pytest.mark.django_db
class TestV264BulkStatusChange:
    """V264: bulk_actions POST with action=status_change updates selected
    assets."""

    def test_bulk_status_change_updates_assets(
        self, admin_client, asset, category, location, admin_user
    ):
        """bulk_actions with status_change should update asset statuses."""
        from assets.models import Asset

        asset2 = Asset.objects.create(
            name="Second Asset",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=admin_user,
        )
        url = reverse("assets:bulk_actions")
        response = admin_client.post(
            url,
            {
                "asset_ids": [asset.pk, asset2.pk],
                "bulk_action": "status_change",
                "new_status": "retired",
            },
        )
        assert response.status_code == 302  # Redirect after success
        asset.refresh_from_db()
        asset2.refresh_from_db()
        assert asset.status == "retired"
        assert asset2.status == "retired"


# ============================================================
# V265 (S2.8.2-02): Bulk location transfer
# ============================================================


@pytest.mark.django_db
class TestV265BulkLocationTransfer:
    """V265: bulk_actions POST with action=transfer moves selected assets."""

    def test_bulk_transfer_moves_assets(
        self, admin_client, asset, category, location, admin_user
    ):
        """bulk_actions with transfer should move assets to new location."""
        from assets.models import Asset, Location

        asset2 = Asset.objects.create(
            name="Third Asset",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=admin_user,
        )
        new_location = Location.objects.create(name="New Storage")
        url = reverse("assets:bulk_actions")
        response = admin_client.post(
            url,
            {
                "asset_ids": [asset.pk, asset2.pk],
                "bulk_action": "transfer",
                "location": new_location.pk,
            },
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        asset2.refresh_from_db()
        assert asset.current_location == new_location
        assert asset2.current_location == new_location

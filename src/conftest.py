"""Shared pytest fixtures and factories for PROPS tests."""

import pytest

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

# Use local filesystem storage for tests (avoids S3 credential errors)
settings.STORAGES["default"] = {
    "BACKEND": "django.core.files.storage.FileSystemStorage",
}
settings.STORAGES["staticfiles"] = {
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}

# Run Celery tasks synchronously in tests
settings.CELERY_TASK_ALWAYS_EAGER = True
settings.CELERY_TASK_EAGER_PROPAGATES = True

# Use in-memory cache for tests (avoids Redis connection errors)
settings.CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Use in-memory channel layer for tests (avoids Redis for WS tests)
settings.CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Shorten print service auth timeout for fast tests (default 30s)
settings.PRINT_SERVICE_AUTH_TIMEOUT = 0.5


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the in-memory cache before each test.

    Prevents rate-limit counters (django_ratelimit) from bleeding
    across tests, which causes flaky failures under pytest-xdist.
    """
    from django.core.cache import cache

    cache.clear()


def _ensure_group_permissions(group_name):
    """Create a group and assign correct permissions.

    Mirrors setup_groups for the test database.

    This ensures tests that use permission-based role resolution
    work correctly even without running the full setup_groups command.
    """
    from accounts.models import CustomUser
    from assets.models import Asset, Category, Department, Location

    group, _ = Group.objects.get_or_create(name=group_name)

    asset_ct = ContentType.objects.get_for_model(Asset)
    category_ct = ContentType.objects.get_for_model(Category)
    location_ct = ContentType.objects.get_for_model(Location)
    department_ct = ContentType.objects.get_for_model(Department)
    user_ct = ContentType.objects.get_for_model(CustomUser)

    def get_perm(codename, ct=None):
        if ct:
            return Permission.objects.get(codename=codename, content_type=ct)
        return Permission.objects.get(codename=codename)

    # Common view permissions
    view_asset = get_perm("view_asset", asset_ct)
    view_category = get_perm("view_category", category_ct)
    view_location = get_perm("view_location", location_ct)
    view_department = get_perm("view_department", department_ct)

    perm_map = {
        "System Admin": [
            view_asset,
            get_perm("add_asset", asset_ct),
            get_perm("change_asset", asset_ct),
            get_perm("delete_asset", asset_ct),
            get_perm("can_checkout_asset", asset_ct),
            get_perm("can_checkin_asset", asset_ct),
            get_perm("can_print_labels", asset_ct),
            get_perm("can_merge_assets", asset_ct),
            get_perm("can_export_assets", asset_ct),
            get_perm("can_handover_asset", asset_ct),
            get_perm("override_hold_checkout", asset_ct),
            get_perm("can_be_borrower", asset_ct),
            get_perm("add_category", category_ct),
            get_perm("change_category", category_ct),
            get_perm("delete_category", category_ct),
            view_category,
            get_perm("add_location", location_ct),
            get_perm("change_location", location_ct),
            get_perm("delete_location", location_ct),
            view_location,
            get_perm("add_department", department_ct),
            get_perm("change_department", department_ct),
            get_perm("delete_department", department_ct),
            view_department,
            get_perm("can_approve_users", user_ct),
        ],
        "Department Manager": [
            view_asset,
            get_perm("add_asset", asset_ct),
            get_perm("change_asset", asset_ct),
            get_perm("delete_asset", asset_ct),
            get_perm("can_checkout_asset", asset_ct),
            get_perm("can_checkin_asset", asset_ct),
            get_perm("can_print_labels", asset_ct),
            get_perm("can_merge_assets", asset_ct),
            get_perm("can_export_assets", asset_ct),
            get_perm("can_handover_asset", asset_ct),
            get_perm("override_hold_checkout", asset_ct),
            get_perm("add_category", category_ct),
            get_perm("change_category", category_ct),
            get_perm("delete_category", category_ct),
            view_category,
            view_location,
            view_department,
        ],
        "Member": [
            view_asset,
            get_perm("add_asset", asset_ct),
            get_perm("change_asset", asset_ct),
            get_perm("can_checkout_asset", asset_ct),
            get_perm("can_checkin_asset", asset_ct),
            get_perm("can_print_labels", asset_ct),
            get_perm("can_export_assets", asset_ct),
            view_category,
            view_location,
            view_department,
        ],
        "Viewer": [
            view_asset,
            get_perm("can_export_assets", asset_ct),
            view_category,
            view_location,
            view_department,
        ],
        "Borrower": [
            get_perm("can_be_borrower", asset_ct),
        ],
    }

    if group_name in perm_map:
        group.permissions.set(perm_map[group_name])

    return group


from assets.factories import (  # noqa: E402
    AssetFactory,
    AssetSerialFactory,
    CategoryFactory,
    DepartmentFactory,
    LocationFactory,
    TagFactory,
    UserFactory,
)
from assets.models import AssetKit  # noqa: E402

# --- User fixtures ---


@pytest.fixture
def password():
    return "testpass123!"


@pytest.fixture
def user(db, password):
    group = _ensure_group_permissions("Member")
    u = UserFactory(
        username="testuser",
        email="test@example.com",
        password=password,
        display_name="Test User",
    )
    u.groups.add(group)
    return u


@pytest.fixture
def admin_user(db, password):
    u = UserFactory(
        username="admin",
        email="admin@example.com",
        password=password,
        is_staff=True,
        is_superuser=True,
    )
    return u


@pytest.fixture
def member_user(db, password):
    group = _ensure_group_permissions("Member")
    u = UserFactory(
        username="member",
        email="member@example.com",
        password=password,
        display_name="",
    )
    u.groups.add(group)
    return u


@pytest.fixture
def viewer_user(db, password):
    group = _ensure_group_permissions("Viewer")
    u = UserFactory(
        username="viewer",
        email="viewer@example.com",
        password=password,
        display_name="",
    )
    u.groups.add(group)
    return u


@pytest.fixture
def client_logged_in(client, user, password):
    client.login(username=user.username, password=password)
    return client


@pytest.fixture
def admin_client(client, admin_user, password):
    client.login(username=admin_user.username, password=password)
    return client


@pytest.fixture
def member_client(client, member_user, password):
    client.login(username=member_user.username, password=password)
    return client


@pytest.fixture
def viewer_client(client, viewer_user, password):
    client.login(username=viewer_user.username, password=password)
    return client


@pytest.fixture
def dept_manager_user(db, password, department):
    group = _ensure_group_permissions("Department Manager")
    u = UserFactory(
        username="deptmanager",
        email="deptmanager@example.com",
        password=password,
        display_name="Dept Manager",
    )
    u.groups.add(group)
    department.managers.add(u)
    return u


@pytest.fixture
def dept_manager_client(client, dept_manager_user, password):
    client.login(username=dept_manager_user.username, password=password)
    return client


# --- Core model fixtures ---


@pytest.fixture
def department(db):
    return DepartmentFactory(
        name="Props",
        description="Props department",
    )


@pytest.fixture
def tag(db):
    return TagFactory(name="fragile", color="red")


@pytest.fixture
def category(department):
    return CategoryFactory(
        name="Hand Props",
        description="Small hand-held items",
        department=department,
    )


@pytest.fixture
def location(db):
    return LocationFactory(
        name="Main Store",
        address="123 Theatre St",
    )


@pytest.fixture
def child_location(location):
    return LocationFactory(
        name="Shelf A",
        parent=location,
    )


@pytest.fixture
def asset(category, location, user):
    return AssetFactory(
        name="Test Prop",
        description="A test prop for testing",
        category=category,
        current_location=location,
        status="active",
        is_serialised=False,
        created_by=user,
    )


@pytest.fixture
def draft_asset(user):
    return AssetFactory(
        name="Draft Item",
        status="draft",
        is_serialised=False,
        created_by=user,
        category=None,
        current_location=None,
    )


@pytest.fixture
def second_user(db, password):
    return UserFactory(
        username="borrower",
        email="borrower@example.com",
        password=password,
        display_name="Borrower Person",
    )


# --- Serialisation & Kit fixtures ---


@pytest.fixture
def serialised_asset(category, location, user):
    return AssetFactory(
        name="Wireless Mic Set",
        description="Set of wireless microphones",
        category=category,
        current_location=location,
        status="active",
        is_serialised=True,
        created_by=user,
    )


@pytest.fixture
def non_serialised_asset(category, location, user):
    return AssetFactory(
        name="Cable Bundle",
        description="Pack of XLR cables",
        category=category,
        current_location=location,
        status="active",
        is_serialised=False,
        quantity=10,
        created_by=user,
    )


@pytest.fixture
def asset_serial(serialised_asset, location):
    return AssetSerialFactory(
        asset=serialised_asset,
        serial_number="001",
        barcode=f"{serialised_asset.barcode}-S001",
        status="active",
        condition="good",
        current_location=location,
    )


@pytest.fixture
def kit_asset(category, location, user):
    return AssetFactory(
        name="Sound Kit",
        description="Complete sound kit",
        category=category,
        current_location=location,
        status="active",
        is_kit=True,
        created_by=user,
    )


@pytest.fixture
def kit_component(kit_asset, asset):
    return AssetKit.objects.create(
        kit=kit_asset,
        component=asset,
        quantity=1,
        is_required=True,
    )


# --- Hold list fixtures ---


@pytest.fixture
def hold_list_status(db):
    from assets.models import HoldListStatus

    status, _ = HoldListStatus.objects.get_or_create(
        name="Draft",
        defaults={"is_default": True, "sort_order": 10},
    )
    return status


@pytest.fixture
def hold_list(hold_list_status, department, admin_user):
    from assets.models import HoldList

    return HoldList.objects.create(
        name="Show Hold",
        department=department,
        status=hold_list_status,
        start_date="2026-03-01",
        end_date="2026-03-31",
        created_by=admin_user,
    )


@pytest.fixture
def active_hold_status(db):
    """Non-terminal hold list status."""
    from assets.models import HoldListStatus

    status, _ = HoldListStatus.objects.get_or_create(
        name="Confirmed",
        defaults={"is_default": False, "is_terminal": False, "sort_order": 20},
    )
    return status


@pytest.fixture
def terminal_hold_status(db):
    """Terminal hold list status."""
    from assets.models import HoldListStatus

    status, _ = HoldListStatus.objects.get_or_create(
        name="Fulfilled",
        defaults={"is_default": False, "is_terminal": True, "sort_order": 40},
    )
    return status


@pytest.fixture
def active_hold_list(active_hold_status, department, user):
    """An active (non-terminal) hold list."""
    from assets.models import HoldList

    return HoldList.objects.create(
        name="Show Hold List",
        status=active_hold_status,
        department=department,
        created_by=user,
        start_date="2026-01-01",
        end_date="2026-12-31",
    )


@pytest.fixture
def _seed_holdlist_statuses(db):
    """Seed hold list statuses for tests that need them."""
    from django.core.management import call_command

    call_command("seed_holdlist_statuses")


@pytest.fixture
def hl_active_status(db):
    """Non-terminal hold list status for VV tests."""
    from assets.models import HoldListStatus

    status, _ = HoldListStatus.objects.get_or_create(
        name="Draft",
        defaults={"is_default": True, "is_terminal": False, "sort_order": 10},
    )
    return status


@pytest.fixture
def hl_terminal_status(db):
    """Terminal hold list status for VV tests."""
    from assets.models import HoldListStatus

    status, _ = HoldListStatus.objects.get_or_create(
        name="Fulfilled",
        defaults={"is_default": False, "is_terminal": True, "sort_order": 40},
    )
    return status

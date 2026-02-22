"""Shared pytest fixtures and factories for PROPS tests."""

import pytest

from django.conf import settings
from django.contrib.auth.models import Group

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
    group, _ = Group.objects.get_or_create(name="Member")
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
    group, _ = Group.objects.get_or_create(name="Member")
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
    group, _ = Group.objects.get_or_create(name="Viewer")
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
    group, _ = Group.objects.get_or_create(name="Department Manager")
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

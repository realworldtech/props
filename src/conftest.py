"""Shared pytest fixtures and factories for PROPS tests."""

import pytest

from django.conf import settings
from django.contrib.auth import get_user_model
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

from assets.models import (  # noqa: E402
    Asset,
    AssetKit,
    AssetSerial,
    Category,
    Department,
    Location,
    Tag,
)

User = get_user_model()


# --- User fixtures ---


@pytest.fixture
def password():
    return "testpass123!"


@pytest.fixture
def user(db, password):
    group, _ = Group.objects.get_or_create(name="Member")
    u = User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password=password,
        display_name="Test User",
    )
    u.groups.add(group)
    return u


@pytest.fixture
def admin_user(db, password):
    return User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password=password,
    )


@pytest.fixture
def member_user(db, password):
    group, _ = Group.objects.get_or_create(name="Member")
    u = User.objects.create_user(
        username="member",
        email="member@example.com",
        password=password,
    )
    u.groups.add(group)
    return u


@pytest.fixture
def viewer_user(db, password):
    group, _ = Group.objects.get_or_create(name="Viewer")
    u = User.objects.create_user(
        username="viewer",
        email="viewer@example.com",
        password=password,
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
    u = User.objects.create_user(
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
    return Department.objects.create(
        name="Props",
        description="Props department",
    )


@pytest.fixture
def tag(db):
    return Tag.objects.create(name="fragile", color="red")


@pytest.fixture
def category(department):
    return Category.objects.create(
        name="Hand Props",
        description="Small hand-held items",
        department=department,
    )


@pytest.fixture
def location(db):
    return Location.objects.create(
        name="Main Store",
        address="123 Theatre St",
    )


@pytest.fixture
def child_location(location):
    return Location.objects.create(
        name="Shelf A",
        parent=location,
    )


@pytest.fixture
def asset(category, location, user):
    a = Asset(
        name="Test Prop",
        description="A test prop for testing",
        category=category,
        current_location=location,
        status="active",
        is_serialised=False,
        created_by=user,
    )
    a.save()
    return a


@pytest.fixture
def draft_asset(user):
    a = Asset(
        name="Draft Item",
        status="draft",
        is_serialised=False,
        created_by=user,
    )
    a.save()
    return a


@pytest.fixture
def second_user(db, password):
    return User.objects.create_user(
        username="borrower",
        email="borrower@example.com",
        password=password,
        display_name="Borrower Person",
    )


# --- Serialisation & Kit fixtures ---


@pytest.fixture
def serialised_asset(category, location, user):
    a = Asset(
        name="Wireless Mic Set",
        description="Set of wireless microphones",
        category=category,
        current_location=location,
        status="active",
        is_serialised=True,
        created_by=user,
    )
    a.save()
    return a


@pytest.fixture
def non_serialised_asset(category, location, user):
    a = Asset(
        name="Cable Bundle",
        description="Pack of XLR cables",
        category=category,
        current_location=location,
        status="active",
        is_serialised=False,
        quantity=10,
        created_by=user,
    )
    a.save()
    return a


@pytest.fixture
def asset_serial(serialised_asset, location):
    return AssetSerial.objects.create(
        asset=serialised_asset,
        serial_number="001",
        barcode=f"{serialised_asset.barcode}-S001",
        status="active",
        condition="good",
        current_location=location,
    )


@pytest.fixture
def kit_asset(category, location, user):
    a = Asset(
        name="Sound Kit",
        description="Complete sound kit",
        category=category,
        current_location=location,
        status="active",
        is_kit=True,
        created_by=user,
    )
    a.save()
    return a


@pytest.fixture
def kit_component(kit_asset, asset):
    return AssetKit.objects.create(
        kit=kit_asset,
        component=asset,
        quantity=1,
        is_required=True,
    )

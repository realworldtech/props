"""Shared fixtures for functional (user-capability) tests.

These fixtures build realistic data sets that represent common
pre-conditions described in the S11 usage scenarios.
"""

import pytest

from django.contrib.auth.models import Group

from assets.factories import (
    AssetFactory,
    AssetSerialFactory,
    CategoryFactory,
    DepartmentFactory,
    LocationFactory,
    UserFactory,
)
from assets.models import Asset, AssetKit, AssetSerial, Department, Location


@pytest.fixture
def props_dept(db):
    """A 'Props' department with barcode prefix PROP."""
    return DepartmentFactory(name="Props", barcode_prefix="PROP")


@pytest.fixture
def tech_dept(db):
    """A 'Technical' department with barcode prefix TECH."""
    return DepartmentFactory(name="Technical", barcode_prefix="TECH")


@pytest.fixture
def warehouse(db):
    """A warehouse location with child locations."""
    wh = LocationFactory(name="Warehouse", parent=None)
    bay1 = LocationFactory(name="Bay 1", parent=wh)
    bay4 = LocationFactory(name="Bay 4", parent=wh)
    shelf_a = LocationFactory(name="Shelf A", parent=bay4)
    shelf_b = LocationFactory(name="Shelf B", parent=bay4)
    return {
        "root": wh,
        "bay1": bay1,
        "bay4": bay4,
        "shelf_a": shelf_a,
        "shelf_b": shelf_b,
    }


@pytest.fixture
def borrower_user(db, password):
    """A user in the 'Borrower' group for checkout target tests."""
    group, _ = Group.objects.get_or_create(name="Borrower")
    u = UserFactory(
        username="borrower",
        email="borrower@example.com",
        password=password,
        display_name="Mel Smith",
    )
    u.groups.add(group)
    return u


@pytest.fixture
def active_asset(db, category, location, admin_user):
    """A single active asset ready for checkout/checkin/transfer."""
    return AssetFactory(
        name="Sound Desk",
        status="active",
        category=category,
        current_location=location,
        created_by=admin_user,
    )


@pytest.fixture
def draft_asset(db, category, admin_user):
    """A draft asset with a barcode but no location (typical quick-capture
    state).

    Overrides root conftest draft_asset — uses admin_user as creator
    (root version uses regular user).
    """
    return AssetFactory(
        name="Quick Capture Feb 22 14:32",
        status="draft",
        category=None,
        current_location=None,
        created_by=admin_user,
    )


@pytest.fixture
def serialised_asset_with_units(db, tech_dept, location, admin_user):
    """A serialised asset (wireless microphones) with 5 active serials."""
    category = CategoryFactory(name="Microphones", department=tech_dept)
    asset = AssetFactory(
        name="Wireless Microphone",
        status="active",
        is_serialised=True,
        category=category,
        current_location=location,
        created_by=admin_user,
    )
    serials = []
    for i in range(1, 6):
        s = AssetSerialFactory(
            asset=asset,
            serial_number=f"WM-00{i}",
            status="active",
        )
        serials.append(s)
    return {"asset": asset, "serials": serials}


@pytest.fixture
def kit_with_components(db, props_dept, location, admin_user):
    """An asset kit (lighting kit) with required and optional components."""
    category = CategoryFactory(name="Kits", department=props_dept)
    kit = AssetFactory(
        name="Lighting Kit",
        status="active",
        is_kit=True,
        category=category,
        current_location=location,
        created_by=admin_user,
    )
    dimmer = AssetFactory(
        name="Dimmer Pack",
        status="active",
        category=category,
        current_location=location,
        created_by=admin_user,
    )
    par_can = AssetFactory(
        name="PAR Can",
        status="active",
        category=category,
        current_location=location,
        created_by=admin_user,
    )
    AssetKit.objects.create(kit=kit, component=dimmer, is_required=True)
    AssetKit.objects.create(kit=kit, component=par_can, is_required=True)
    return {"kit": kit, "dimmer": dimmer, "par_can": par_can}

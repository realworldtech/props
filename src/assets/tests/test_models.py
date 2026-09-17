"""Tests for asset models and database layer."""

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
    AssetImage,
    AssetKit,
    AssetSerial,
    Category,
    Department,
    Location,
    NFCTag,
    SiteBranding,
    StocktakeItem,
    StocktakeSession,
    Tag,
    Transaction,
)
from assets.views import BARCODE_PATTERN

User = get_user_model()

# ============================================================
# MODEL TESTS
# ============================================================


class TestDepartment:
    def test_str(self, department):
        assert str(department) == "Props"

    def test_ordering(self, db):
        Department.objects.create(name="Zzz")
        Department.objects.create(name="Aaa")
        names = list(Department.objects.values_list("name", flat=True))
        assert names == sorted(names)


class TestTag:
    def test_str(self, tag):
        assert str(tag) == "fragile"

    def test_default_color(self, db):
        t = Tag.objects.create(name="test")
        assert t.color == "gray"


class TestCategory:
    def test_str(self, category):
        assert str(category) == "Hand Props"

    def test_unique_per_department(self, category, department):
        with pytest.raises(Exception):
            Category.objects.create(name="Hand Props", department=department)


class TestLocation:
    def test_str_is_full_path(self, location):
        assert str(location) == "Main Store"

    def test_full_path_with_parent(self, location, child_location):
        assert child_location.full_path == "Main Store > Shelf A"

    def test_circular_reference_prevented(self, location, child_location):
        location.parent = child_location
        with pytest.raises(ValidationError):
            location.clean()

    def test_max_depth_enforced(self, db):
        l1 = Location.objects.create(name="L1")
        l2 = Location.objects.create(name="L2", parent=l1)
        l3 = Location.objects.create(name="L3", parent=l2)
        l4 = Location.objects.create(name="L4", parent=l3)
        l5 = Location(name="L5", parent=l4)
        with pytest.raises(ValidationError, match="nesting depth"):
            l5.clean()

    def test_get_descendants(self, location, child_location):
        grandchild = Location.objects.create(
            name="Box 1", parent=child_location
        )
        descendants = location.get_descendants()
        assert child_location in descendants
        assert grandchild in descendants

    def test_get_descendants_returns_children(self, location, child_location):
        """Direct children are included in descendants."""
        descendants = location.get_descendants()
        assert child_location in descendants
        assert len(descendants) == 1

    def test_get_descendants_returns_grandchildren(
        self, location, child_location
    ):
        """3-level hierarchy returns children and grandchildren."""
        grandchild = Location.objects.create(
            name="Box 1", parent=child_location
        )
        descendants = location.get_descendants()
        assert child_location in descendants
        assert grandchild in descendants
        assert len(descendants) == 2

    def test_get_descendants_empty(self, location):
        """Leaf location with no children returns empty list."""
        descendants = location.get_descendants()
        assert descendants == []

    def test_get_descendants_no_recursive_queries(self, location, db):
        """Iterative approach uses at most depth+1 queries, not N+1."""
        child = Location.objects.create(name="Child", parent=location)
        Location.objects.create(name="GC1", parent=child)
        Location.objects.create(name="GC2", parent=child)
        # depth=2 hierarchy: should need ≤3 queries (one per level + final)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            location.get_descendants()
        # 3 levels to check: children, grandchildren, empty level = 3
        assert len(ctx) <= 4

    def test_get_absolute_url(self, location):
        url = location.get_absolute_url()
        assert f"/locations/{location.pk}/" in url


class TestAsset:
    def test_str(self, asset):
        assert asset.name in str(asset)
        assert asset.barcode in str(asset)

    def test_barcode_auto_generated(self, asset):
        assert asset.barcode
        assert asset.barcode.startswith("ASSET-")

    def test_barcode_unique(self, asset, category, location, user):
        a2 = Asset(
            name="Another",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        a2.save()
        assert a2.barcode != asset.barcode

    def test_valid_transitions(self, asset):
        assert asset.can_transition_to("retired")
        assert asset.can_transition_to("missing")
        assert asset.can_transition_to("disposed")
        assert not asset.can_transition_to("draft")

    def test_draft_transitions(self, draft_asset):
        assert draft_asset.can_transition_to("active")
        assert draft_asset.can_transition_to("disposed")
        assert not draft_asset.can_transition_to("retired")

    def test_disposed_no_transitions(self, asset):
        asset.status = "disposed"
        assert not asset.can_transition_to("active")
        assert not asset.can_transition_to("draft")

    def test_clean_non_draft_requires_category(self, db, location, user):
        a = Asset(
            name="No Category",
            current_location=location,
            status="active",
            created_by=user,
        )
        a.barcode = "TEST-NOCAT123"
        with pytest.raises(ValidationError, match="category"):
            a.clean()

    def test_clean_non_draft_requires_location(self, db, category, user):
        a = Asset(
            name="No Location",
            category=category,
            status="active",
            created_by=user,
        )
        a.barcode = "TEST-NOLOC123"
        with pytest.raises(ValidationError, match="current_location"):
            a.clean()

    def test_clean_draft_allows_missing_fields(self, draft_asset):
        draft_asset.clean()  # Should not raise

    def test_is_checked_out(self, asset, second_user):
        assert not asset.is_checked_out
        asset.checked_out_to = second_user
        assert asset.is_checked_out

    def test_department_property(self, asset, department):
        assert asset.department == department

    def test_department_property_none(self, draft_asset):
        assert draft_asset.department is None

    def test_primary_image(self, asset):
        assert asset.primary_image is None

    def test_active_nfc_tags_empty(self, asset):
        assert asset.active_nfc_tags.count() == 0

    def test_get_absolute_url(self, asset):
        assert f"/assets/{asset.pk}/" in asset.get_absolute_url()


class TestAssetImage:
    def test_first_image_becomes_primary(self, asset):
        from django.core.files.uploadedfile import SimpleUploadedFile

        img_file = SimpleUploadedFile(
            "test.jpg",
            b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        image = AssetImage.objects.create(asset=asset, image=img_file)
        assert image.is_primary

    def test_setting_primary_unsets_others(self, asset):
        from django.core.files.uploadedfile import SimpleUploadedFile

        img1 = SimpleUploadedFile(
            "test1.jpg",
            b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        img2 = SimpleUploadedFile(
            "test2.jpg",
            b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        i1 = AssetImage.objects.create(
            asset=asset, image=img1, is_primary=True
        )
        i2 = AssetImage.objects.create(
            asset=asset, image=img2, is_primary=True
        )
        i1.refresh_from_db()
        assert not i1.is_primary
        assert i2.is_primary


class TestAssetImageThumbnailUrl:
    """Unit tests for AssetImage.thumbnail_url property."""

    def test_returns_thumbnail_url_when_thumbnail_set(self, asset):
        """When thumbnail field is populated, thumbnail_url returns it."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        img_file = SimpleUploadedFile(
            "test.jpg",
            b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        thumb_file = SimpleUploadedFile(
            "thumb.jpg",
            b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        image = AssetImage.objects.create(
            asset=asset, image=img_file, thumbnail=thumb_file
        )
        assert image.thumbnail_url == image.thumbnail.url
        assert "thumb" in image.thumbnail_url

    def test_falls_back_to_image_url_when_no_thumbnail(self, asset):
        """When thumbnail is absent, thumbnail_url falls back to
        the full image URL."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        img_file = SimpleUploadedFile(
            "test.jpg",
            b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        image = AssetImage.objects.create(asset=asset, image=img_file)
        assert not image.thumbnail
        assert image.thumbnail_url == image.image.url

    def test_returns_empty_string_when_neither_set(self, asset):
        """When both thumbnail and image are empty,
        thumbnail_url returns empty string."""
        image = AssetImage(asset=asset)
        assert image.thumbnail_url == ""


class TestNFCTag:
    def test_str(self, asset, user):
        nfc = NFCTag.objects.create(
            tag_id="NFC-001", asset=asset, assigned_by=user
        )
        assert "NFC-001" in str(nfc)
        assert "active" in str(nfc)

    def test_is_active(self, asset, user):
        nfc = NFCTag.objects.create(
            tag_id="NFC-002", asset=asset, assigned_by=user
        )
        assert nfc.is_active

    def test_get_asset_by_tag(self, asset, user):
        NFCTag.objects.create(tag_id="NFC-003", asset=asset, assigned_by=user)
        found = NFCTag.get_asset_by_tag("NFC-003")
        assert found == asset

    def test_get_asset_by_tag_not_found(self, db):
        assert NFCTag.get_asset_by_tag("NONEXISTENT") is None

    def test_get_asset_by_tag_case_insensitive(self, asset, user):
        NFCTag.objects.create(tag_id="NFC-CASE", asset=asset, assigned_by=user)
        assert NFCTag.get_asset_by_tag("nfc-case") == asset

    def test_unique_active_constraint(self, asset, user):
        NFCTag.objects.create(
            tag_id="NFC-UNIQUE", asset=asset, assigned_by=user
        )
        with pytest.raises(Exception):
            NFCTag.objects.create(
                tag_id="NFC-UNIQUE", asset=asset, assigned_by=user
            )


class TestTransaction:
    def test_str(self, asset, user):
        txn = Transaction.objects.create(
            asset=asset, user=user, action="checkout"
        )
        assert asset.name in str(txn)
        assert "Check Out" in str(txn)


class TestStocktakeSession:
    def test_str(self, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        assert location.name in str(session)

    def test_expected_assets(self, asset, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        expected = session.expected_assets
        assert asset in expected

    def test_missing_assets(self, asset, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        missing = session.missing_assets
        assert asset in missing

    def test_confirmed_reduces_missing(self, asset, location, user):
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        session.confirmed_assets.add(asset)
        assert asset not in session.missing_assets

    def test_unexpected_assets(self, asset, location, user):
        other_loc = Location.objects.create(name="Other Place")
        session = StocktakeSession.objects.create(
            location=other_loc, started_by=user
        )
        session.confirmed_assets.add(asset)
        unexpected = session.unexpected_assets
        assert asset in unexpected


# ============================================================
# ASSET KITS & SERIALISATION TESTS (F2)
# ============================================================


class TestAssetNewFields:
    """Test is_serialised and is_kit defaults on Asset."""

    def test_is_serialised_default_false(self, category, location, user):
        """S3a: new assets default to is_serialised=False."""
        a = Asset(
            name="Default Check",
            category=category,
            current_location=location,
            status="active",
            created_by=user,
        )
        a.save()
        assert a.is_serialised is False

    def test_is_kit_default_false(self, asset):
        assert asset.is_kit is False

    def test_serialised_asset_flag(self, serialised_asset):
        assert serialised_asset.is_serialised is True

    def test_kit_asset_flag(self, kit_asset):
        assert kit_asset.is_kit is True


class TestTransactionNewFields:
    """Test new Transaction fields."""

    def test_quantity_default(self, asset, user):
        txn = Transaction.objects.create(
            asset=asset, user=user, action="checkout"
        )
        assert txn.quantity == 1

    def test_serial_fk_nullable(self, asset, user):
        txn = Transaction.objects.create(
            asset=asset, user=user, action="checkout"
        )
        assert txn.serial is None

    def test_serial_barcode_nullable(self, asset, user):
        txn = Transaction.objects.create(
            asset=asset, user=user, action="checkout"
        )
        assert txn.serial_barcode is None

    def test_kit_return_action(self, asset, user):
        txn = Transaction.objects.create(
            asset=asset, user=user, action="kit_return"
        )
        assert txn.get_action_display() == "Kit Return"

    def test_transaction_with_serial(
        self, serialised_asset, asset_serial, user
    ):
        txn = Transaction.objects.create(
            asset=serialised_asset,
            user=user,
            action="checkout",
            serial=asset_serial,
            serial_barcode=asset_serial.barcode,
        )
        assert txn.serial == asset_serial
        assert txn.serial_barcode == asset_serial.barcode


class TestAssetSerialModel:
    """Test AssetSerial model."""

    def test_creation(self, serialised_asset, location):
        serial = AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="002",
            barcode="TEST-SERIAL-002",
            current_location=location,
        )
        assert serial.status == "active"
        assert serial.condition == "good"
        assert serial.is_archived is False

    def test_str(self, asset_serial, serialised_asset):
        expected = f"{serialised_asset.name} #001"
        assert str(asset_serial) == expected

    def test_unique_serial_per_asset(self, serialised_asset, asset_serial):
        with pytest.raises(Exception):
            AssetSerial.objects.create(
                asset=serialised_asset,
                serial_number="001",
                barcode="TEST-DIFFERENT",
            )

    def test_unique_barcode(self, serialised_asset, asset_serial):
        with pytest.raises(Exception):
            AssetSerial.objects.create(
                asset=serialised_asset,
                serial_number="099",
                barcode=asset_serial.barcode,
            )

    def test_cross_table_barcode_validation(self, serialised_asset, asset):
        serial = AssetSerial(
            asset=serialised_asset,
            serial_number="099",
            barcode=asset.barcode,
        )
        with pytest.raises(ValidationError, match="already in use"):
            serial.clean()

    def test_clean_non_serialised_parent(self, asset, location):
        serial = AssetSerial(
            asset=asset,
            serial_number="001",
            barcode="TEST-BAD-SERIAL",
        )
        with pytest.raises(ValidationError, match="non-serialised"):
            serial.clean()

    def test_draft_status_rejected(self, serialised_asset):
        serial = AssetSerial(
            asset=serialised_asset,
            serial_number="099",
            status="draft",
        )
        with pytest.raises(ValidationError, match="draft"):
            serial.clean()

    def test_status_choices(self, db):
        choices = dict(AssetSerial.STATUS_CHOICES)
        assert "active" in choices
        assert "retired" in choices
        assert "missing" in choices
        assert "lost" in choices
        assert "stolen" in choices
        assert "disposed" in choices
        assert "draft" not in choices


class TestAssetKitModel:
    """Test AssetKit model."""

    def test_creation(self, kit_component, kit_asset, asset):
        assert kit_component.kit == kit_asset
        assert kit_component.component == asset
        assert kit_component.quantity == 1
        assert kit_component.is_required is True

    def test_str(self, kit_component, kit_asset, asset):
        expected = f"{kit_asset.name} -> {asset.name}"
        assert str(kit_component) == expected

    def test_unique_kit_component(self, kit_component, kit_asset, asset):
        with pytest.raises(Exception):
            AssetKit.objects.create(
                kit=kit_asset,
                component=asset,
            )

    def test_clean_kit_must_be_kit(self, asset, category, location, user):
        non_kit = Asset(
            name="Not A Kit",
            category=category,
            current_location=location,
            status="active",
            is_kit=False,
            created_by=user,
        )
        non_kit.save()
        ak = AssetKit(kit=non_kit, component=asset)
        with pytest.raises(ValidationError, match="is_kit"):
            ak.clean()

    def test_no_self_reference(self, kit_asset):
        ak = AssetKit(kit=kit_asset, component=kit_asset)
        with pytest.raises(ValidationError, match="itself"):
            ak.clean()

    def test_circular_reference(self, category, location, user):
        kit_a = Asset(
            name="Kit A",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            created_by=user,
        )
        kit_a.save()
        kit_b = Asset(
            name="Kit B",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            created_by=user,
        )
        kit_b.save()

        # A contains B
        AssetKit.objects.create(kit=kit_a, component=kit_b)
        # B contains A -> circular
        ak = AssetKit(kit=kit_b, component=kit_a)
        with pytest.raises(ValidationError, match="Circular"):
            ak.clean()

    def test_serial_must_belong_to_component(
        self, kit_asset, serialised_asset, asset_serial, asset
    ):
        # asset_serial belongs to serialised_asset, not to asset
        ak = AssetKit(
            kit=kit_asset,
            component=asset,
            serial=asset_serial,
        )
        with pytest.raises(ValidationError, match="component"):
            ak.clean()


class TestDerivedFields:
    """Test derived properties on Asset for serialised assets."""

    def test_effective_quantity_serialised_no_serials(self, serialised_asset):
        assert serialised_asset.effective_quantity == 0

    def test_effective_quantity_serialised_with_serials(
        self, serialised_asset, location
    ):
        for i in range(3):
            AssetSerial.objects.create(
                asset=serialised_asset,
                serial_number=f"EQ-{i}",
                barcode=f"EQ-SERIAL-{i}",
                current_location=location,
            )
        assert serialised_asset.effective_quantity == 3

    def test_effective_quantity_excludes_disposed(
        self, serialised_asset, location
    ):
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="EQ-A",
            barcode="EQ-A-BC",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="EQ-B",
            barcode="EQ-B-BC",
            status="disposed",
            current_location=location,
        )
        assert serialised_asset.effective_quantity == 1

    def test_effective_quantity_non_serialised(self, non_serialised_asset):
        assert non_serialised_asset.effective_quantity == 10

    def test_derived_status_non_serialised(self, asset):
        assert asset.derived_status == "active"

    def test_derived_status_serialised_active(
        self, serialised_asset, location
    ):
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="DS-1",
            barcode="DS-1-BC",
            status="active",
            current_location=location,
        )
        assert serialised_asset.derived_status == "active"

    def test_derived_status_serialised_missing_priority(
        self, serialised_asset, location
    ):
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="DS-2",
            barcode="DS-2-BC",
            status="retired",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="DS-3",
            barcode="DS-3-BC",
            status="missing",
            current_location=location,
        )
        # Missing should take priority over retired
        assert serialised_asset.derived_status == "missing"

    def test_derived_status_no_serials_falls_back(self, serialised_asset):
        assert serialised_asset.derived_status == "active"

    def test_condition_summary_non_serialised(self, asset):
        assert asset.condition_summary == "good"

    def test_condition_summary_serialised(self, serialised_asset, location):
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="CS-1",
            barcode="CS-1-BC",
            condition="good",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="CS-2",
            barcode="CS-2-BC",
            condition="good",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="CS-3",
            barcode="CS-3-BC",
            condition="fair",
            current_location=location,
        )
        summary = serialised_asset.condition_summary
        assert summary["good"] == 2
        assert summary["fair"] == 1

    def test_available_count_serialised(
        self, serialised_asset, location, second_user
    ):
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="AC-1",
            barcode="AC-1-BC",
            status="active",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="AC-2",
            barcode="AC-2-BC",
            status="active",
            checked_out_to=second_user,
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="AC-3",
            barcode="AC-3-BC",
            status="retired",
            current_location=location,
        )
        assert serialised_asset.available_count == 1

    def test_is_checked_out_serialised(
        self, serialised_asset, location, second_user
    ):
        assert not serialised_asset.is_checked_out
        AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="CO-1",
            barcode="CO-1-BC",
            status="active",
            checked_out_to=second_user,
            current_location=location,
        )
        assert serialised_asset.is_checked_out

    def test_is_checked_out_non_serialised(self, asset, second_user):
        assert not asset.is_checked_out
        asset.checked_out_to = second_user
        assert asset.is_checked_out


# ============================================================
# BATCH E: SHOULD-IMPLEMENT QUICK WINS
# ============================================================


class TestDepartmentBarcodePrefix:
    """V10: Department barcode prefix on asset generation."""

    def test_department_has_barcode_prefix_field(self, department):
        assert hasattr(department, "barcode_prefix")

    def test_asset_uses_department_prefix(self, user, location, db):
        dept = Department.objects.create(name="Sound", barcode_prefix="SND")
        cat = Category.objects.create(name="Microphones", department=dept)
        a = Asset(
            name="SM58",
            category=cat,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=user,
        )
        a.save()
        assert a.barcode.startswith("SND-")

    def test_asset_falls_back_to_global_prefix(self, asset):
        # asset fixture has department without barcode_prefix
        assert asset.barcode.startswith("ASSET-")


# ============================================================
# G5, L4, L5, L6, L7, L8, L12 — MODEL FIELD CHANGES
# ============================================================


@pytest.mark.django_db
class TestAssetPublicFields:
    """G5: is_public and public_description fields on Asset."""

    def test_is_public_defaults_false(self, asset):
        assert asset.is_public is False

    def test_public_description_nullable(self, asset):
        assert asset.public_description is None
        asset.public_description = "Visible to the public"
        asset.save()
        asset.refresh_from_db()
        assert asset.public_description == "Visible to the public"


@pytest.mark.django_db
class TestDepartmentBarcodePrefixLength:
    """L6: barcode_prefix max_length increased to 20."""

    def test_accepts_20_char_prefix(self, db):
        dept = Department.objects.create(
            name="Long Prefix Dept",
            barcode_prefix="A" * 20,
        )
        dept.refresh_from_db()
        assert len(dept.barcode_prefix) == 20


@pytest.mark.django_db
class TestTransactionDueDateDatetime:
    """L7: due_date changed from DateField to DateTimeField."""

    def test_due_date_accepts_datetime(self, asset, user):
        from django.utils import timezone

        now = timezone.now()
        txn = Transaction.objects.create(
            asset=asset,
            action="checkout",
            user=user,
            due_date=now,
        )
        txn.refresh_from_db()
        assert txn.due_date is not None
        assert txn.due_date.hour == now.hour


@pytest.mark.django_db
class TestSiteBrandingColorMode:
    """L8: color_mode default changed to 'system'."""

    def test_color_mode_default_is_system(self, db):
        branding = SiteBranding.objects.create()
        assert branding.color_mode == "system"


class TestBarcodePatternCaseInsensitive:
    """L12: BARCODE_PATTERN matches lowercase input."""

    def test_lowercase_barcode_matches(self):
        assert BARCODE_PATTERN.match("props-abc123")

    def test_uppercase_barcode_still_matches(self):
        assert BARCODE_PATTERN.match("PROPS-ABC123")

    def test_mixed_case_barcode_matches(self):
        assert BARCODE_PATTERN.match("Props-Abc123")


@pytest.mark.django_db
class TestTopLevelLocationUnique:
    """L5: top-level locations must have unique names."""

    def test_duplicate_top_level_names_fail(self, db):
        Location.objects.create(name="Warehouse")
        with pytest.raises(IntegrityError):
            Location.objects.create(name="Warehouse")

    def test_sub_locations_same_name_different_parents_ok(self, db):
        parent_a = Location.objects.create(name="Building A")
        parent_b = Location.objects.create(name="Building B")
        Location.objects.create(name="Room 1", parent=parent_a)
        Location.objects.create(name="Room 1", parent=parent_b)


# ============================================================
# STOCKTAKE ITEM MODEL TESTS (G9 — S3.1.9, M6, M7)
# ============================================================


@pytest.mark.django_db
class TestStocktakeItemModel:
    """G9/M6/M7: StocktakeItem model, expected snapshot, missing txns."""

    def test_stocktake_item_creation(self, asset, location, user):
        """Can create a StocktakeItem linked to a session and asset."""
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        item = StocktakeItem.objects.create(
            session=session, asset=asset, status="expected"
        )
        assert item.pk is not None
        assert item.session == session
        assert item.asset == asset
        assert item.status == "expected"

    def test_stocktake_item_with_serial(
        self, serialised_asset, asset_serial, location, user
    ):
        """Can create a StocktakeItem with a serial reference."""
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        item = StocktakeItem.objects.create(
            session=session,
            asset=serialised_asset,
            serial=asset_serial,
            status="expected",
        )
        assert item.serial == asset_serial

    def test_stocktake_item_scanned_by_tracked(self, asset, location, user):
        """The scanned_by user is recorded."""
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        item = StocktakeItem.objects.create(
            session=session,
            asset=asset,
            status="confirmed",
            scanned_by=user,
        )
        assert item.scanned_by == user

    def test_stocktake_item_notes(self, asset, location, user):
        """Notes field works."""
        session = StocktakeSession.objects.create(
            location=location, started_by=user
        )
        item = StocktakeItem.objects.create(
            session=session,
            asset=asset,
            status="expected",
            notes="Found on top shelf",
        )
        assert item.notes == "Found on top shelf"

    def test_expected_snapshot_created_on_start(
        self, admin_client, asset, location
    ):
        """M6: Starting a stocktake creates StocktakeItem records
        for expected assets with status='expected'."""
        response = admin_client.post(
            reverse("assets:stocktake_start"),
            {"location": location.pk},
        )
        assert response.status_code == 302
        session = StocktakeSession.objects.get(location=location)
        items = StocktakeItem.objects.filter(
            session=session, status="expected"
        )
        assert items.count() >= 1
        assert items.filter(asset=asset).exists()

    def test_confirm_updates_stocktake_item(
        self, admin_client, asset, location, admin_user
    ):
        """Confirming an asset updates its StocktakeItem status
        to 'confirmed'."""
        session = StocktakeSession.objects.create(
            location=location, started_by=admin_user
        )
        StocktakeItem.objects.create(
            session=session, asset=asset, status="expected"
        )
        admin_client.post(
            reverse("assets:stocktake_confirm", args=[session.pk]),
            {"asset_id": asset.pk},
        )
        item = StocktakeItem.objects.get(session=session, asset=asset)
        assert item.status == "confirmed"
        assert item.scanned_by == admin_user

    def test_unexpected_asset_creates_stocktake_item(
        self, admin_client, asset, location, admin_user, category, user
    ):
        """Confirming an asset not in expected creates a StocktakeItem
        with status='unexpected'."""
        other_loc = Location.objects.create(name="Other Place")
        session = StocktakeSession.objects.create(
            location=other_loc, started_by=admin_user
        )
        # No expected items at other_loc for this asset
        admin_client.post(
            reverse("assets:stocktake_confirm", args=[session.pk]),
            {"asset_id": asset.pk},
        )
        item = StocktakeItem.objects.get(session=session, asset=asset)
        assert item.status == "unexpected"

    def test_complete_marks_missing_with_transaction(
        self, admin_client, asset, location, admin_user
    ):
        """M7: Completing stocktake with mark_missing creates
        Transaction records per missing asset."""
        session = StocktakeSession.objects.create(
            location=location, started_by=admin_user
        )
        StocktakeItem.objects.create(
            session=session, asset=asset, status="expected"
        )
        # Don't confirm — asset should be marked missing
        admin_client.post(
            reverse("assets:stocktake_complete", args=[session.pk]),
            {"action": "complete", "mark_missing": "1"},
        )
        # Check StocktakeItem updated to missing
        item = StocktakeItem.objects.get(session=session, asset=asset)
        assert item.status == "missing"
        # Check Transaction created for missing asset
        txn = (
            Transaction.objects.filter(asset=asset, action="audit")
            .order_by("-timestamp")
            .first()
        )
        assert txn is not None
        assert "missing" in txn.notes.lower()

    def test_stocktake_summary_uses_items(
        self, admin_client, asset, location, admin_user
    ):
        """Summary view uses StocktakeItem data for counts."""
        session = StocktakeSession.objects.create(
            location=location,
            started_by=admin_user,
            status="completed",
        )
        StocktakeItem.objects.create(
            session=session, asset=asset, status="confirmed"
        )
        response = admin_client.get(
            reverse("assets:stocktake_summary", args=[session.pk])
        )
        assert response.status_code == 200
        ctx = response.context
        assert ctx["confirmed_count"] >= 1


@pytest.mark.django_db
class TestNullFieldEdgeCases:
    """S7.3 — Null safety edge cases."""

    def test_vv707_null_location_in_list_view_shows_unknown(
        self, admin_client, user
    ):
        """VV707: Active asset with null current_location should
        display 'Unknown' in list view, not crash or show blank."""
        asset = AssetFactory(
            name="Orphan Active",
            status="active",
            category=CategoryFactory(),
            current_location=None,
            created_by=user,
        )
        Asset.objects.filter(pk=asset.pk).update(current_location=None)

        response = admin_client.get(
            reverse("assets:asset_list") + "?status=active"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Unknown" in content or "unknown" in content, (
            "S7.3.1: Active asset with null current_location must "
            "display 'Unknown' in list view, not a blank or "
            "'None'. Current implementation shows blank for null "
            "locations."
        )

    def test_vv707_null_location_in_export_shows_unknown(
        self, admin_client, user
    ):
        """VV707: Active asset with null current_location should
        show 'Unknown' in Excel export, not blank."""
        from assets.services.export import export_assets_xlsx

        asset = AssetFactory(
            name="Orphan Export",
            status="active",
            category=CategoryFactory(),
            current_location=None,
            created_by=user,
        )
        Asset.objects.filter(pk=asset.pk).update(current_location=None)

        qs = Asset.objects.select_related(
            "category",
            "category__department",
            "current_location",
            "checked_out_to",
            "created_by",
        ).prefetch_related("tags")
        buf = export_assets_xlsx(queryset=qs)

        import openpyxl

        wb = openpyxl.load_workbook(buf)
        ws = wb["Assets"]
        location_value = None
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] == "Orphan Export":
                location_value = row[5]
                break
        assert location_value and "Unknown" in str(location_value), (
            "S7.3.1: Export must show 'Unknown' for active assets "
            "with null current_location, not blank. Currently the "
            "location_display is empty when both checked_out_to "
            "and current_location are null."
        )

    def test_vv708_null_category_in_search_results(self, admin_client, user):
        """VV708: Draft asset with null category should display
        'Unassigned' in search results, not blank or crash."""
        AssetFactory(
            name="Uncategorised Draft",
            status="draft",
            category=None,
            current_location=None,
            created_by=user,
        )
        response = admin_client.get(
            reverse("assets:asset_list") + "?status=draft&q=Uncategorised"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert (
            "Unassigned" in content
            or "No category" in content
            or "unassigned" in content
        ), (
            "S7.3.2: Draft asset with null category must display "
            "'Unassigned' or 'No category' in search results. "
            "Current template shows blank for null category."
        )


# ============================================================
# BATCH 6: ZERO-COVERAGE DATA MODEL AND EDGE CASE TESTS
# ============================================================


@pytest.mark.django_db
class TestProjectDateRangeModel:
    """V551 (S3.1.12): ProjectDateRange model exists and has correct fields."""

    def test_project_date_range_model_exists(self):
        """ProjectDateRange model exists."""
        from assets.models import ProjectDateRange

        assert ProjectDateRange is not None

    def test_project_date_range_has_required_fields(self):
        """ProjectDateRange has start_date and end_date fields."""
        from assets.models import ProjectDateRange

        field_names = [f.name for f in ProjectDateRange._meta.get_fields()]
        assert "start_date" in field_names
        assert "end_date" in field_names

    def test_project_date_range_can_be_created(self, db):
        """ProjectDateRange instances can be created."""
        from datetime import date

        from assets.models import Project, ProjectDateRange

        project = Project.objects.create(
            name="Test Project",
        )
        pdr = ProjectDateRange.objects.create(
            project=project,
            label="Rehearsal Week",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        assert pdr.pk is not None
        assert str(pdr) != ""


@pytest.mark.django_db
class TestV49StatusFieldMigration:
    """V49 (S2.2.3-06, MUST): Migration from is_draft to status field."""

    def test_draft_asset_uses_status_field(self, draft_asset):
        """Draft assets should use status='draft', not is_draft boolean."""
        assert draft_asset.status == "draft"
        # Verify is_draft field doesn't exist
        assert not hasattr(draft_asset, "is_draft")

    def test_active_asset_uses_status_field(self, asset):
        """Active assets should use status='active'."""
        assert asset.status == "active"
        assert not hasattr(asset, "is_draft")

    def test_asset_status_choices_defined(self):
        """Asset model should have STATUS_CHOICES defined."""
        assert hasattr(Asset, "STATUS_CHOICES")
        assert len(Asset.STATUS_CHOICES) > 0
        status_values = [choice[0] for choice in Asset.STATUS_CHOICES]
        assert "draft" in status_values
        assert "active" in status_values


# ============================================================
# FACTORY BOY ADOPTION TESTS (G12 — S8.6.1-03)
# ============================================================


@pytest.mark.django_db
class TestFactories:
    """Verify Factory Boy factories produce valid model instances."""

    def test_user_factory(self):
        """UserFactory creates a valid user with hashed password."""
        from assets.factories import UserFactory

        user = UserFactory()
        assert user.pk is not None
        assert user.username.startswith("user")
        assert "@example.com" in user.email
        assert user.check_password("testpass123!")
        assert user.is_active

    def test_department_factory(self):
        """DepartmentFactory creates a valid department."""
        from assets.factories import DepartmentFactory

        dept = DepartmentFactory()
        assert dept.pk is not None
        assert dept.name.startswith("Department")
        assert dept.description  # Faker sentence is non-empty

    def test_asset_factory(self):
        """AssetFactory creates a valid asset with related objects."""
        from assets.factories import AssetFactory

        asset = AssetFactory()
        assert asset.pk is not None
        assert asset.barcode  # auto-generated by save()
        assert asset.category is not None
        assert asset.current_location is not None
        assert asset.created_by is not None
        assert asset.status == "active"

    def test_asset_serial_factory(self):
        """AssetSerialFactory creates valid serial for serialised asset."""
        from assets.factories import AssetSerialFactory

        serial = AssetSerialFactory()
        assert serial.pk is not None
        assert serial.asset.is_serialised is True
        assert serial.barcode is not None
        assert serial.status == "active"
        assert serial.current_location is not None

    def test_hold_list_factory(self):
        """HoldListFactory creates a valid hold list with status."""
        from assets.factories import HoldListFactory

        hl = HoldListFactory()
        assert hl.pk is not None
        assert hl.department is not None
        assert hl.status is not None
        assert hl.created_by is not None

    def test_transaction_factory(self):
        """TransactionFactory creates a valid transaction."""
        from assets.factories import TransactionFactory

        tx = TransactionFactory()
        assert tx.pk is not None
        assert tx.action == "checkout"
        assert tx.asset is not None
        assert tx.user is not None

    def test_factory_sequences_unique(self):
        """Creating 10 assets produces unique names and barcodes."""
        from assets.factories import AssetFactory

        assets = AssetFactory.create_batch(10)
        names = [a.name for a in assets]
        barcodes = [a.barcode for a in assets]
        assert len(set(names)) == 10
        assert len(set(barcodes)) == 10

    def test_virtual_barcode_factory(self):
        """VirtualBarcodeFactory creates a valid record."""
        from assets.factories import VirtualBarcodeFactory

        vb = VirtualBarcodeFactory()
        assert vb.pk is not None
        assert vb.barcode.startswith("VIRT-")
        assert vb.created_by is not None
        assert vb.assigned_to_asset is None

    def test_tag_factory(self):
        """TagFactory creates a valid tag."""
        from assets.factories import TagFactory

        tag = TagFactory()
        assert tag.pk is not None
        assert tag.name.startswith("tag-")

    def test_category_factory(self):
        """CategoryFactory creates a valid category with department."""
        from assets.factories import CategoryFactory

        cat = CategoryFactory()
        assert cat.pk is not None
        assert cat.department is not None

    def test_location_factory(self):
        """LocationFactory creates a valid location."""
        from assets.factories import LocationFactory

        loc = LocationFactory()
        assert loc.pk is not None
        assert loc.name.startswith("Location")

    def test_nfc_tag_factory(self):
        """NFCTagFactory creates a valid NFC tag."""
        from assets.factories import NFCTagFactory

        nfc = NFCTagFactory()
        assert nfc.pk is not None
        assert nfc.tag_id.startswith("NFC-")
        assert nfc.asset is not None
        assert nfc.assigned_by is not None

    def test_asset_kit_factory(self):
        """AssetKitFactory creates a kit-component relationship."""
        from assets.factories import AssetKitFactory

        kit_link = AssetKitFactory()
        assert kit_link.pk is not None
        assert kit_link.kit.is_kit is True
        assert kit_link.component is not None
        assert kit_link.kit.pk != kit_link.component.pk

    def test_stocktake_session_factory(self):
        """StocktakeSessionFactory creates a valid session."""
        from assets.factories import StocktakeSessionFactory

        session = StocktakeSessionFactory()
        assert session.pk is not None
        assert session.location is not None
        assert session.started_by is not None
        assert session.status == "in_progress"

    def test_stocktake_item_factory(self):
        """StocktakeItemFactory creates a valid item."""
        from assets.factories import StocktakeItemFactory

        item = StocktakeItemFactory()
        assert item.pk is not None
        assert item.session is not None
        assert item.asset is not None
        assert item.status == "expected"

    def test_hold_list_status_factory(self):
        """HoldListStatusFactory creates a valid status."""
        from assets.factories import HoldListStatusFactory

        status = HoldListStatusFactory()
        assert status.pk is not None
        assert status.name.startswith("Status")

    def test_hold_list_item_factory(self):
        """HoldListItemFactory creates a valid hold list item."""
        from assets.factories import HoldListItemFactory

        item = HoldListItemFactory()
        assert item.pk is not None
        assert item.hold_list is not None
        assert item.asset is not None

    def test_project_factory(self):
        """ProjectFactory creates a valid project."""
        from assets.factories import ProjectFactory

        project = ProjectFactory()
        assert project.pk is not None
        assert project.name.startswith("Project")
        assert project.created_by is not None

    def test_site_branding_factory(self):
        """SiteBrandingFactory creates a valid branding instance."""
        from assets.factories import SiteBrandingFactory

        branding = SiteBrandingFactory()
        assert branding.pk is not None
        assert branding.primary_color == "#4F46E5"

    def test_asset_image_factory(self):
        """AssetImageFactory creates a valid image."""
        from assets.factories import AssetImageFactory

        img = AssetImageFactory()
        assert img.pk is not None
        assert img.asset is not None
        assert img.image is not None


@pytest.mark.django_db
class TestV707NullLocationDisplay:
    """V707: Null current_location in list views shows 'Unknown'."""

    def test_list_shows_unknown_for_null_location(
        self, admin_client, admin_user, asset
    ):
        """Active asset with null location should show 'Unknown'."""
        asset.current_location = None
        asset.status = "active"
        asset.save()
        response = admin_client.get(reverse("assets:asset_list"))
        content = response.content.decode()
        assert "Unknown" in content


# ============================================================
# VERIFICATION GAPS V708, V792, V557, V323, V422
# ============================================================


@pytest.mark.django_db
class TestV708NullCategoryUnassigned:
    """V708 (S7.3.2): Null category shows 'Unassigned' in asset list."""

    def test_list_shows_unassigned_for_null_category(
        self, admin_client, admin_user, location
    ):
        """Asset with category=None should show 'Unassigned' in list."""
        a = Asset(
            name="No Category Asset V708",
            category=None,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        a.save()
        response = admin_client.get(reverse("assets:asset_list"))
        content = response.content.decode()
        assert "Unassigned" in content

    def test_detail_shows_fallback_for_null_category(
        self, admin_client, admin_user, location
    ):
        """Asset detail with null category shows a fallback string."""
        a = Asset(
            name="No Category Detail V708",
            category=None,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        a.save()
        response = admin_client.get(
            reverse("assets:asset_detail", args=[a.pk])
        )
        content = response.content.decode()
        # Detail page uses "Not set" or "Unassigned"
        assert "Not set" in content or "Unassigned" in content


@pytest.mark.django_db
class TestV557DatabaseIndexes:
    """V557 (S3.1.18): Recommended database indexes exist."""

    def test_asset_serial_current_location_indexed(self):
        """AssetSerial.current_location should have a db index."""
        from django.apps import apps

        model = apps.get_model("assets", "AssetSerial")
        meta = model._meta
        # Check Meta.indexes for current_location
        index_field_names = set()
        for idx in meta.indexes:
            for field in idx.fields:
                index_field_names.add(field)
        # Also check db_index on the field
        field = meta.get_field("current_location")
        has_field_index = field.db_index
        has_meta_index = (
            "current_location" in index_field_names
            or "current_location_id" in index_field_names
        )
        assert (
            has_field_index or has_meta_index
        ), "AssetSerial.current_location should be indexed"

    def test_transaction_asset_indexed(self):
        """Transaction.asset should have a db index."""
        from django.apps import apps

        model = apps.get_model("assets", "Transaction")
        meta = model._meta
        index_field_names = set()
        for idx in meta.indexes:
            for field in idx.fields:
                index_field_names.add(field)
        field = meta.get_field("asset")
        has_field_index = field.db_index
        has_meta_index = (
            "asset" in index_field_names or "asset_id" in index_field_names
        )
        assert (
            has_field_index or has_meta_index
        ), "Transaction.asset should be indexed"

    def test_transaction_borrower_indexed(self):
        """Transaction.borrower should have a db index."""
        from django.apps import apps

        model = apps.get_model("assets", "Transaction")
        meta = model._meta
        index_field_names = set()
        for idx in meta.indexes:
            for field in idx.fields:
                index_field_names.add(field)
        field = meta.get_field("borrower")
        has_field_index = field.db_index
        has_meta_index = (
            "borrower" in index_field_names
            or "borrower_id" in index_field_names
        )
        assert (
            has_field_index or has_meta_index
        ), "Transaction.borrower should be indexed"

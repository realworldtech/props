"""Factory Boy factories for PROPS test data generation."""

from datetime import datetime

import factory
from factory.django import DjangoModelFactory

from django.utils import timezone


class UserFactory(DjangoModelFactory):
    """Factory for CustomUser model."""

    class Meta:
        model = "accounts.CustomUser"
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
    display_name = factory.Faker("name")
    is_active = True

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        pwd = extracted or "testpass123!"
        self.set_password(pwd)
        if create:
            self.save(update_fields=["password"])


class DepartmentFactory(DjangoModelFactory):
    """Factory for Department model."""

    class Meta:
        model = "assets.Department"

    name = factory.Sequence(lambda n: f"Department {n}")
    description = factory.Faker("sentence")


class TagFactory(DjangoModelFactory):
    """Factory for Tag model."""

    class Meta:
        model = "assets.Tag"

    name = factory.Sequence(lambda n: f"tag-{n}")
    color = "gray"


class CategoryFactory(DjangoModelFactory):
    """Factory for Category model."""

    class Meta:
        model = "assets.Category"

    name = factory.Sequence(lambda n: f"Category {n}")
    department = factory.SubFactory(DepartmentFactory)


class LocationFactory(DjangoModelFactory):
    """Factory for Location model."""

    class Meta:
        model = "assets.Location"

    name = factory.Sequence(lambda n: f"Location {n}")
    address = factory.Faker("address")


class AssetFactory(DjangoModelFactory):
    """Factory for Asset model.

    Does NOT set barcode — Asset.save() auto-generates it.
    Sets created_at to a date in the past (2025-01-01) so that
    backdating tests using dates like 2026-01-15 work correctly
    with S7.21.2 pre-creation date validation.
    """

    class Meta:
        model = "assets.Asset"
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"Asset {n}")
    category = factory.SubFactory(CategoryFactory)
    current_location = factory.SubFactory(LocationFactory)
    status = "active"
    is_serialised = False
    created_by = factory.SubFactory(UserFactory)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        obj = super()._create(model_class, *args, **kwargs)
        # Backdate created_at so S7.21.2 pre-creation
        # validation doesn't block legitimate backdating tests
        model_class.objects.filter(pk=obj.pk).update(
            created_at=timezone.make_aware(datetime(2025, 1, 1, 0, 0, 0))
        )
        obj.refresh_from_db()
        return obj


class AssetSerialFactory(DjangoModelFactory):
    """Factory for AssetSerial model."""

    class Meta:
        model = "assets.AssetSerial"

    asset = factory.SubFactory(AssetFactory, is_serialised=True)
    serial_number = factory.Sequence(lambda n: f"{n:03d}")
    barcode = factory.LazyAttribute(
        lambda o: f"{o.asset.barcode}-S{o.serial_number}"
    )
    status = "active"
    condition = "good"
    current_location = factory.LazyAttribute(
        lambda o: o.asset.current_location
    )


class AssetImageFactory(DjangoModelFactory):
    """Factory for AssetImage model."""

    class Meta:
        model = "assets.AssetImage"

    asset = factory.SubFactory(AssetFactory)
    image = factory.django.ImageField(
        filename="test.jpg", width=100, height=100
    )
    is_primary = False


class NFCTagFactory(DjangoModelFactory):
    """Factory for NFCTag model."""

    class Meta:
        model = "assets.NFCTag"

    tag_id = factory.Sequence(lambda n: f"NFC-{n:08d}")
    asset = factory.SubFactory(AssetFactory)
    assigned_by = factory.SubFactory(UserFactory)


class TransactionFactory(DjangoModelFactory):
    """Factory for Transaction model.

    Transaction.save() blocks updates on existing objects,
    so this factory only creates new instances.
    """

    class Meta:
        model = "assets.Transaction"

    asset = factory.SubFactory(AssetFactory)
    user = factory.SubFactory(UserFactory)
    action = "checkout"


class AssetKitFactory(DjangoModelFactory):
    """Factory for AssetKit model."""

    class Meta:
        model = "assets.AssetKit"

    kit = factory.SubFactory(AssetFactory, is_kit=True)
    component = factory.SubFactory(AssetFactory)
    quantity = 1
    is_required = True


class StocktakeSessionFactory(DjangoModelFactory):
    """Factory for StocktakeSession model."""

    class Meta:
        model = "assets.StocktakeSession"

    location = factory.SubFactory(LocationFactory)
    started_by = factory.SubFactory(UserFactory)
    status = "in_progress"


class StocktakeItemFactory(DjangoModelFactory):
    """Factory for StocktakeItem model."""

    class Meta:
        model = "assets.StocktakeItem"

    session = factory.SubFactory(StocktakeSessionFactory)
    asset = factory.SubFactory(AssetFactory)
    status = "expected"


class HoldListStatusFactory(DjangoModelFactory):
    """Factory for HoldListStatus model."""

    class Meta:
        model = "assets.HoldListStatus"

    name = factory.Sequence(lambda n: f"Status {n}")
    is_default = False


class HoldListFactory(DjangoModelFactory):
    """Factory for HoldList model."""

    class Meta:
        model = "assets.HoldList"

    name = factory.Sequence(lambda n: f"Hold List {n}")
    department = factory.SubFactory(DepartmentFactory)
    status = factory.SubFactory(HoldListStatusFactory)
    created_by = factory.SubFactory(UserFactory)


class HoldListItemFactory(DjangoModelFactory):
    """Factory for HoldListItem model."""

    class Meta:
        model = "assets.HoldListItem"

    hold_list = factory.SubFactory(HoldListFactory)
    asset = factory.SubFactory(AssetFactory)
    quantity = 1


class ProjectFactory(DjangoModelFactory):
    """Factory for Project model."""

    class Meta:
        model = "assets.Project"

    name = factory.Sequence(lambda n: f"Project {n}")
    created_by = factory.SubFactory(UserFactory)
    is_active = True


class SiteBrandingFactory(DjangoModelFactory):
    """Factory for SiteBranding model."""

    class Meta:
        model = "assets.SiteBranding"

    primary_color = "#4F46E5"


class VirtualBarcodeFactory(DjangoModelFactory):
    """Factory for VirtualBarcode model."""

    class Meta:
        model = "assets.VirtualBarcode"

    barcode = factory.Sequence(lambda n: f"VIRT-{n:08d}")
    created_by = factory.SubFactory(UserFactory)

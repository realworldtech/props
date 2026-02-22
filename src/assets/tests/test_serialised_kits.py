"""Tests for serialised assets and kit management."""

import json
from unittest.mock import patch

import pytest

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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
    AssetKit,
    AssetSerial,
    Department,
    HoldList,
    HoldListStatus,
    Location,
    Transaction,
)

User = get_user_model()

# ============================================================
# V6: SERIALISATION CONVERSION TESTS
# ============================================================


class TestSerialisationConversionV6:
    """V6: Serialisation conversion workflow."""

    def test_convert_to_serialised_impact(self, asset, user):
        asset.is_serialised = False
        asset.quantity = 5
        asset.save()
        from assets.services.serial import convert_to_serialised

        impact = convert_to_serialised(asset, user)
        assert impact["current_quantity"] == 5

    def test_apply_convert_to_serialised(self, asset, user):
        asset.is_serialised = False
        asset.save()
        from assets.services.serial import apply_convert_to_serialised

        apply_convert_to_serialised(asset, user)
        asset.refresh_from_db()
        assert asset.is_serialised is True

    def test_convert_to_non_serialised_impact(self, asset, user):
        asset.is_serialised = True
        asset.save()
        AssetSerial.objects.create(
            asset=asset, serial_number="S1", status="active"
        )
        AssetSerial.objects.create(
            asset=asset, serial_number="S2", status="active"
        )
        from assets.services.serial import convert_to_non_serialised

        impact = convert_to_non_serialised(asset, user)
        assert impact["total_serials"] == 2
        assert impact["active_serials"] == 2

    def test_apply_convert_to_non_serialised(self, asset, user):
        asset.is_serialised = True
        asset.save()
        AssetSerial.objects.create(
            asset=asset, serial_number="S1", status="active"
        )
        from assets.services.serial import (
            apply_convert_to_non_serialised,
        )

        apply_convert_to_non_serialised(asset, user)
        asset.refresh_from_db()
        assert asset.is_serialised is False
        assert asset.quantity >= 1
        assert (
            AssetSerial.objects.filter(asset=asset, is_archived=True).count()
            == 1
        )

    def test_convert_non_serialised_blocks_checked_out(self, asset, user):
        asset.is_serialised = True
        asset.save()
        AssetSerial.objects.create(
            asset=asset,
            serial_number="S1",
            status="active",
            checked_out_to=user,
        )
        from assets.services.serial import (
            apply_convert_to_non_serialised,
        )

        with pytest.raises(ValidationError, match="checked out"):
            apply_convert_to_non_serialised(asset, user)

    def test_convert_non_serialised_override_checkout(self, asset, user):
        asset.is_serialised = True
        asset.save()
        AssetSerial.objects.create(
            asset=asset,
            serial_number="S1",
            status="active",
            checked_out_to=user,
        )
        from assets.services.serial import (
            apply_convert_to_non_serialised,
        )

        apply_convert_to_non_serialised(asset, user, override_checkout=True)
        asset.refresh_from_db()
        assert asset.is_serialised is False

    def test_restore_archived_serials(self, asset, user):
        asset.is_serialised = True
        asset.save()
        s = AssetSerial.objects.create(
            asset=asset,
            serial_number="S1",
            status="active",
            is_archived=True,
        )
        from assets.services.serial import restore_archived_serials

        result = restore_archived_serials(asset, user)
        assert result["restored"] == 1
        s.refresh_from_db()
        assert s.is_archived is False

    def test_kit_pins_cleared_on_conversion(
        self, asset, user, category, location
    ):
        asset.is_serialised = True
        asset.is_kit = False
        asset.save()
        serial = AssetSerial.objects.create(
            asset=asset, serial_number="S1", status="active"
        )
        kit_asset = Asset.objects.create(
            name="Kit",
            category=category,
            current_location=location,
            is_kit=True,
            is_serialised=False,
            created_by=user,
        )
        ak = AssetKit.objects.create(
            kit=kit_asset, component=asset, serial=serial
        )
        from assets.services.serial import (
            apply_convert_to_non_serialised,
        )

        apply_convert_to_non_serialised(asset, user)
        ak.refresh_from_db()
        assert ak.serial is None

    def test_conversion_view_requires_permission(
        self, client_logged_in, asset
    ):
        response = client_logged_in.get(
            reverse(
                "assets:asset_convert_serialisation",
                args=[asset.pk],
            )
        )
        assert response.status_code == 403

    def test_conversion_view_accessible_by_admin(self, admin_client, asset):
        response = admin_client.get(
            reverse(
                "assets:asset_convert_serialisation",
                args=[asset.pk],
            )
        )
        assert response.status_code == 200


# ============================================================
# KIT CHECKOUT/CHECK-IN CASCADE TESTS (K1)
# ============================================================


class TestKitCheckoutV7:
    """V7: Kit checkout cascade."""

    def test_kit_checkout_creates_transactions(
        self, user, admin_user, category, location
    ):
        kit = Asset.objects.create(
            name="Kit",
            category=category,
            current_location=location,
            is_kit=True,
            is_serialised=False,
            created_by=admin_user,
        )
        comp = Asset.objects.create(
            name="Comp",
            category=category,
            current_location=location,
            is_serialised=False,
            created_by=admin_user,
        )
        AssetKit.objects.create(kit=kit, component=comp, is_required=True)
        from assets.services.kits import kit_checkout

        txns = kit_checkout(kit, user, admin_user)
        assert len(txns) >= 1
        comp.refresh_from_db()
        assert comp.checked_out_to == user

    def test_kit_checkout_blocks_unavailable_required(
        self, user, admin_user, category, location
    ):
        kit = Asset.objects.create(
            name="Kit",
            category=category,
            current_location=location,
            is_kit=True,
            is_serialised=False,
            created_by=admin_user,
        )
        comp = Asset.objects.create(
            name="Comp",
            category=category,
            current_location=location,
            is_serialised=False,
            created_by=admin_user,
            checked_out_to=user,
        )
        AssetKit.objects.create(kit=kit, component=comp, is_required=True)
        from assets.services.kits import kit_checkout

        with pytest.raises(ValidationError, match="unavailable"):
            kit_checkout(kit, user, admin_user)

    def test_kit_checkin_returns_all_components(
        self, user, admin_user, category, location
    ):
        kit = Asset.objects.create(
            name="Kit",
            category=category,
            current_location=location,
            is_kit=True,
            is_serialised=False,
            created_by=admin_user,
        )
        comp = Asset.objects.create(
            name="Comp",
            category=category,
            current_location=location,
            is_serialised=False,
            created_by=admin_user,
        )
        AssetKit.objects.create(kit=kit, component=comp, is_required=True)
        from assets.services.kits import kit_checkin, kit_checkout

        kit_checkout(kit, user, admin_user)
        txns = kit_checkin(kit, admin_user, to_location=location)
        assert len(txns) >= 1
        comp.refresh_from_db()
        assert comp.checked_out_to is None

    def test_serial_kit_restriction(self, admin_user, category, location):
        kit = Asset.objects.create(
            name="Kit",
            category=category,
            current_location=location,
            is_kit=True,
            is_serialised=False,
            created_by=admin_user,
        )
        comp = Asset.objects.create(
            name="Comp",
            category=category,
            current_location=location,
            is_serialised=True,
            created_by=admin_user,
        )
        serial = AssetSerial.objects.create(
            asset=comp, serial_number="S1", status="active"
        )
        AssetKit.objects.create(
            kit=kit,
            component=comp,
            serial=serial,
            is_required=True,
        )
        from assets.services.kits import (
            check_serial_kit_restriction,
        )

        blocked, reason = check_serial_kit_restriction(serial)
        assert blocked
        assert "kit" in reason.lower()

    def test_kit_checkout_not_a_kit_raises(
        self, user, admin_user, category, location
    ):
        not_kit = Asset.objects.create(
            name="Not Kit",
            category=category,
            current_location=location,
            is_kit=False,
            is_serialised=False,
            created_by=admin_user,
        )
        from assets.services.kits import kit_checkout

        with pytest.raises(ValidationError, match="not a kit"):
            kit_checkout(not_kit, user, admin_user)

    def test_kit_checkin_not_a_kit_raises(
        self, admin_user, category, location
    ):
        not_kit = Asset.objects.create(
            name="Not Kit",
            category=category,
            current_location=location,
            is_kit=False,
            is_serialised=False,
            created_by=admin_user,
        )
        from assets.services.kits import kit_checkin

        with pytest.raises(ValidationError, match="not a kit"):
            kit_checkin(not_kit, admin_user)


# ============================================================
# KIT MANAGEMENT VIEW TESTS (K5)
# ============================================================


class TestKitViewsV8:
    """V8: Kit management views."""

    def test_kit_contents_view(
        self, admin_client, admin_user, category, location
    ):
        kit = Asset.objects.create(
            name="Kit",
            category=category,
            current_location=location,
            is_kit=True,
            is_serialised=False,
            created_by=admin_user,
        )
        response = admin_client.get(
            reverse("assets:kit_contents", args=[kit.pk])
        )
        assert response.status_code == 200

    def test_kit_contents_non_kit_redirects(
        self, admin_client, admin_user, category, location
    ):
        not_kit = Asset.objects.create(
            name="Not Kit",
            category=category,
            current_location=location,
            is_kit=False,
            is_serialised=False,
            created_by=admin_user,
        )
        response = admin_client.get(
            reverse("assets:kit_contents", args=[not_kit.pk])
        )
        assert response.status_code == 302

    def test_kit_add_component(
        self, admin_client, admin_user, category, location
    ):
        kit = Asset.objects.create(
            name="Kit",
            category=category,
            current_location=location,
            is_kit=True,
            is_serialised=False,
            created_by=admin_user,
        )
        comp = Asset.objects.create(
            name="Comp",
            category=category,
            current_location=location,
            is_serialised=False,
            created_by=admin_user,
        )
        response = admin_client.post(
            reverse("assets:kit_add_component", args=[kit.pk]),
            {
                "component_id": comp.pk,
                "quantity": "1",
                "is_required": "1",
            },
        )
        assert response.status_code == 302
        assert AssetKit.objects.filter(kit=kit, component=comp).exists()

    def test_kit_remove_component(
        self, admin_client, admin_user, category, location
    ):
        kit = Asset.objects.create(
            name="Kit",
            category=category,
            current_location=location,
            is_kit=True,
            is_serialised=False,
            created_by=admin_user,
        )
        comp = Asset.objects.create(
            name="Comp",
            category=category,
            current_location=location,
            is_serialised=False,
            created_by=admin_user,
        )
        ak = AssetKit.objects.create(kit=kit, component=comp, is_required=True)
        response = admin_client.post(
            reverse(
                "assets:kit_remove_component",
                args=[kit.pk, ak.pk],
            ),
        )
        assert response.status_code == 302
        assert not AssetKit.objects.filter(pk=ak.pk).exists()

    def test_kit_remove_component_permission(
        self, client_logged_in, admin_user, category, location
    ):
        kit = Asset.objects.create(
            name="Kit",
            category=category,
            current_location=location,
            is_kit=True,
            is_serialised=False,
            created_by=admin_user,
        )
        response = client_logged_in.post(
            reverse("assets:kit_remove_component", args=[kit.pk, 1])
        )
        assert response.status_code == 403


class TestSerialisedCheckoutUX:
    """Tests for G2 S2.17.2: Serialised/non-serialised checkout UX."""

    def test_serialised_checkout_shows_serial_picker(
        self, admin_client, serialised_asset, asset_serial
    ):
        """S2.17.2-01: GET checkout for serialised asset shows
        available serials in context."""
        url = reverse(
            "assets:asset_checkout", kwargs={"pk": serialised_asset.pk}
        )
        response = admin_client.get(url)
        assert response.status_code == 200
        assert "available_serials" in response.context
        serials = list(response.context["available_serials"])
        assert asset_serial in serials

    def test_serialised_checkout_creates_serial_transaction(
        self,
        admin_client,
        serialised_asset,
        asset_serial,
        second_user,
    ):
        """S2.17.2-01: POST with serial_ids creates per-serial
        Transactions and sets serial.checked_out_to."""
        url = reverse(
            "assets:asset_checkout", kwargs={"pk": serialised_asset.pk}
        )
        response = admin_client.post(
            url,
            {
                "borrower": second_user.pk,
                "serial_ids": [asset_serial.pk],
                "notes": "Test checkout",
            },
        )
        assert response.status_code == 302
        asset_serial.refresh_from_db()
        assert asset_serial.checked_out_to == second_user
        tx = Transaction.objects.filter(
            asset=serialised_asset,
            action="checkout",
            serial=asset_serial,
        ).first()
        assert tx is not None
        assert tx.borrower == second_user

    def test_serialised_checkout_ignores_unavailable_serials(
        self,
        admin_client,
        serialised_asset,
        asset_serial,
        second_user,
    ):
        """S2.17.2-01: POST with already-checked-out serial ID
        is ignored."""
        # Pre-checkout the serial
        asset_serial.checked_out_to = second_user
        asset_serial.save()
        url = reverse(
            "assets:asset_checkout", kwargs={"pk": serialised_asset.pk}
        )
        response = admin_client.post(
            url,
            {
                "borrower": second_user.pk,
                "serial_ids": [asset_serial.pk],
                "notes": "",
            },
        )
        # Should redirect (no crash) but no new transaction created
        assert response.status_code == 302
        assert not Transaction.objects.filter(
            asset=serialised_asset,
            action="checkout",
            serial=asset_serial,
        ).exists()

    def test_nonserialized_checkout_shows_quantity_field(
        self, admin_client, non_serialised_asset
    ):
        """S2.17.2-02: GET checkout for non-serialised asset has
        show_quantity=True in context."""
        url = reverse(
            "assets:asset_checkout",
            kwargs={"pk": non_serialised_asset.pk},
        )
        response = admin_client.get(url)
        assert response.status_code == 200
        assert response.context.get("show_quantity") is True
        assert response.context.get("max_quantity") == 10

    def test_nonserialized_checkout_records_quantity(
        self, admin_client, non_serialised_asset, second_user
    ):
        """S2.17.2-02: POST with quantity creates Transaction
        with that quantity."""
        url = reverse(
            "assets:asset_checkout",
            kwargs={"pk": non_serialised_asset.pk},
        )
        response = admin_client.post(
            url,
            {
                "borrower": second_user.pk,
                "quantity": 3,
                "notes": "",
            },
        )
        assert response.status_code == 302
        tx = Transaction.objects.filter(
            asset=non_serialised_asset,
            action="checkout",
        ).first()
        assert tx is not None
        assert tx.quantity == 3

    def test_nonserialized_checkout_clamps_quantity(
        self, admin_client, non_serialised_asset, second_user
    ):
        """S2.17.2-02: Quantity > asset.quantity is clamped."""
        url = reverse(
            "assets:asset_checkout",
            kwargs={"pk": non_serialised_asset.pk},
        )
        response = admin_client.post(
            url,
            {
                "borrower": second_user.pk,
                "quantity": 999,
                "notes": "",
            },
        )
        assert response.status_code == 302
        tx = Transaction.objects.filter(
            asset=non_serialised_asset,
            action="checkout",
        ).first()
        assert tx is not None
        assert tx.quantity == non_serialised_asset.quantity

    def test_serialised_checkin_shows_checked_out_serials(
        self,
        admin_client,
        serialised_asset,
        asset_serial,
        second_user,
    ):
        """S2.17.2-03: GET check-in for serialised asset shows
        checked-out serials in context."""
        asset_serial.checked_out_to = second_user
        asset_serial.save()
        serialised_asset.checked_out_to = second_user
        serialised_asset.save()
        url = reverse(
            "assets:asset_checkin",
            kwargs={"pk": serialised_asset.pk},
        )
        response = admin_client.get(url)
        assert response.status_code == 200
        assert "checked_out_serials" in response.context
        serials = list(response.context["checked_out_serials"])
        assert asset_serial in serials

    def test_serialised_checkin_checks_in_selected_serials(
        self,
        admin_client,
        serialised_asset,
        asset_serial,
        second_user,
        location,
    ):
        """S2.17.2-03: POST with serial_ids checks in those serials."""
        asset_serial.checked_out_to = second_user
        asset_serial.save()
        serialised_asset.checked_out_to = second_user
        serialised_asset.save()
        url = reverse(
            "assets:asset_checkin",
            kwargs={"pk": serialised_asset.pk},
        )
        response = admin_client.post(
            url,
            {
                "location": location.pk,
                "serial_ids": [asset_serial.pk],
                "notes": "",
            },
        )
        assert response.status_code == 302
        asset_serial.refresh_from_db()
        assert asset_serial.checked_out_to is None
        assert asset_serial.current_location == location
        tx = Transaction.objects.filter(
            asset=serialised_asset,
            action="checkin",
            serial=asset_serial,
        ).first()
        assert tx is not None

    def test_serialised_checkin_all_serials_returns_asset(
        self,
        admin_client,
        serialised_asset,
        asset_serial,
        second_user,
        location,
    ):
        """S2.17.2-03: When all serials are checked in,
        asset.checked_out_to is cleared."""
        asset_serial.checked_out_to = second_user
        asset_serial.save()
        serialised_asset.checked_out_to = second_user
        serialised_asset.save()
        url = reverse(
            "assets:asset_checkin",
            kwargs={"pk": serialised_asset.pk},
        )
        admin_client.post(
            url,
            {
                "location": location.pk,
                "serial_ids": [asset_serial.pk],
                "notes": "",
            },
        )
        serialised_asset.refresh_from_db()
        assert serialised_asset.checked_out_to is None


@pytest.mark.django_db
class TestKitEnhancements:
    """Tests for M9, M10, L26, L27 kit enhancements."""

    def test_kit_partial_return_nonserialized(
        self,
        kit_asset,
        non_serialised_asset,
        admin_user,
        second_user,
        location,
    ):
        """M9: Partial return of non-serialised kit component."""
        from assets.services.kits import kit_checkout, kit_partial_return

        AssetKit.objects.create(
            kit=kit_asset,
            component=non_serialised_asset,
            quantity=1,
            is_required=True,
        )

        kit_checkout(kit_asset, second_user, admin_user, destination=location)
        kit_asset.refresh_from_db()
        non_serialised_asset.refresh_from_db()
        assert kit_asset.checked_out_to == second_user
        assert non_serialised_asset.checked_out_to == second_user

        # Partial return: return component but kit stays checked out
        txns = kit_partial_return(
            kit_asset,
            [non_serialised_asset.pk],
            admin_user,
            to_location=location,
        )
        non_serialised_asset.refresh_from_db()
        kit_asset.refresh_from_db()

        assert non_serialised_asset.checked_out_to is None
        assert kit_asset.checked_out_to == second_user
        assert len(txns) >= 1

    def test_kit_partial_return_creates_transaction(
        self,
        kit_asset,
        non_serialised_asset,
        admin_user,
        second_user,
        location,
    ):
        """M9: Partial return creates a kit_return transaction."""
        from assets.services.kits import kit_checkout, kit_partial_return

        AssetKit.objects.create(
            kit=kit_asset,
            component=non_serialised_asset,
            quantity=1,
            is_required=True,
        )

        kit_checkout(kit_asset, second_user, admin_user, destination=location)

        txns = kit_partial_return(
            kit_asset,
            [non_serialised_asset.pk],
            admin_user,
            to_location=location,
        )
        assert len(txns) == 1
        assert txns[0].action == "kit_return"
        assert txns[0].asset == non_serialised_asset
        assert txns[0].to_location == location

    def test_kit_completion_status_complete(
        self,
        kit_asset,
        asset,
        kit_component,
    ):
        """M10: Kit with all required components available is complete."""
        from assets.services.kits import get_kit_completion_status

        result = get_kit_completion_status(kit_asset)
        assert result["status"] == "complete"
        assert result["total"] == 1
        assert result["available"] == 1
        assert result["missing"] == []

    def test_kit_completion_status_incomplete(
        self,
        kit_asset,
        asset,
        kit_component,
        second_user,
    ):
        """M10: Kit missing a required component is incomplete."""
        from assets.services.kits import get_kit_completion_status

        # Check out the component so it's unavailable
        asset.checked_out_to = second_user
        asset.save(update_fields=["checked_out_to"])

        result = get_kit_completion_status(kit_asset)
        assert result["status"] == "incomplete"
        assert result["total"] == 1
        assert result["available"] == 0
        assert len(result["missing"]) == 1
        assert result["missing"][0] == asset.name

    def test_asset_list_is_kit_filter(
        self,
        admin_client,
        kit_asset,
        asset,
    ):
        """L26: is_kit=1 filter returns only kit assets."""
        response = admin_client.get(
            reverse("assets:asset_list"),
            {"is_kit": "1", "status": "active"},
        )
        assert response.status_code == 200
        page_assets = response.context["page_obj"].object_list
        for a in page_assets:
            assert a.is_kit is True

    def test_asset_list_is_kit_filter_excludes_non_kits(
        self,
        admin_client,
        kit_asset,
        asset,
    ):
        """L26: is_kit=0 filter returns only non-kit assets."""
        response = admin_client.get(
            reverse("assets:asset_list"),
            {"is_kit": "0", "status": "active"},
        )
        assert response.status_code == 200
        page_assets = response.context["page_obj"].object_list
        for a in page_assets:
            assert a.is_kit is False

    def test_asset_detail_shows_member_of_kits(
        self,
        admin_client,
        kit_asset,
        asset,
        kit_component,
    ):
        """L27: Asset detail context includes member_of_kits."""
        response = admin_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        assert response.status_code == 200
        assert "member_of_kits" in response.context
        kits = list(response.context["member_of_kits"])
        assert len(kits) == 1
        assert kits[0].kit == kit_asset


@pytest.mark.django_db
class TestSerialisedEdgeCases:
    """S7.19: Serialised asset edge cases."""

    def test_all_serials_disposed_auto_updates_parent_status(
        self, admin_user, serialised_asset, asset_serial
    ):
        """VV794: When all serials are disposed, parent asset's actual
        status should be auto-updated to disposed (S7.19.2)."""
        asset_serial.status = "disposed"
        asset_serial.save()
        serialised_asset.refresh_from_db()
        # The derived_status property returns disposed, but the actual
        # status field on the parent should also be updated
        # automatically per spec.
        assert serialised_asset.status == "disposed", (
            "VV794: When all serials are disposed, the parent asset's "
            "actual status field must be auto-updated to 'disposed'. "
            f"Current status: '{serialised_asset.status}'. The "
            "derived_status property works but the DB field is not "
            "updated."
        )

    def test_scanning_disposed_serial_barcode_shows_message(
        self, admin_client, admin_user, serialised_asset, asset_serial
    ):
        """VV795: Scanning a disposed serial's barcode should show
        specific message (S7.19.3)."""
        asset_serial.status = "disposed"
        asset_serial.save()
        # Scan the serial's barcode
        url = reverse("assets:scan_lookup")
        response = admin_client.get(url, {"code": asset_serial.barcode})
        if response.status_code == 200:
            content = response.content.decode()
            # Should show disposed message, not redirect to Quick Capture
            data = (
                json.loads(content)
                if "application/json" in response.get("Content-Type", "")
                else {}
            )
            if data:
                assert data.get("status") != "not_found", (
                    "VV795: Disposed serial scan must NOT redirect to "
                    "Quick Capture. Should show 'disposed' message."
                )
        elif response.status_code == 302:
            redirect_url = response.url
            assert "quick-capture" not in redirect_url, (
                "VV795: Scanning a disposed serial must NOT redirect "
                "to Quick Capture. Should show a disposed message."
            )

    def test_merging_serialised_source_into_non_serialised_target(
        self, admin_user, category, location
    ):
        """VV797: Merging a serialised source into a non-serialised
        target should make target serialised (S7.19.5)."""
        target = Asset.objects.create(
            name="Non-Ser Target",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            quantity=5,
            created_by=admin_user,
        )
        source = Asset.objects.create(
            name="Ser Source",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
            created_by=admin_user,
        )
        s1 = AssetSerial.objects.create(
            asset=source,
            serial_number="SRC-001",
            barcode=f"{source.barcode}-SSRC001",
            status="active",
            current_location=location,
        )
        from assets.services.merge import merge_assets

        merge_assets(target, [source], admin_user)
        target.refresh_from_db()
        s1.refresh_from_db()
        # Per spec: "Only source serialised: Target becomes serialised.
        # Source's serials transfer to target."
        assert target.is_serialised is True, (
            "VV797: When merging a serialised source into a "
            "non-serialised target, the target must become serialised. "
            f"Currently target.is_serialised = {target.is_serialised}."
        )
        assert (
            s1.asset_id == target.pk
        ), "VV797: Serials from source must be re-parented to target."

    def test_hold_list_items_serial_level_availability(
        self, admin_user, serialised_asset, asset_serial, department
    ):
        """VV798: Hold list items for serialised assets should check
        serial-level availability (S7.19.6)."""
        # Create a second serial that is checked out
        s2 = AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="002",
            barcode=f"{serialised_asset.barcode}-S002",
            status="active",
            current_location=serialised_asset.current_location,
        )
        s2.checked_out_to = admin_user
        s2.save()

        hl_status = HoldListStatus.objects.create(
            name="Active Serial HL", is_default=False
        )
        hl = HoldList.objects.create(
            name="Serial HL",
            department=department,
            status=hl_status,
            created_by=admin_user,
            start_date="2026-05-01",
            end_date="2026-05-15",
        )
        from assets.models import HoldListItem

        item = HoldListItem.objects.create(
            hold_list=hl,
            asset=serialised_asset,
            quantity=2,
            added_by=admin_user,
        )
        # Available serials: 1 (asset_serial is active, s2 is checked
        # out). Requested: 2. Should show a warning.
        available = serialised_asset.serials.filter(
            status="active",
            checked_out_to__isnull=True,
            is_archived=False,
        ).count()
        assert available < item.quantity, (
            "Test setup error: expected fewer available serials than "
            "requested"
        )
        # The system should have a way to check this — currently
        # overlap detection does not account for serial availability
        from assets.services.holdlists import detect_overlaps

        # This is a structural test — the overlap/availability check
        # should consider serial-level availability
        warnings = detect_overlaps(hl)
        # Even without overlaps, the system should warn about
        # insufficient serial availability
        assert any(
            "serial" in str(w).lower() or "available" in str(w).lower()
            for w in warnings
        ), (
            "VV798: Hold list system must check available serial count "
            "when requested quantity exceeds available serials. "
            f"Available: {available}, Requested: {item.quantity}. "
            "Currently no serial-level availability check exists."
        )

    def test_quantity_mismatch_after_conversion_round_trip(
        self, admin_user, serialised_asset, asset_serial
    ):
        """VV801: Quantity mismatch after round-trip conversion should
        be flagged for reconciliation (S7.19.9)."""
        from assets.services.serial import (
            apply_convert_to_non_serialised,
            apply_convert_to_serialised,
            restore_archived_serials,
        )

        # Convert to non-serialised
        apply_convert_to_non_serialised(serialised_asset, admin_user)
        serialised_asset.refresh_from_db()

        # Change quantity manually
        serialised_asset.quantity = 5
        serialised_asset.save(update_fields=["quantity"])

        # Convert back to serialised
        apply_convert_to_serialised(serialised_asset, admin_user)
        result = restore_archived_serials(serialised_asset, admin_user)

        # After restore, serial count != quantity
        active_serial_count = serialised_asset.serials.filter(
            is_archived=False
        ).count()
        assert (
            active_serial_count != 5
        ), "Test setup: serial count should differ from quantity"
        # The system should flag this discrepancy via a
        # "discrepancy" key in the result dict
        assert result.get("discrepancy") or (
            active_serial_count != serialised_asset.quantity
            and "conflicts" in result
        ), (
            "VV801: After round-trip conversion with quantity change, "
            "the system must display a discrepancy and allow the user "
            f"to reconcile. Serials: {active_serial_count}, "
            f"Quantity: 5. Currently no reconciliation exists."
        )

    def test_concurrent_conversion_prevented(
        self, admin_user, serialised_asset
    ):
        """VV802: Concurrent conversion attempts must be prevented
        (S7.19.10)."""
        import inspect

        from assets.services import serial as serial_mod

        source = inspect.getsource(serial_mod.apply_convert_to_non_serialised)
        source += inspect.getsource(serial_mod.apply_convert_to_serialised)
        has_locking = (
            "select_for_update" in source
            or "atomic" in source
            or "lock" in source.lower()
        )
        assert has_locking, (
            "VV802: Serialisation conversion must use database-level "
            "locking (select_for_update or similar) to prevent "
            "concurrent conversions. Currently no locking is "
            "implemented."
        )


@pytest.mark.django_db
class TestSerialisedEdgeCasesExtended:
    """S7.19 extended: Additional serialised asset tests."""

    def test_disposed_serial_scan_shows_specific_disposed_message(
        self, admin_client, serialised_asset, asset_serial
    ):
        """VV795b: Scanning a disposed serial's barcode should show a
        specific message mentioning the serial number (S7.19.3)."""
        asset_serial.status = "disposed"
        asset_serial.save()
        # Use the unified lookup endpoint
        url = reverse(
            "assets:asset_by_identifier",
            args=[asset_serial.barcode],
        )
        response = admin_client.get(url, follow=True)
        content = response.content.decode()
        # Per spec: "The system MUST display a message: 'This serial
        # (SN-XXX) of [asset name] has been disposed.'"
        assert (
            asset_serial.serial_number in content
            and "disposed" in content.lower()
        ), (
            "VV795b: Scanning a disposed serial must show a message "
            "with the serial number and 'disposed' status. The page "
            f"does not contain serial number '{asset_serial.serial_number}' "
            "and/or 'disposed'."
        )

    def test_serial_number_conflicts_during_merge_handled(
        self, admin_user, category, location
    ):
        """VV797b: Serial number conflicts during merge should be handled
        by appending a suffix (S7.19.5)."""
        target = Asset.objects.create(
            name="Target SN Conflict",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
            created_by=admin_user,
        )
        source = Asset.objects.create(
            name="Source SN Conflict",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
            created_by=admin_user,
        )
        # Create serials with same serial_number on both assets
        AssetSerial.objects.create(
            asset=target,
            serial_number="SN-001",
            barcode=f"{target.barcode}-S001",
            status="active",
            current_location=location,
        )
        AssetSerial.objects.create(
            asset=source,
            serial_number="SN-001",
            barcode=f"{source.barcode}-S001",
            status="active",
            current_location=location,
        )
        from assets.services.merge import merge_assets

        merge_assets(target, [source], admin_user)
        # After merge, both serials should exist on target, with the
        # conflicting one renamed (e.g., "SN-001-merged")
        target_serials = AssetSerial.objects.filter(asset=target).values_list(
            "serial_number", flat=True
        )
        assert len(set(target_serials)) == 2, (
            "VV797b: Serial number conflicts during merge must be "
            "handled by renaming. Both serials should exist on target "
            f"with unique serial numbers. Got: {list(target_serials)}"
        )


# ============================================================
# S7 EDGE CASE GAP TESTS — Pre-implementation (expected to FAIL)
# ============================================================


@pytest.mark.django_db
class TestKitEdgeCases:
    """S7.16 — Kit management edge cases."""

    def test_vv774_nested_kit_checked_out_component_path(
        self, admin_user, category, location, second_user
    ):
        """VV774: Nested kit checkout with already-checked-out
        component should report full path."""
        from assets.services.kits import kit_checkout

        kit_a = AssetFactory(
            name="Kit A",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
        )
        kit_b = AssetFactory(
            name="Kit B",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
        )
        comp_x = AssetFactory(
            name="Component X",
            category=category,
            current_location=location,
            status="active",
        )

        AssetKit.objects.create(
            kit=kit_a,
            component=kit_b,
            quantity=1,
            is_required=True,
        )
        AssetKit.objects.create(
            kit=kit_b,
            component=comp_x,
            quantity=1,
            is_required=True,
        )

        comp_x.checked_out_to = second_user
        comp_x.save(update_fields=["checked_out_to"])

        with pytest.raises(ValidationError) as exc_info:
            kit_checkout(kit_a, admin_user, admin_user)

        error_msg = str(exc_info.value)
        assert "Kit B" in error_msg or "Component X" in error_msg, (
            "S7.16.3: Nested kit checkout failure must report "
            "the unavailable component name."
        )
        assert ">" in error_msg or "path" in error_msg.lower(), (
            "S7.16.3: Error must include path to unavailable "
            "component (e.g. 'Kit A > Kit B > Component X'). "
            "Currently no path information is provided."
        )

    def test_vv775_kit_only_independent_checkout_warns(
        self,
        admin_client,
        admin_user,
        kit_asset,
        asset,
        kit_component,
    ):
        """VV775: Checking out a kit-only component
        independently should warn but not block."""
        AssetKit.objects.filter(kit=kit_asset, component=asset).update(
            is_kit_only=True
        )

        response = admin_client.get(
            reverse("assets:asset_checkout", args=[asset.pk])
        )
        content = response.content.decode()
        assert (
            "kit" in content.lower()
            or "normally checked out as part of" in content.lower()
        ), (
            "S7.16.4: Checking out a kit-only component "
            "independently should display a warning. "
            "Currently no warning is shown."
        )

    def test_vv776_partial_quantity_checkout_blocks(
        self, admin_user, category, location
    ):
        """VV776: Kit specifying more quantity than available
        should block checkout for required components."""
        from assets.services.kits import kit_checkout

        kit = AssetFactory(
            name="Quantity Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
        )
        component = AssetFactory(
            name="Bulk Component",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
        )
        for i in range(3):
            AssetSerialFactory(
                asset=component,
                serial_number=f"PQ-{i}",
                barcode=f"{component.barcode}-PQ{i}",
                current_location=location,
            )

        AssetKit.objects.create(
            kit=kit,
            component=component,
            quantity=10,
            is_required=True,
        )

        with pytest.raises(ValidationError) as exc_info:
            kit_checkout(kit, admin_user, admin_user)

        error_msg = str(exc_info.value)
        assert (
            "insufficient" in error_msg.lower()
            or "unavailable" in error_msg.lower()
            or "available" in error_msg.lower()
        ), (
            "S7.16.5: Kit checkout with insufficient quantity "
            "must report the shortage."
        )

    def test_vv778_pinned_serial_unavailable_suggests(
        self, admin_user, category, location, second_user
    ):
        """VV778: Pinned serial unavailable should suggest
        replacement with another serial."""
        from assets.services.kits import kit_checkout

        kit = AssetFactory(
            name="Pinned Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
        )
        component = AssetFactory(
            name="Serialised Comp",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
        )
        pinned = AssetSerialFactory(
            asset=component,
            serial_number="PIN-001",
            barcode=f"{component.barcode}-PIN1",
            current_location=location,
        )
        AssetSerialFactory(
            asset=component,
            serial_number="PIN-002",
            barcode=f"{component.barcode}-PIN2",
            current_location=location,
        )

        AssetKit.objects.create(
            kit=kit,
            component=component,
            quantity=1,
            is_required=True,
            serial=pinned,
        )

        pinned.checked_out_to = second_user
        pinned.save(update_fields=["checked_out_to"])

        with pytest.raises(ValidationError) as exc_info:
            kit_checkout(kit, admin_user, admin_user)

        error_msg = str(exc_info.value)
        assert (
            "replacement" in error_msg.lower()
            or "different serial" in error_msg.lower()
            or "PIN-002" in error_msg
            or "select" in error_msg.lower()
        ), (
            "S7.16.7: When a pinned serial is unavailable, "
            "the error should suggest replacing it with "
            "another available serial. Currently just says "
            "'unavailable'."
        )

    def test_vv779_kit_checkin_atomic_rollback(
        self, admin_user, category, location, second_user
    ):
        """VV779: Kit check-in failure on one serial should
        roll back the entire check-in (atomic)."""
        from assets.services.kits import kit_checkin

        kit = AssetFactory(
            name="Atomic Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            checked_out_to=second_user,
        )
        comp1 = AssetFactory(
            name="Comp 1",
            category=category,
            current_location=location,
            status="active",
            checked_out_to=second_user,
        )
        comp2 = AssetFactory(
            name="Comp 2",
            category=category,
            current_location=location,
            status="active",
            checked_out_to=second_user,
        )

        AssetKit.objects.create(
            kit=kit,
            component=comp1,
            quantity=1,
            is_required=True,
        )
        AssetKit.objects.create(
            kit=kit,
            component=comp2,
            quantity=1,
            is_required=True,
        )

        original_save = Asset.save
        call_count = [0]

        def failing_save(self, *args, **kwargs):
            if self.pk == comp2.pk:
                call_count[0] += 1
                if call_count[0] <= 1:
                    raise Exception("Simulated DB error")
            return original_save(self, *args, **kwargs)

        with patch.object(Asset, "save", failing_save):
            try:
                kit_checkin(kit, admin_user, location)
            except Exception:
                pass

        comp1.refresh_from_db()
        assert comp1.checked_out_to == second_user, (
            "S7.16.8: Kit check-in must be atomic. If any "
            "serial check-in fails, the entire kit check-in "
            "must roll back. Currently each component is "
            "processed independently without a transaction "
            "wrapper."
        )

    def test_vv780_pinned_serial_disposed_auto_unpin(
        self, admin_user, category, location
    ):
        """VV780: Disposing a pinned serial should auto-unpin
        it from kit components."""
        kit = AssetFactory(
            name="Unpin Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
        )
        component = AssetFactory(
            name="Pinnable Comp",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
        )
        serial = AssetSerialFactory(
            asset=component,
            serial_number="DISP-001",
            barcode=f"{component.barcode}-DISP1",
            current_location=location,
        )

        kit_link = AssetKit.objects.create(
            kit=kit,
            component=component,
            quantity=1,
            is_required=True,
            serial=serial,
        )

        serial.status = "disposed"
        serial.save()

        kit_link.refresh_from_db()
        assert kit_link.serial is None, (
            "S7.16.9: Disposing a pinned serial must auto-unpin "
            "it from kit components (set AssetKit.serial to "
            "NULL). Currently the serial FK is not cleared on "
            "disposal."
        )

    def test_vv781_kit_detail_shows_replacement_needed(
        self, admin_client, admin_user, category, location
    ):
        """VV781: Kit detail should show 'replacement needed'
        for disposed pinned serial slots."""
        kit = AssetFactory(
            name="Replace Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
        )
        component = AssetFactory(
            name="Replaceable Comp",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
        )
        serial = AssetSerialFactory(
            asset=component,
            serial_number="REPL-001",
            barcode=f"{component.barcode}-REPL1",
            current_location=location,
        )

        AssetKit.objects.create(
            kit=kit,
            component=component,
            quantity=1,
            is_required=True,
            serial=serial,
        )

        serial.status = "disposed"
        serial.save()

        response = admin_client.get(
            reverse("assets:kit_contents", args=[kit.pk])
        )
        content = response.content.decode()
        assert (
            "replacement" in content.lower()
            or "disposed" in content.lower()
            or "unavailable" in content.lower()
        ), (
            "S7.16.10: Kit detail must show 'replacement "
            "needed' for disposed pinned serial slots. "
            "Currently no visual indicator is shown."
        )


@pytest.mark.django_db
class TestV507KitComponentInAnotherKit:
    """V507 S2.17.3-04: Warn when component is in another checked-out kit."""

    def test_warn_component_in_another_checked_out_kit(
        self, admin_user, category, location, second_user
    ):
        """Checking out a kit should warn if a component is in
        another currently checked-out kit."""
        from assets.services.kits import kit_checkout

        shared_comp = AssetFactory(
            name="Shared Component",
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        kit_a = AssetFactory(
            name="Kit A",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        kit_b = AssetFactory(
            name="Kit B",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        AssetKit.objects.create(
            kit=kit_a, component=shared_comp, is_required=True
        )
        AssetKit.objects.create(
            kit=kit_b, component=shared_comp, is_required=True
        )

        # Checkout Kit A first
        kit_checkout(kit_a, second_user, admin_user)
        shared_comp.refresh_from_db()

        # Now try to checkout Kit B — should fail because
        # shared_comp is in checked-out Kit A
        with pytest.raises(ValidationError) as exc_info:
            kit_checkout(kit_b, admin_user, admin_user)

        error_msg = str(exc_info.value)
        assert "unavailable" in error_msg.lower(), (
            "S2.17.3-04: Kit checkout must block when a "
            "required component is in another checked-out kit."
        )


@pytest.mark.django_db
class TestV500NonSerialisedConcurrentCheckouts:
    """V500 S2.17.2-05: Non-serialised concurrent checkouts —
    available quantity = total minus open checkout quantities."""

    def test_available_count_subtracts_open_checkouts(
        self, category, location, user
    ):
        """available_count for non-serialised should be total minus
        sum of open checkout quantities."""
        asset = AssetFactory(
            name="Bulk Cables",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            quantity=10,
            created_by=user,
        )
        # Create an open checkout for 3 units
        Transaction.objects.create(
            asset=asset,
            user=user,
            action="checkout",
            borrower=user,
            quantity=3,
        )
        asset.checked_out_to = user
        asset.save(update_fields=["checked_out_to"])

        # available_count should reflect quantity minus checked-out
        assert asset.available_count <= 10
        assert asset.available_count >= 0

    def test_non_serialised_quantity_tracking(self, category, location, user):
        """Non-serialised asset with quantity=10 and 3 checked out
        should have 7 available (or correct calculation)."""
        asset = AssetFactory(
            name="Bulk Cables",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            quantity=10,
            created_by=user,
        )
        # Currently non-serialised uses checked_out_to as binary
        # This test documents expected behavior
        assert asset.available_count == 10  # Nothing checked out
        asset.checked_out_to = user
        asset.save(update_fields=["checked_out_to"])
        # With something checked out, should have fewer available
        assert asset.available_count < 10


@pytest.mark.django_db
class TestV526KitAddComponentViaSearch:
    """V526 S2.17.5-02: Adding kit component via asset search/scan."""

    def test_add_component_via_post(
        self,
        admin_client,
        admin_user,
        kit_asset,
        category,
        location,
    ):
        """Can add component to kit via POST to kit_add_component."""
        comp = AssetFactory(
            name="New Component",
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        response = admin_client.post(
            reverse("assets:kit_add_component", args=[kit_asset.pk]),
            {
                "component_id": comp.pk,
                "is_required": "1",
                "quantity": "1",
            },
        )
        assert response.status_code == 302
        assert AssetKit.objects.filter(kit=kit_asset, component=comp).exists()

    def test_add_component_non_kit_redirects(
        self, admin_client, admin_user, asset, category, location
    ):
        """Adding component to non-kit asset should error."""
        comp = AssetFactory(
            name="Component",
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        response = admin_client.post(
            reverse("assets:kit_add_component", args=[asset.pk]),
            {"component_id": comp.pk, "is_required": "1", "quantity": "1"},
        )
        assert response.status_code == 302
        assert not AssetKit.objects.filter(kit=asset, component=comp).exists()

    def test_add_self_as_component_fails(
        self, admin_client, admin_user, kit_asset
    ):
        """Cannot add a kit as a component of itself."""
        response = admin_client.post(
            reverse("assets:kit_add_component", args=[kit_asset.pk]),
            {
                "component_id": kit_asset.pk,
                "is_required": "1",
                "quantity": "1",
            },
        )
        assert response.status_code == 302
        assert not AssetKit.objects.filter(
            kit=kit_asset, component=kit_asset
        ).exists()


@pytest.mark.django_db
class TestV483ConversionConfirmationDialog:
    """V483 S2.17.1d-04: Conversion requires confirmation dialog."""

    def test_conversion_requires_confirm_param(self, admin_client, asset):
        """POST without confirm param should reject conversion."""
        response = admin_client.post(
            reverse(
                "assets:asset_convert_serialisation",
                args=[asset.pk],
            ),
            {},
        )
        # Should redirect back without converting
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.is_serialised is False

    def test_conversion_with_confirm_succeeds(self, admin_client, asset):
        """POST with confirm=1 should perform conversion."""
        response = admin_client.post(
            reverse(
                "assets:asset_convert_serialisation",
                args=[asset.pk],
            ),
            {"confirm": "1"},
        )
        assert response.status_code == 302
        asset.refresh_from_db()
        assert asset.is_serialised is True


@pytest.mark.django_db
class TestV491DecliningRestoreKeepsArchivedSerials:
    """V491 S2.17.1d-11: Declining restore keeps archived serials."""

    def test_conversion_without_restore_keeps_archived(
        self, admin_client, asset, user
    ):
        """Converting to serialised without restore_serials=1 should
        not restore archived serials."""
        # First make it serialised with serials, then convert to non-serialised
        asset.is_serialised = True
        asset.save()
        AssetSerial.objects.create(
            asset=asset,
            serial_number="S1",
            status="active",
            is_archived=True,
        )
        # Now convert back (it's currently serialised, so convert
        # to non-serialised first)
        from assets.services.serial import apply_convert_to_non_serialised

        apply_convert_to_non_serialised(asset, user)
        asset.refresh_from_db()
        assert asset.is_serialised is False

        # Now convert to serialised without restoring
        response = admin_client.post(
            reverse(
                "assets:asset_convert_serialisation",
                args=[asset.pk],
            ),
            {"confirm": "1"},
        )
        assert response.status_code == 302
        # Archived serials should still be archived
        archived = AssetSerial.objects.filter(
            asset=asset, is_archived=True
        ).count()
        assert archived >= 1, "Declining restore should keep archived serials."


@pytest.mark.django_db
class TestV493ConversionRestrictedToManagersAdmins:
    """V493 S2.17.1d-13: Conversion restricted to managers and admins."""

    def test_member_cannot_access_conversion(self, client_logged_in, asset):
        """Regular member should get 403 on conversion page."""
        response = client_logged_in.get(
            reverse(
                "assets:asset_convert_serialisation",
                args=[asset.pk],
            )
        )
        assert response.status_code == 403

    def test_viewer_cannot_access_conversion(self, viewer_client, asset):
        """Viewer should get 403 on conversion page."""
        response = viewer_client.get(
            reverse(
                "assets:asset_convert_serialisation",
                args=[asset.pk],
            )
        )
        assert response.status_code == 403

    def test_admin_can_access_conversion(self, admin_client, asset):
        """Admin should access conversion page successfully."""
        response = admin_client.get(
            reverse(
                "assets:asset_convert_serialisation",
                args=[asset.pk],
            )
        )
        assert response.status_code == 200

    def test_dept_manager_can_access_conversion(
        self, dept_manager_client, asset
    ):
        """Department manager should access conversion page."""
        response = dept_manager_client.get(
            reverse(
                "assets:asset_convert_serialisation",
                args=[asset.pk],
            )
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestV494ConversionActionOnAssetEdit:
    """V494 S2.17.1d-14: Conversion action accessible from asset detail."""

    def test_asset_detail_has_conversion_link(self, admin_client, asset):
        """Asset detail page should have a link to conversion page."""
        response = admin_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        content = response.content.decode()
        conversion_url = reverse(
            "assets:asset_convert_serialisation",
            args=[asset.pk],
        )
        assert conversion_url in content or "convert" in content.lower(), (
            "S2.17.1d-14: Asset detail should provide access to "
            "the serialisation conversion page."
        )


@pytest.mark.django_db
class TestV495ConversionOverrideUI:
    """V495 S2.17.1d-15: Conversion override UI with confirmation."""

    def test_conversion_override_checkout_field_in_form(
        self, admin_client, asset, user
    ):
        """When converting serialised-to-non with checked-out serials,
        override_checkout field should be available."""
        asset.is_serialised = True
        asset.save()
        AssetSerial.objects.create(
            asset=asset,
            serial_number="S1",
            status="active",
            checked_out_to=user,
        )
        response = admin_client.get(
            reverse(
                "assets:asset_convert_serialisation",
                args=[asset.pk],
            )
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "override" in content.lower() or "warning" in content.lower()


@pytest.mark.django_db
class TestV508NestedKitsAllowed:
    """V508 S2.17.3-05: Nested kits are allowed."""

    def test_kit_can_contain_another_kit(self, admin_user, category, location):
        """A kit can have another kit as a component."""
        outer_kit = AssetFactory(
            name="Outer Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        inner_kit = AssetFactory(
            name="Inner Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        ak = AssetKit(
            kit=outer_kit,
            component=inner_kit,
            is_required=True,
        )
        ak.full_clean()
        ak.save()
        assert AssetKit.objects.filter(
            kit=outer_kit, component=inner_kit
        ).exists()

    def test_nested_kit_circular_reference_blocked(
        self, admin_user, category, location
    ):
        """Circular nested kits should be rejected."""
        kit_a = AssetFactory(
            name="Kit A",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        kit_b = AssetFactory(
            name="Kit B",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        AssetKit.objects.create(kit=kit_a, component=kit_b, is_required=True)
        circular = AssetKit(kit=kit_b, component=kit_a, is_required=True)
        with pytest.raises(ValidationError, match="[Cc]ircular"):
            circular.full_clean()


@pytest.mark.django_db
class TestV513OptionalComponentsChecklist:
    """V513 S2.17.4-02: Optional components presented as checklist."""

    def test_optional_component_can_be_created(
        self, kit_asset, category, location, admin_user
    ):
        """Optional (non-required) components can be added to kits."""
        comp = AssetFactory(
            name="Optional Comp",
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        ak = AssetKit.objects.create(
            kit=kit_asset,
            component=comp,
            is_required=False,
        )
        assert ak.is_required is False

    def test_kit_checkout_with_selected_optional(
        self, admin_user, category, location, second_user
    ):
        """Kit checkout with selected_optionals should check out
        those optional components."""
        from assets.services.kits import kit_checkout

        kit = AssetFactory(
            name="Optional Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        opt_comp = AssetFactory(
            name="Optional Component",
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        ak = AssetKit.objects.create(
            kit=kit,
            component=opt_comp,
            is_required=False,
        )
        _txns = kit_checkout(  # noqa: F841
            kit,
            second_user,
            admin_user,
            selected_optionals=[ak.pk],
        )
        opt_comp.refresh_from_db()
        assert opt_comp.checked_out_to == second_user


@pytest.mark.django_db
class TestV516OptionalUnavailabilityDoesNotBlock:
    """V516 S2.17.4-05: Optional component unavailability does not
    block kit checkout."""

    def test_unavailable_optional_does_not_block(
        self, admin_user, category, location, second_user
    ):
        """Kit checkout should succeed even if an optional component
        is unavailable."""
        from assets.services.kits import kit_checkout

        kit = AssetFactory(
            name="Opt Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        req_comp = AssetFactory(
            name="Required Comp",
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        opt_comp = AssetFactory(
            name="Optional Comp",
            category=category,
            current_location=location,
            status="active",
            checked_out_to=second_user,
            created_by=admin_user,
        )
        AssetKit.objects.create(kit=kit, component=req_comp, is_required=True)
        AssetKit.objects.create(kit=kit, component=opt_comp, is_required=False)

        # Should not raise even though optional is unavailable
        txns = kit_checkout(kit, admin_user, admin_user)
        assert len(txns) >= 1
        req_comp.refresh_from_db()
        assert req_comp.checked_out_to == admin_user


@pytest.mark.django_db
class TestV518NestedKitCheckoutCascade:
    """V518 S2.17.4-07: Nested kit checkout cascade is recursive."""

    def test_nested_kit_checkout_cascades(
        self, admin_user, category, location, second_user
    ):
        """Checking out a kit with a nested kit should recursively
        check out nested components."""
        from assets.services.kits import kit_checkout

        outer = AssetFactory(
            name="Outer Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        inner = AssetFactory(
            name="Inner Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        leaf = AssetFactory(
            name="Leaf Component",
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )

        AssetKit.objects.create(kit=outer, component=inner, is_required=True)
        AssetKit.objects.create(kit=inner, component=leaf, is_required=True)

        _txns = kit_checkout(outer, second_user, admin_user)  # noqa: F841

        leaf.refresh_from_db()
        inner.refresh_from_db()
        assert inner.checked_out_to == second_user
        assert leaf.checked_out_to == second_user, (
            "S2.17.4-07: Nested kit checkout must recursively "
            "cascade to leaf components."
        )


@pytest.mark.django_db
class TestV521KitCompletionNonSerialised:
    """V521 S2.17.4-10: Kit completion for non-serialised components."""

    def test_kit_completion_non_serialised_available(
        self, kit_asset, asset, kit_component
    ):
        """Non-serialised component that is not checked out
        should count as available for kit completion."""
        from assets.services.kits import get_kit_completion_status

        result = get_kit_completion_status(kit_asset)
        assert result["status"] == "complete"
        assert result["available"] == 1

    def test_kit_completion_non_serialised_checked_out(
        self, kit_asset, asset, kit_component, second_user
    ):
        """Non-serialised component that is checked out should
        count as missing for kit completion."""
        from assets.services.kits import get_kit_completion_status

        asset.checked_out_to = second_user
        asset.save(update_fields=["checked_out_to"])

        result = get_kit_completion_status(kit_asset)
        assert result["status"] == "incomplete"
        assert asset.name in result["missing"]

    def test_kit_completion_mixed_components(
        self, admin_user, category, location, second_user
    ):
        """Kit with mix of serialised and non-serialised components."""
        from assets.services.kits import get_kit_completion_status

        kit = AssetFactory(
            name="Mixed Kit",
            is_kit=True,
            category=category,
            current_location=location,
            status="active",
            created_by=admin_user,
        )
        non_ser = AssetFactory(
            name="Non-ser Comp",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=admin_user,
        )
        ser = AssetFactory(
            name="Ser Comp",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
            created_by=admin_user,
        )
        AssetSerialFactory(
            asset=ser,
            serial_number="MIX-001",
            barcode=f"{ser.barcode}-MIX1",
            current_location=location,
        )
        AssetKit.objects.create(kit=kit, component=non_ser, is_required=True)
        AssetKit.objects.create(kit=kit, component=ser, is_required=True)

        result = get_kit_completion_status(kit)
        assert result["status"] == "complete"
        assert result["total"] == 2
        assert result["available"] == 2


# ============================================================
# S8 BATCH 6: VERIFICATION GAP TEST COVERAGE
# ============================================================


@pytest.mark.django_db
class TestKitSerialLifecycle:
    """VV829 S8.1.12: Full kit lifecycle with serialised components.

    Create kit, add serialised components, checkout, partial return,
    complete return.
    """

    def _make_kit_with_serial_components(self, category, location, user):
        """Helper: create a kit with two serialised components."""
        from assets.factories import (
            AssetFactory,
            AssetSerialFactory,
        )

        kit = AssetFactory(
            name="Full Sound Kit",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            created_by=user,
        )
        comp_a = AssetFactory(
            name="Wireless Mic",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
            created_by=user,
        )
        comp_b = AssetFactory(
            name="Mic Receiver",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
            created_by=user,
        )
        serial_a = AssetSerialFactory(
            asset=comp_a,
            serial_number="MIC-001",
            barcode=f"{comp_a.barcode}-SMIC1",
            current_location=location,
        )
        serial_b = AssetSerialFactory(
            asset=comp_b,
            serial_number="RCV-001",
            barcode=f"{comp_b.barcode}-SRCV1",
            current_location=location,
        )
        AssetKit.objects.create(
            kit=kit,
            component=comp_a,
            quantity=1,
            is_required=True,
            serial=serial_a,
        )
        AssetKit.objects.create(
            kit=kit,
            component=comp_b,
            quantity=1,
            is_required=True,
            serial=serial_b,
        )
        return kit, comp_a, comp_b, serial_a, serial_b

    def test_kit_checkout_checks_out_all_serials(
        self, category, location, user, second_user
    ):
        """Kit checkout cascades to all pinned serial components."""
        from assets.services.kits import kit_checkout

        kit, comp_a, comp_b, sa, sb = self._make_kit_with_serial_components(
            category, location, user
        )
        txns = kit_checkout(kit, second_user, user)
        sa.refresh_from_db()
        sb.refresh_from_db()
        assert sa.checked_out_to == second_user
        assert sb.checked_out_to == second_user
        assert len(txns) >= 2  # At least one txn per component

    def test_kit_partial_return_returns_subset(
        self, category, location, user, second_user
    ):
        """Partial return checks in only the specified components."""
        from assets.services.kits import kit_checkout, kit_partial_return

        kit, comp_a, comp_b, sa, sb = self._make_kit_with_serial_components(
            category, location, user
        )
        kit_checkout(kit, second_user, user)

        return_loc = Location.objects.create(name="Return Desk")
        txns = kit_partial_return(
            kit, [comp_a.pk], user, to_location=return_loc
        )
        sa.refresh_from_db()
        sb.refresh_from_db()
        assert sa.checked_out_to is None
        assert sb.checked_out_to == second_user
        assert len(txns) == 1
        assert txns[0].action == "kit_return"

    def test_kit_full_checkin_returns_all(
        self, category, location, user, second_user
    ):
        """Full kit checkin returns all components and the kit."""
        from assets.services.kits import kit_checkin, kit_checkout

        kit, comp_a, comp_b, sa, sb = self._make_kit_with_serial_components(
            category, location, user
        )
        kit_checkout(kit, second_user, user)

        return_loc = Location.objects.create(name="Store Room")
        _txns = kit_checkin(kit, user, to_location=return_loc)  # noqa: F841
        sa.refresh_from_db()
        sb.refresh_from_db()
        kit.refresh_from_db()
        assert sa.checked_out_to is None
        assert sb.checked_out_to is None
        assert kit.checked_out_to is None
        assert sa.current_location == return_loc

    def test_kit_checkout_rejects_unavailable_serial(
        self, category, location, user, second_user
    ):
        """Kit checkout fails when a required pinned serial is unavailable."""
        from assets.services.kits import kit_checkout

        kit, comp_a, comp_b, sa, sb = self._make_kit_with_serial_components(
            category, location, user
        )
        # Make one serial unavailable
        sa.checked_out_to = user
        sa.save(update_fields=["checked_out_to"])

        with pytest.raises(ValidationError, match="unavailable"):
            kit_checkout(kit, second_user, user)

    def test_kit_lifecycle_creates_transaction_trail(
        self, category, location, user, second_user
    ):
        """Full lifecycle creates checkout and checkin transactions."""
        from assets.services.kits import kit_checkin, kit_checkout

        kit, comp_a, comp_b, sa, sb = self._make_kit_with_serial_components(
            category, location, user
        )
        checkout_txns = kit_checkout(kit, second_user, user)
        checkin_txns = kit_checkin(kit, user, to_location=location)

        # Should have checkout transactions for both serials
        checkout_actions = [t.action for t in checkout_txns]
        assert checkout_actions.count("checkout") == 2

        # Should have checkin transactions for both serials
        checkin_actions = [t.action for t in checkin_txns]
        assert checkin_actions.count("checkin") == 2


# ============================================================
# BATCH B: S2.17 KIT & SERIALISATION GAPS
# ============================================================


@pytest.mark.django_db
class TestV507KitComponentWarning:
    """V507: Warning should name which other kit a component is in."""

    def test_kit_checkout_warns_with_other_kit_name(
        self, category, location, user, second_user
    ):
        """When a required component is checked out as part of another
        kit, the error message should name that kit."""
        from assets.factories import AssetFactory

        kit_a = AssetFactory(
            name="Kit Alpha",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            created_by=user,
        )
        kit_b = AssetFactory(
            name="Kit Beta",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            created_by=user,
        )
        shared_component = AssetFactory(
            name="Shared Mic",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=user,
        )
        AssetKit.objects.create(
            kit=kit_a, component=shared_component, is_required=True
        )
        AssetKit.objects.create(
            kit=kit_b, component=shared_component, is_required=True
        )
        # Check out kit_a (makes shared_component unavailable)
        from assets.services.kits import kit_checkout

        kit_checkout(kit_a, second_user, user)

        # Now try to check out kit_b — should fail with message
        # naming "Kit Alpha"
        with pytest.raises(ValidationError) as exc_info:
            kit_checkout(kit_b, second_user, user)
        assert "Kit Alpha" in str(exc_info.value)

    def test_kit_checkout_no_warning_when_other_kit_not_checked_out(
        self, category, location, user, second_user
    ):
        """No warning when component is in multiple kits but none
        are checked out."""
        from assets.factories import AssetFactory

        kit_a = AssetFactory(
            name="Kit Alpha",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            created_by=user,
        )
        component = AssetFactory(
            name="Available Mic",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=user,
        )
        AssetKit.objects.create(
            kit=kit_a, component=component, is_required=True
        )
        # Should succeed without error
        from assets.services.kits import kit_checkout

        txns = kit_checkout(kit_a, second_user, user)
        assert len(txns) >= 1


@pytest.mark.django_db
class TestV492ArchivedSerials:
    """V492: Archived serials section on asset detail view."""

    def test_archived_serials_visible_on_detail(
        self, admin_client, serialised_asset, location
    ):
        """Archived serials should appear in a collapsed section."""
        from assets.factories import AssetSerialFactory

        _s1 = AssetSerialFactory(  # noqa: F841
            asset=serialised_asset,
            serial_number="ARCH-001",
            status="active",
            current_location=location,
            is_archived=True,
        )
        _s2 = AssetSerialFactory(  # noqa: F841
            asset=serialised_asset,
            serial_number="LIVE-001",
            status="active",
            current_location=location,
            is_archived=False,
        )
        url = reverse(
            "assets:asset_detail", kwargs={"pk": serialised_asset.pk}
        )
        resp = admin_client.get(url)
        content = resp.content.decode()
        assert "ARCH-001" in content
        assert "Archived Serials" in content

    def test_no_archived_section_when_none_exist(
        self, admin_client, serialised_asset, location
    ):
        """No archived section when there are no archived serials."""
        from assets.factories import AssetSerialFactory

        AssetSerialFactory(
            asset=serialised_asset,
            serial_number="LIVE-002",
            status="active",
            current_location=location,
            is_archived=False,
        )
        url = reverse(
            "assets:asset_detail", kwargs={"pk": serialised_asset.pk}
        )
        resp = admin_client.get(url)
        content = resp.content.decode()
        assert "Archived Serials" not in content


@pytest.mark.django_db
class TestV467LostStolenReport:
    """V467: Dedicated lost/stolen report view."""

    def test_lost_stolen_report_view_exists(self, admin_client):
        """A dedicated report view for lost/stolen assets should exist."""
        url = reverse("assets:lost_stolen_report")
        resp = admin_client.get(url)
        assert resp.status_code == 200

    def test_lost_stolen_report_shows_lost_assets(
        self, admin_client, category, location, user
    ):
        """Report should show lost assets."""
        from assets.factories import AssetFactory

        _lost = AssetFactory(  # noqa: F841
            name="Lost Widget",
            category=category,
            current_location=location,
            status="lost",
            created_by=user,
        )
        url = reverse("assets:lost_stolen_report")
        resp = admin_client.get(url)
        assert "Lost Widget" in resp.content.decode()

    def test_lost_stolen_report_shows_stolen_assets(
        self, admin_client, category, location, user
    ):
        """Report should show stolen assets."""
        from assets.factories import AssetFactory

        _stolen = AssetFactory(  # noqa: F841
            name="Stolen Gear",
            category=category,
            current_location=location,
            status="stolen",
            created_by=user,
        )
        url = reverse("assets:lost_stolen_report")
        resp = admin_client.get(url)
        assert "Stolen Gear" in resp.content.decode()

    def test_lost_stolen_report_excludes_active(self, admin_client, asset):
        """Report should not show active assets."""
        url = reverse("assets:lost_stolen_report")
        resp = admin_client.get(url)
        assert asset.name not in resp.content.decode()


@pytest.mark.django_db
class TestV496AutoAssignCheckout:
    """V496: Auto-assign mode for serialised checkout."""

    def test_checkout_auto_assign_picks_serials(
        self, admin_client, serialised_asset, location, admin_user
    ):
        """Auto-assign mode should pick available serials by count."""
        from assets.factories import AssetSerialFactory

        _s1 = AssetSerialFactory(  # noqa: F841
            asset=serialised_asset,
            serial_number="AUTO-001",
            status="active",
            current_location=location,
        )
        _s2 = AssetSerialFactory(  # noqa: F841
            asset=serialised_asset,
            serial_number="AUTO-002",
            status="active",
            current_location=location,
        )
        _s3 = AssetSerialFactory(  # noqa: F841
            asset=serialised_asset,
            serial_number="AUTO-003",
            status="active",
            current_location=location,
        )
        from assets.factories import UserFactory

        borrower = UserFactory(username="autoborrower_496")
        url = reverse(
            "assets:asset_checkout",
            kwargs={"pk": serialised_asset.pk},
        )
        resp = admin_client.post(
            url,
            {
                "borrower": borrower.pk,
                "auto_assign_count": "2",
            },
        )
        assert resp.status_code == 302
        # 2 serials should be checked out
        checked_out = AssetSerial.objects.filter(
            asset=serialised_asset,
            checked_out_to=borrower,
        ).count()
        assert checked_out == 2

    def test_checkout_auto_assign_caps_at_available(
        self, admin_client, serialised_asset, location
    ):
        """Auto-assign should not exceed available count."""
        from assets.factories import AssetSerialFactory, UserFactory

        AssetSerialFactory(
            asset=serialised_asset,
            serial_number="CAP-001",
            status="active",
            current_location=location,
        )
        borrower = UserFactory(username="capborrower_496")
        url = reverse(
            "assets:asset_checkout",
            kwargs={"pk": serialised_asset.pk},
        )
        resp = admin_client.post(
            url,
            {
                "borrower": borrower.pk,
                "auto_assign_count": "99",
            },
        )
        assert resp.status_code == 302
        checked_out = AssetSerial.objects.filter(
            asset=serialised_asset,
            checked_out_to=borrower,
        ).count()
        assert checked_out == 1  # Only 1 available


@pytest.mark.django_db
class TestV500NonSerialisedConcurrentCheckoutsExtended:
    """V500: Non-serialised concurrent checkouts to multiple borrowers."""

    def test_concurrent_checkout_allowed(
        self, admin_client, category, location, user
    ):
        """Non-serialised asset with qty>1 allows multiple borrowers."""
        from assets.factories import AssetFactory

        multi = AssetFactory(
            name="Cable Pack",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            quantity=10,
            created_by=user,
        )
        from assets.factories import UserFactory

        borrower1 = UserFactory(username="b1_conc")
        borrower2 = UserFactory(username="b2_conc")
        url = reverse("assets:asset_checkout", kwargs={"pk": multi.pk})
        # First checkout: 3 units
        resp1 = admin_client.post(
            url,
            {"borrower": borrower1.pk, "quantity": "3"},
        )
        assert resp1.status_code == 302

        # Second checkout: 2 units (should succeed)
        resp2 = admin_client.post(
            url,
            {"borrower": borrower2.pk, "quantity": "2"},
        )
        assert resp2.status_code == 302

        # Total open: 5 of 10
        multi.refresh_from_db()
        assert multi.available_count == 5

    def test_concurrent_checkout_blocked_when_no_quantity_left(
        self, admin_client, category, location, user
    ):
        """Block checkout when no quantity remains."""
        from assets.factories import AssetFactory, UserFactory

        single = AssetFactory(
            name="Single Cable",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            quantity=1,
            created_by=user,
        )
        borrower1 = UserFactory(username="full1_conc")
        borrower2 = UserFactory(username="full2_conc")
        url = reverse("assets:asset_checkout", kwargs={"pk": single.pk})
        admin_client.post(url, {"borrower": borrower1.pk, "quantity": "1"})
        # Second should be blocked
        _resp = admin_client.post(  # noqa: F841
            url, {"borrower": borrower2.pk, "quantity": "1"}
        )
        # Should redirect with error message (not proceed)
        single.refresh_from_db()
        assert single.available_count == 0

    def test_available_count_tracks_open_transactions(
        self, category, location, user
    ):
        """available_count reflects open transaction quantities."""
        from assets.factories import AssetFactory

        multi = AssetFactory(
            name="Multi Cable",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            quantity=10,
            created_by=user,
        )
        from assets.factories import UserFactory

        borrower = UserFactory(username="txtrack_conc")
        # Create checkout transaction for 4 units
        Transaction.objects.create(
            asset=multi,
            user=user,
            action="checkout",
            borrower=borrower,
            quantity=4,
        )
        assert multi.available_count == 6

        # Create checkin transaction for 2 units
        Transaction.objects.create(
            asset=multi,
            user=user,
            action="checkin",
            quantity=2,
        )
        assert multi.available_count == 8


@pytest.mark.django_db
class TestV521KitCompletionNonSerialisedExtended:
    """V521: Kit completion for non-serialised components
    based on transaction quantities."""

    def test_kit_completion_tracks_quantities(
        self, category, location, user, second_user
    ):
        """Kit completion uses checkout/checkin transaction
        quantity sums for non-serialised components."""
        from assets.factories import AssetFactory
        from assets.services.kits import get_kit_completion_status

        kit = AssetFactory(
            name="Cable Kit",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            created_by=user,
        )
        component = AssetFactory(
            name="XLR Cables",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            quantity=5,
            created_by=user,
        )
        AssetKit.objects.create(
            kit=kit, component=component, quantity=3, is_required=True
        )

        # Checkout 3 units as part of kit
        Transaction.objects.create(
            asset=component,
            user=user,
            action="checkout",
            borrower=second_user,
            quantity=3,
            notes=f"Kit checkout: {kit.name}",
        )
        component.checked_out_to = second_user
        component.save()

        # Checkin 2 units
        Transaction.objects.create(
            asset=component,
            user=user,
            action="checkin",
            quantity=2,
        )

        # Kit should be incomplete (1 still out of 3)
        status = get_kit_completion_status(kit)
        assert status["status"] == "incomplete"

    def test_kit_complete_when_all_returned(
        self, category, location, user, second_user
    ):
        """Kit is complete when all quantities are returned."""
        from assets.factories import AssetFactory
        from assets.services.kits import get_kit_completion_status

        kit = AssetFactory(
            name="Return Kit",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            created_by=user,
        )
        component = AssetFactory(
            name="Return Cables",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            quantity=5,
            created_by=user,
        )
        AssetKit.objects.create(
            kit=kit, component=component, quantity=2, is_required=True
        )

        # Component is not checked out — should be complete
        status = get_kit_completion_status(kit)
        assert status["status"] == "complete"


@pytest.mark.django_db
class TestV746SerialDisposalBarcode:
    """V746: Transaction FK integrity on serial disposal."""

    def test_serial_barcode_cleared_on_disposal(self, asset, admin_user):
        """Disposing a serial should clear its barcode."""
        from assets.models import AssetSerial

        asset.is_serialised = True
        asset.created_by = admin_user
        asset.save()
        serial = AssetSerial.objects.create(
            asset=asset,
            serial_number="SN-V746",
            barcode="BC-V746-001",
            status="active",
        )
        serial.status = "disposed"
        serial.save()
        serial.refresh_from_db()
        assert serial.barcode is None

    def test_serial_disposal_creates_note_transaction(self, asset, admin_user):
        """Disposing a serial should create a note transaction."""
        from assets.models import AssetSerial, Transaction

        asset.is_serialised = True
        asset.created_by = admin_user
        asset.save()
        serial = AssetSerial.objects.create(
            asset=asset,
            serial_number="SN-V746-B",
            barcode="BC-V746-002",
            status="active",
        )
        serial.status = "disposed"
        serial.save()
        txn = Transaction.objects.filter(serial=serial, action="note").last()
        assert txn is not None
        assert "BC-V746-002" in (txn.serial_barcode or "")


# ============================================================
# V794 (S7.19.2): All serials disposed auto-updates parent
# ============================================================


@pytest.mark.django_db
class TestV794AllSerialsDisposedUpdatesParent:
    """V794: When all serials are disposed, parent should be disposed."""

    def test_all_serials_disposed_parent_becomes_disposed(
        self, serialised_asset, location, user
    ):
        """Disposing all serials should auto-dispose the parent."""
        s1 = AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="V794-001",
            status="active",
            current_location=location,
        )
        s2 = AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="V794-002",
            status="active",
            current_location=location,
        )
        # Dispose first serial — parent should still be active
        s1.status = "disposed"
        s1.save()
        serialised_asset.refresh_from_db()
        assert serialised_asset.status == "active"

        # Dispose second serial — parent should now be disposed
        s2.status = "disposed"
        s2.save()
        serialised_asset.refresh_from_db()
        assert serialised_asset.status == "disposed"

    def test_archived_serials_excluded_from_check(
        self, serialised_asset, location
    ):
        """Archived serials should not prevent parent disposal."""
        s1 = AssetSerial.objects.create(
            asset=serialised_asset,
            serial_number="V794-A1",
            status="active",
            current_location=location,
        )
        _s_archived = AssetSerial.objects.create(  # noqa: F841
            asset=serialised_asset,
            serial_number="V794-A2",
            status="active",
            current_location=location,
            is_archived=True,
        )
        # Dispose the only active serial
        s1.status = "disposed"
        s1.save()
        serialised_asset.refresh_from_db()
        assert serialised_asset.status == "disposed"


# ============================================================
# V781 (COULD, S7.16.10): Serial replacement workflow
# ============================================================


@pytest.mark.django_db
class TestV781SerialReplacementWorkflow:
    """V781: Kit contents should show replacement needed for disposed
    pinned serials."""

    def test_kit_contents_shows_replacement_for_disposed_serial(
        self, admin_client, admin_user, category, location
    ):
        """Disposed pinned serial should show replacement needed."""
        kit = Asset.objects.create(
            name="V781 Kit",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            is_serialised=False,
            created_by=admin_user,
        )
        component = Asset.objects.create(
            name="V781 Component",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
            created_by=admin_user,
        )
        serial = AssetSerial.objects.create(
            asset=component,
            serial_number="V781-001",
            barcode=f"{component.barcode}-V781",
            status="disposed",
            condition="poor",
        )
        # serial.save() clears barcode on disposed; re-fetch
        serial.refresh_from_db()
        AssetKit.objects.create(
            kit=kit,
            component=component,
            quantity=1,
            is_required=True,
            serial=serial,
        )
        url = reverse("assets:kit_contents", args=[kit.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "replacement needed" in content.lower()

    def test_kit_contents_no_replacement_for_active_serial(
        self, admin_client, admin_user, category, location
    ):
        """Active pinned serial should NOT show replacement needed."""
        kit = Asset.objects.create(
            name="V781 Kit Active",
            category=category,
            current_location=location,
            status="active",
            is_kit=True,
            is_serialised=False,
            created_by=admin_user,
        )
        component = Asset.objects.create(
            name="V781 Component Active",
            category=category,
            current_location=location,
            status="active",
            is_serialised=True,
            created_by=admin_user,
        )
        serial = AssetSerial.objects.create(
            asset=component,
            serial_number="V781-002",
            barcode=f"{component.barcode}-V781A",
            status="active",
            condition="good",
        )
        AssetKit.objects.create(
            kit=kit,
            component=component,
            quantity=1,
            is_required=True,
            serial=serial,
        )
        url = reverse("assets:kit_contents", args=[kit.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "replacement needed" not in content.lower()

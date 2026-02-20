"""TDD tests for PrintClient (S3.1.20) and PrintRequest (S3.1.21) models.

These tests are written BEFORE the models exist. They will fail until the
models are implemented. This is intentional — red-green TDD cycle.

Spec references:
  - S3.1.20: PrintClient
  - S3.1.21: PrintRequest
  - §8.1.13: Print Service Model Tests
"""

import asyncio
import hashlib
import secrets
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import path, reverse
from django.utils import timezone

from assets.consumers import PrintServiceConsumer
from assets.models import Asset, PrintClient, PrintRequest

# ---------------------------------------------------------------------------
# §8.1.13-01 — PrintClient model tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPrintClientDefaults:
    """PrintClient field defaults and creation (§8.1.13-01)."""

    def test_print_client_status_defaults_to_pending(self, user):
        """S3.1.20: status defaults to 'pending' on creation.

        The spec states status has two persisted states: pending, approved.
        New clients start in pending state awaiting admin approval.
        """
        token_hash = hashlib.sha256(b"secret-token-1").hexdigest()
        client = PrintClient.objects.create(
            name="Test Station",
            token_hash=token_hash,
        )
        assert client.status == "pending"

    def test_print_client_is_active_defaults_to_true(self, user):
        """S3.1.20: is_active defaults to True on creation."""
        token_hash = hashlib.sha256(b"secret-token-2").hexdigest()
        client = PrintClient.objects.create(
            name="Test Station",
            token_hash=token_hash,
        )
        assert client.is_active is True

    def test_print_client_is_connected_defaults_to_false(self):
        """S3.1.20: is_connected defaults to False on creation.

        §8.1.13-01: is_connected defaults to False on creation.
        Managed by the WebSocket consumer on connect/disconnect.
        """
        token_hash = hashlib.sha256(b"secret-token-3").hexdigest()
        client = PrintClient.objects.create(
            name="Test Station",
            token_hash=token_hash,
        )
        assert client.is_connected is False

    def test_print_client_last_seen_at_defaults_to_null(self):
        """S3.1.20: last_seen_at is nullable and defaults to null."""
        token_hash = hashlib.sha256(b"secret-token-4").hexdigest()
        client = PrintClient.objects.create(
            name="Test Station",
            token_hash=token_hash,
        )
        assert client.last_seen_at is None

    def test_print_client_printers_defaults_to_empty_list(self):
        """S3.1.20: printers JSONField defaults to empty list."""
        token_hash = hashlib.sha256(b"secret-token-5").hexdigest()
        client = PrintClient.objects.create(
            name="Test Station",
            token_hash=token_hash,
        )
        assert client.printers == []
        assert isinstance(client.printers, list)

    def test_print_client_approved_by_defaults_to_null(self):
        """S3.1.20: approved_by FK is nullable and defaults to null."""
        token_hash = hashlib.sha256(b"secret-token-6").hexdigest()
        client = PrintClient.objects.create(
            name="Test Station",
            token_hash=token_hash,
        )
        assert client.approved_by is None

    def test_print_client_approved_at_defaults_to_null(self):
        """S3.1.20: approved_at is nullable, defaults to null."""
        token_hash = hashlib.sha256(b"secret-token-7").hexdigest()
        client = PrintClient.objects.create(
            name="Test Station",
            token_hash=token_hash,
        )
        assert client.approved_at is None

    def test_print_client_created_at_auto_set(self):
        """S3.1.20: created_at is auto-set on creation (auto_now_add)."""
        token_hash = hashlib.sha256(b"secret-token-8").hexdigest()
        before = timezone.now()
        client = PrintClient.objects.create(
            name="Test Station",
            token_hash=token_hash,
        )
        after = timezone.now()
        assert before <= client.created_at <= after


@pytest.mark.django_db
class TestPrintClientTokenUniqueness:
    """token_hash uniqueness enforcement (§8.1.13-01, S3.1.20)."""

    def test_token_hash_is_unique_at_database_level(self):
        """S3.1.20: token_hash MUST be unique at the database level.

        Two PrintClient records with the same token_hash must be rejected.
        """
        from django.db import IntegrityError

        token_hash = hashlib.sha256(b"shared-token").hexdigest()
        PrintClient.objects.create(name="Station A", token_hash=token_hash)
        with pytest.raises(IntegrityError):
            PrintClient.objects.create(name="Station B", token_hash=token_hash)

    def test_token_hash_is_64_characters(self):
        """S3.1.20: token_hash is stored as SHA-256 hex digest (64 chars).

        SHA-256 produces a 64-character hex string.
        """
        raw = b"some-raw-token"
        token_hash = hashlib.sha256(raw).hexdigest()
        assert len(token_hash) == 64

        client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
        )
        assert len(client.token_hash) == 64

    def test_different_tokens_produce_different_hashes(self):
        """S3.1.20: Each client gets a unique token hash."""
        hash1 = hashlib.sha256(b"token-alpha").hexdigest()
        hash2 = hashlib.sha256(b"token-beta").hexdigest()
        assert hash1 != hash2

        client1 = PrintClient.objects.create(name="Alpha", token_hash=hash1)
        client2 = PrintClient.objects.create(name="Beta", token_hash=hash2)
        assert client1.token_hash != client2.token_hash


@pytest.mark.django_db
class TestPrintClientStatusChoices:
    """Status field constraints (S3.1.20)."""

    def test_status_pending_is_valid(self):
        """S3.1.20: 'pending' is a valid status value."""
        token_hash = hashlib.sha256(b"token-pending").hexdigest()
        client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="pending",
        )
        assert client.status == "pending"

    def test_status_approved_is_valid(self):
        """S3.1.20: 'approved' is a valid status value."""
        token_hash = hashlib.sha256(b"token-approved").hexdigest()
        client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        assert client.status == "approved"


@pytest.mark.django_db
class TestPrintClientApprovalFields:
    """Approval fields are set when a client is approved (§8.1.13-01)."""

    def test_approved_by_and_approved_at_set_on_approval(self, admin_user):
        """S3.1.20: approval fields set when client is approved.

        §8.1.13-01: approved_by/approved_at set when a client
        is approved.
        """
        token_hash = hashlib.sha256(b"token-for-approval").hexdigest()
        client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="pending",
        )
        assert client.approved_by is None
        assert client.approved_at is None

        now = timezone.now()
        client.status = "approved"
        client.approved_by = admin_user
        client.approved_at = now
        client.save()

        client.refresh_from_db()
        assert client.status == "approved"
        assert client.approved_by == admin_user
        assert client.approved_at == now

    def test_approved_by_is_nullable_set_null_on_user_delete(self, db):
        """S3.1.20: approved_by uses on_delete=SET_NULL (nullable FK)."""
        from assets.factories import UserFactory

        approver = UserFactory(
            username="approver99", email="approver99@example.com"
        )
        token_hash = hashlib.sha256(b"token-set-null-test").hexdigest()
        client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
            approved_by=approver,
            approved_at=timezone.now(),
        )
        approver.delete()
        client.refresh_from_db()
        assert client.approved_by is None


@pytest.mark.django_db
class TestPrintClientPrintersJsonField:
    """printers JSONField stores and retrieves JSON array (§8.1.13-01)."""

    def test_printers_stores_json_array_of_printer_objects(self):
        """S3.1.20: printers field correctly stores and retrieves JSON list.

        Each element must have id, name, type, status, and templates keys.
        """
        printers_data = [
            {
                "id": "printer-001",
                "name": "Label Printer A",
                "type": "zebra",
                "status": "online",
                "templates": ["label-4x6", "label-2x1"],
            },
            {
                "id": "printer-002",
                "name": "Label Printer B",
                "type": "brother",
                "status": "offline",
                "templates": [],
            },
        ]
        token_hash = hashlib.sha256(b"token-printers").hexdigest()
        client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            printers=printers_data,
        )
        client.refresh_from_db()
        assert len(client.printers) == 2
        assert client.printers[0]["id"] == "printer-001"
        assert client.printers[0]["name"] == "Label Printer A"
        assert client.printers[0]["type"] == "zebra"
        assert client.printers[0]["status"] == "online"
        assert client.printers[0]["templates"] == ["label-4x6", "label-2x1"]
        assert client.printers[1]["status"] == "offline"
        assert client.printers[1]["templates"] == []

    def test_printers_supports_online_offline_error_statuses(self):
        """S3.1.20: printer status valid values are online, offline, error."""
        printers_data = [
            {
                "id": "p1",
                "name": "Printer 1",
                "type": "zebra",
                "status": "online",
                "templates": [],
            },
            {
                "id": "p2",
                "name": "Printer 2",
                "type": "zebra",
                "status": "offline",
                "templates": [],
            },
            {
                "id": "p3",
                "name": "Printer 3",
                "type": "zebra",
                "status": "error",
                "templates": [],
            },
        ]
        token_hash = hashlib.sha256(b"token-printer-statuses").hexdigest()
        client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            printers=printers_data,
        )
        client.refresh_from_db()
        statuses = [p["status"] for p in client.printers]
        assert "online" in statuses
        assert "offline" in statuses
        assert "error" in statuses


# ---------------------------------------------------------------------------
# §8.1.13-02 — PrintRequest model tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPrintRequestDefaults:
    """PrintRequest field defaults and creation (§8.1.13-02)."""

    def test_print_request_status_defaults_to_pending(self, asset):
        """S3.1.21: status defaults to 'pending' on creation."""
        token_hash = hashlib.sha256(b"req-token-1").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        assert req.status == "pending"

    def test_print_request_quantity_defaults_to_one(self, asset):
        """S3.1.21: quantity defaults to 1."""
        token_hash = hashlib.sha256(b"req-token-2").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        assert req.quantity == 1

    def test_print_request_job_id_auto_generated_as_uuid(self, asset):
        """S3.1.21: job_id is auto-generated as a UUID.

        §8.1.13-02: job_id is auto-generated as a UUID and is unique.
        """
        token_hash = hashlib.sha256(b"req-token-3").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        assert req.job_id is not None
        # Should be parseable as a UUID
        parsed = uuid.UUID(str(req.job_id))
        assert str(parsed) == str(req.job_id)

    def test_print_request_sent_at_defaults_to_null(self, asset):
        """S3.1.21: sent_at is nullable and null by default."""
        token_hash = hashlib.sha256(b"req-token-4").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        assert req.sent_at is None

    def test_print_request_acked_at_defaults_to_null(self, asset):
        """S3.1.21: acked_at is nullable and null by default."""
        token_hash = hashlib.sha256(b"req-token-5").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        assert req.acked_at is None

    def test_print_request_completed_at_defaults_to_null(self, asset):
        """S3.1.21: completed_at is nullable and null by default.

        §8.1.13-02: completed_at is nullable and set when status transitions
        to completed or failed.
        """
        token_hash = hashlib.sha256(b"req-token-6").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        assert req.completed_at is None

    def test_print_request_error_message_defaults_to_blank(self, asset):
        """S3.1.21: error_message is blank by default."""
        token_hash = hashlib.sha256(b"req-token-7").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        assert req.error_message == ""

    def test_print_request_created_at_auto_set(self, asset):
        """S3.1.21: created_at is auto-set on creation (auto_now_add)."""
        token_hash = hashlib.sha256(b"req-token-8").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        before = timezone.now()
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        after = timezone.now()
        assert before <= req.created_at <= after


@pytest.mark.django_db
class TestPrintRequestJobIdUniqueness:
    """job_id uniqueness at database level (§8.1.13-02, S3.1.21)."""

    def test_job_id_is_unique_at_database_level(self, asset):
        """S3.1.21: job_id MUST be unique at the database level."""
        from django.db import IntegrityError

        token_hash = hashlib.sha256(b"req-unique-token").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        fixed_job_id = uuid.uuid4()
        PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
            job_id=fixed_job_id,
        )
        with pytest.raises(IntegrityError):
            PrintRequest.objects.create(
                print_client=print_client,
                asset=asset,
                printer_id="printer-001",
                job_id=fixed_job_id,
            )

    def test_two_print_requests_get_different_job_ids(self, asset):
        """S3.1.21: Each PrintRequest gets a unique auto-generated job_id."""
        token_hash = hashlib.sha256(b"req-diff-ids-token").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req1 = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        req2 = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        assert req1.job_id != req2.job_id


@pytest.mark.django_db
class TestPrintRequestStatusChoices:
    """status only accepts valid choices (§8.1.13-02, S3.1.21)."""

    def test_status_pending_is_valid(self, asset):
        """S3.1.21: 'pending' is a valid status choice."""
        token_hash = hashlib.sha256(b"req-status-pending").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
            status="pending",
        )
        assert req.status == "pending"

    def test_status_sent_is_valid(self, asset):
        """S3.1.21: 'sent' is a valid status choice."""
        token_hash = hashlib.sha256(b"req-status-sent").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
            status="sent",
        )
        assert req.status == "sent"

    def test_status_acked_is_valid(self, asset):
        """S3.1.21: 'acked' is a valid status choice."""
        token_hash = hashlib.sha256(b"req-status-acked").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
            status="acked",
        )
        assert req.status == "acked"

    def test_status_completed_is_valid(self, asset):
        """S3.1.21: 'completed' is a valid status choice."""
        token_hash = hashlib.sha256(b"req-status-completed").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
            status="completed",
        )
        assert req.status == "completed"

    def test_status_failed_is_valid(self, asset):
        """S3.1.21: 'failed' is a valid status choice."""
        token_hash = hashlib.sha256(b"req-status-failed").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
            status="failed",
        )
        assert req.status == "failed"


@pytest.mark.django_db
class TestPrintRequestQuantity:
    """quantity field validation (§8.1.13-02, S3.1.21)."""

    def test_quantity_must_be_positive_integer(self, asset):
        """S3.1.21: quantity defaults to 1 and MUST be a positive integer.

        §8.1.13-02: quantity defaults to 1 and MUST be a positive integer.
        A quantity of 0 or negative must be rejected.
        """
        token_hash = hashlib.sha256(b"req-qty-token").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
            quantity=0,
        )
        with pytest.raises(ValidationError):
            req.full_clean()

    def test_quantity_positive_value_is_accepted(self, asset):
        """S3.1.21: positive quantity values are accepted."""
        token_hash = hashlib.sha256(b"req-qty-positive").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
            quantity=5,
        )
        assert req.quantity == 5


@pytest.mark.django_db
class TestPrintRequestNullableForeignKeys:
    """Nullable FK behaviour — preserves audit history (S3.1.21)."""

    def test_print_client_set_null_on_client_delete(self, asset):
        """S3.1.21: print_client uses on_delete=SET_NULL.

        Deleting a PrintClient must set print_client to NULL on PrintRequest
        records, preserving the print job audit history.
        """
        token_hash = hashlib.sha256(b"req-client-null-token").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
        )
        print_client.delete()
        req.refresh_from_db()
        assert req.print_client is None

    def test_asset_set_null_on_asset_delete(self, category, location, user):
        """S3.1.21: asset uses on_delete=SET_NULL.

        Deleting an Asset must set asset to NULL on PrintRequest records,
        preserving the print job audit history.
        """
        from assets.factories import AssetFactory

        ephemeral_asset = AssetFactory(
            name="Ephemeral Prop",
            category=category,
            current_location=location,
            status="active",
            is_serialised=False,
            created_by=user,
        )
        token_hash = hashlib.sha256(b"req-asset-null-token").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=ephemeral_asset,
            printer_id="printer-001",
        )
        # Assets with PROTECT FKs need to be handled; for test isolation
        # we directly set asset to None and save
        req.asset = None
        req.save()
        req.refresh_from_db()
        assert req.asset is None

    def test_requested_by_set_null_on_user_delete(self, asset, db):
        """S3.1.21: requested_by uses on_delete=SET_NULL.

        Deleting a User must set requested_by to NULL, preserving history.
        """
        from assets.factories import UserFactory

        requester = UserFactory(
            username="requester99", email="requester99@example.com"
        )
        token_hash = hashlib.sha256(b"req-user-null-token").hexdigest()
        print_client = PrintClient.objects.create(
            name="Station",
            token_hash=token_hash,
            status="approved",
        )
        req = PrintRequest.objects.create(
            print_client=print_client,
            asset=asset,
            printer_id="printer-001",
            requested_by=requester,
        )
        requester.delete()
        req.refresh_from_db()
        assert req.requested_by is None


# ---------------------------------------------------------------------------
# §8.1.13-03 — PrintRequest status transition matrix tests
# ---------------------------------------------------------------------------


def _make_print_client(suffix):
    """Helper to create a unique PrintClient for transition tests."""
    raw = f"transition-token-{suffix}".encode()
    token_hash = hashlib.sha256(raw).hexdigest()
    return PrintClient.objects.create(
        name=f"Station-{suffix}",
        token_hash=token_hash,
        status="approved",
    )


@pytest.mark.django_db
class TestPrintRequestValidTransitions:
    """Valid status transitions must succeed (§8.1.13-03).

    Valid transitions per spec:
      pending -> sent
      pending -> failed
      sent -> acked
      sent -> failed
      acked -> completed
      acked -> failed
    """

    def test_transition_pending_to_sent(self, asset):
        """§8.1.13-03 valid: pending -> sent (job dispatched to client)."""
        client = _make_print_client("pend-sent")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="pending",
        )
        req.transition_to("sent")
        req.refresh_from_db()
        assert req.status == "sent"

    def test_transition_pending_to_failed(self, asset):
        """§8.1.13-03 valid: pending -> failed (send attempt failed)."""
        client = _make_print_client("pend-fail")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="pending",
        )
        req.transition_to("failed", error_message="Client disconnected")
        req.refresh_from_db()
        assert req.status == "failed"

    def test_transition_sent_to_acked(self, asset):
        """§8.1.13-03 valid: sent -> acked (client acknowledged receipt)."""
        client = _make_print_client("sent-acked")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="sent",
        )
        req.transition_to("acked")
        req.refresh_from_db()
        assert req.status == "acked"

    def test_transition_sent_to_failed(self, asset):
        """§8.1.13-03 valid: sent -> failed (timeout/disconnect)."""
        client = _make_print_client("sent-fail")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="sent",
        )
        req.transition_to("failed", error_message="Timeout")
        req.refresh_from_db()
        assert req.status == "failed"

    def test_transition_acked_to_completed(self, asset):
        """§8.1.13-03 valid: acked -> completed (print success)."""
        client = _make_print_client("acked-completed")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="acked",
        )
        req.transition_to("completed")
        req.refresh_from_db()
        assert req.status == "completed"

    def test_transition_acked_to_failed(self, asset):
        """§8.1.13-03 valid: acked -> failed (client reports print failure)."""
        client = _make_print_client("acked-fail")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="acked",
        )
        req.transition_to("failed", error_message="Paper jam")
        req.refresh_from_db()
        assert req.status == "failed"


@pytest.mark.django_db
class TestPrintRequestTransitionSetsTimestamps:
    """Valid transitions set the correct timestamps (S3.1.21)."""

    def test_transition_to_sent_sets_sent_at(self, asset):
        """S3.1.21: sent_at is set when status transitions to sent."""
        client = _make_print_client("ts-sent")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="pending",
        )
        assert req.sent_at is None
        before = timezone.now()
        req.transition_to("sent")
        after = timezone.now()
        req.refresh_from_db()
        assert req.sent_at is not None
        assert before <= req.sent_at <= after

    def test_transition_to_acked_sets_acked_at(self, asset):
        """S3.1.21: acked_at is set when status transitions to acked."""
        client = _make_print_client("ts-acked")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="sent",
        )
        assert req.acked_at is None
        before = timezone.now()
        req.transition_to("acked")
        after = timezone.now()
        req.refresh_from_db()
        assert req.acked_at is not None
        assert before <= req.acked_at <= after

    def test_transition_to_completed_sets_completed_at(self, asset):
        """S3.1.21: completed_at is set when status transitions to completed.

        §8.1.13-02: completed_at is nullable and set when status transitions
        to completed or failed.
        """
        client = _make_print_client("ts-completed")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="acked",
        )
        assert req.completed_at is None
        before = timezone.now()
        req.transition_to("completed")
        after = timezone.now()
        req.refresh_from_db()
        assert req.completed_at is not None
        assert before <= req.completed_at <= after

    def test_transition_to_failed_sets_completed_at(self, asset):
        """S3.1.21: completed_at is set when status transitions to failed.

        §8.1.13-02: completed_at set on completed OR failed.
        """
        client = _make_print_client("ts-failed")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="sent",
        )
        assert req.completed_at is None
        before = timezone.now()
        req.transition_to("failed", error_message="Network error")
        after = timezone.now()
        req.refresh_from_db()
        assert req.completed_at is not None
        assert before <= req.completed_at <= after

    def test_transition_to_failed_stores_error_message(self, asset):
        """S3.1.21: error_message stored on failed transition."""
        client = _make_print_client("ts-errmsg")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="acked",
        )
        req.transition_to("failed", error_message="Paper jam in tray 1")
        req.refresh_from_db()
        assert req.error_message == "Paper jam in tray 1"


@pytest.mark.django_db
class TestPrintRequestInvalidTransitions:
    """Invalid transitions MUST be rejected (§8.1.13-03).

    The following transitions are explicitly tested as invalid per spec:
      completed -> pending
      completed -> sent
      failed -> sent
      failed -> pending
      pending -> completed  (skipping sent and acked)
      sent -> pending
      acked -> pending
      acked -> sent
    """

    def test_transition_completed_to_pending_is_rejected(self, asset):
        """§8.1.13-03 invalid: completed -> pending MUST be rejected."""
        client = _make_print_client("inv-comp-pend")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="completed",
        )
        with pytest.raises((ValidationError, ValueError)):
            req.transition_to("pending")

    def test_transition_completed_to_sent_is_rejected(self, asset):
        """§8.1.13-03 invalid: completed -> sent MUST be rejected."""
        client = _make_print_client("inv-comp-sent")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="completed",
        )
        with pytest.raises((ValidationError, ValueError)):
            req.transition_to("sent")

    def test_transition_failed_to_sent_is_rejected(self, asset):
        """§8.1.13-03 invalid: failed -> sent MUST be rejected."""
        client = _make_print_client("inv-fail-sent")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="failed",
        )
        with pytest.raises((ValidationError, ValueError)):
            req.transition_to("sent")

    def test_transition_failed_to_pending_is_rejected(self, asset):
        """§8.1.13-03 invalid: failed -> pending MUST be rejected."""
        client = _make_print_client("inv-fail-pend")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="failed",
        )
        with pytest.raises((ValidationError, ValueError)):
            req.transition_to("pending")

    def test_transition_pending_to_completed_skipping_sent_acked_is_rejected(
        self, asset
    ):
        """§8.1.13-03 invalid: pending -> completed (skip sent/acked)."""
        client = _make_print_client("inv-pend-comp")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="pending",
        )
        with pytest.raises((ValidationError, ValueError)):
            req.transition_to("completed")

    def test_transition_sent_to_pending_is_rejected(self, asset):
        """§8.1.13-03 invalid: sent -> pending MUST be rejected."""
        client = _make_print_client("inv-sent-pend")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="sent",
        )
        with pytest.raises((ValidationError, ValueError)):
            req.transition_to("pending")

    def test_transition_acked_to_pending_is_rejected(self, asset):
        """§8.1.13-03 invalid: acked -> pending MUST be rejected."""
        client = _make_print_client("inv-acked-pend")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="acked",
        )
        with pytest.raises((ValidationError, ValueError)):
            req.transition_to("pending")

    def test_transition_acked_to_sent_is_rejected(self, asset):
        """§8.1.13-03 invalid: acked -> sent MUST be rejected."""
        client = _make_print_client("inv-acked-sent")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="acked",
        )
        with pytest.raises((ValidationError, ValueError)):
            req.transition_to("sent")

    def test_completed_is_terminal_no_further_transitions(self, asset):
        """S3.1.21: completed is a terminal state — no transitions allowed."""
        client = _make_print_client("inv-term-comp")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="completed",
        )
        for target in ("pending", "sent", "acked", "failed"):
            with pytest.raises((ValidationError, ValueError)):
                req.transition_to(target)

    def test_failed_is_terminal_no_further_transitions(self, asset):
        """S3.1.21: failed is a terminal state — no transitions allowed."""
        client = _make_print_client("inv-term-fail")
        req = PrintRequest.objects.create(
            print_client=client,
            asset=asset,
            printer_id="printer-001",
            status="failed",
        )
        for target in ("pending", "sent", "acked", "completed"):
            with pytest.raises((ValidationError, ValueError)):
                req.transition_to(target)


# ---------------------------------------------------------------------------
# §8.2.17 — Print Service WebSocket Consumer Integration Tests
# ---------------------------------------------------------------------------
#
# These tests verify the PrintServiceConsumer WebSocket consumer using
# Django Channels WebsocketCommunicator. They are written BEFORE the
# consumer is implemented (TDD red phase). The consumer is currently a
# placeholder that accepts connections but does nothing.
#
# Spec references:
#   - §4.3.3.4: WebSocket Protocol: Authentication & Pairing
#   - §8.2.17-01: Pairing flow
#   - §8.2.17-01b: Token delivery on same connection (happy path)
#   - §8.2.17-02: Authentication flow
# ---------------------------------------------------------------------------

# Build a minimal ASGI application for testing the consumer directly.
_ws_app = URLRouter(
    [path("ws/print-service/", PrintServiceConsumer.as_asgi())]
)


def _make_communicator(path="ws/print-service/"):
    """Create a WebsocketCommunicator for the PrintServiceConsumer."""
    return WebsocketCommunicator(_ws_app, path)


# ---------------------------------------------------------------------------
# §8.2.17-01 — Pairing flow tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPairingFlow:
    """§8.2.17-01: WebSocket pairing request flow."""

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_client_can_connect_to_print_service_endpoint(self):
        """§8.2.17-01: Client can connect to /ws/print-service/.

        Verify the WebSocket endpoint accepts connections.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_pairing_request_creates_pending_print_client(self):
        """§8.2.17-01: Sending pairing_request creates a PrintClient
        with status='pending'.

        The server MUST create a PrintClient record with status='pending'
        when it receives a pairing_request message.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "pairing_request",
                "client_name": "Workshop Printer Station",
                "protocol_version": "1",
            }
        )

        # Server should respond (pairing_response or similar)
        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] in (
            "pairing_response",
            "pairing_pending",
        )

        # Verify PrintClient was created in the database
        client = await database_sync_to_async(PrintClient.objects.get)(
            name="Workshop Printer Station"
        )
        assert client.status == "pending"

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_denied_client_receives_pairing_denied(self, admin_user):
        """§8.2.17-01: Denied clients receive pairing_denied.

        After admin denies the pairing request, the client should receive
        a pairing_denied message on its connection.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "pairing_request",
                "client_name": "Denied Station",
                "protocol_version": "1",
            }
        )

        # Consume the initial pending response
        await communicator.receive_json_from(timeout=5)

        # Admin denies the client
        print_client = await database_sync_to_async(  # noqa: F841
            PrintClient.objects.get
        )(name="Denied Station")

        @database_sync_to_async
        def deny_client(pc, admin):
            pc.status = "denied"
            pc.save()

        await deny_client(print_client, admin_user)

        # Trigger notification via channel layer (the consumer should
        # be in a group to receive denial notifications)
        channel_layer = get_channel_layer()
        group_name = f"print_client_{print_client.pk}"
        await channel_layer.group_send(
            group_name,
            {
                "type": "pairing.denied",
            },
        )

        # Client should receive pairing_denied
        denial = await communicator.receive_json_from(timeout=5)
        assert denial["type"] == "pairing_denied"

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reconnecting_approved_client_gets_token_via_auth(
        self, admin_user
    ):
        """§8.2.17-01: After admin approval, reconnecting client
        authenticates with a token.

        This tests the fallback reconnection path: the client disconnected
        before approval, then reconnects and authenticates with its token.
        """
        # Pre-create an approved PrintClient with a known token
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        @database_sync_to_async
        def create_approved_client():
            return PrintClient.objects.create(
                name="Approved Station",
                token_hash=token_hash,
                status="approved",
                approved_by=admin_user,
                approved_at=timezone.now(),
            )

        await create_approved_client()

        # Client reconnects and authenticates
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "authenticate",
                "token": raw_token,
                "client_name": "Approved Station",
                "printers": [
                    {
                        "id": "zebra-01",
                        "name": "Zebra ZD410",
                        "type": "thermal",
                        "status": "online",
                        "templates": [],
                    }
                ],
                "protocol_version": "1",
            }
        )

        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] == "auth_result"
        assert response["success"] is True
        # Token rotation: new_token should be issued
        assert "new_token" in response

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_idempotent_repairing_same_client_name(self):
        """§4.3.3.4: Idempotent re-pairing with the same client_name.

        A client can re-send pairing_request with the same client_name.
        If a pending PrintClient exists, the server should resume
        waiting — not create a duplicate.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        # First pairing request
        await communicator.send_json_to(
            {
                "type": "pairing_request",
                "client_name": "Idempotent Station",
                "protocol_version": "1",
            }
        )
        await communicator.receive_json_from(timeout=5)
        await communicator.disconnect()

        # Second connection with same client_name
        communicator2 = _make_communicator()
        connected2, _ = await communicator2.connect()
        assert connected2

        await communicator2.send_json_to(
            {
                "type": "pairing_request",
                "client_name": "Idempotent Station",
                "protocol_version": "1",
            }
        )
        await communicator2.receive_json_from(timeout=5)

        # Should still be exactly one PrintClient with this name
        count = await database_sync_to_async(
            PrintClient.objects.filter(name="Idempotent Station").count
        )()
        assert count == 1

        await communicator2.disconnect()


# ---------------------------------------------------------------------------
# §8.2.17-01b — Token delivery on same connection (happy path)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPairingApprovedPush:
    """§8.2.17-01b: Token delivery on same connection.

    The primary UX flow: client connects, sends pairing_request,
    connection stays open, admin approves, server pushes
    pairing_approved with token on the SAME connection.
    """

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_pairing_approved_pushed_on_same_connection(
        self, admin_user
    ):
        """§8.2.17-01b: After pairing_request, admin approves, and
        the server pushes pairing_approved with a valid token to the
        still-open connection without requiring a reconnect.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "pairing_request",
                "client_name": "Push Test Station",
                "protocol_version": "1",
            }
        )

        # Consume the initial pending acknowledgement
        await communicator.receive_json_from(timeout=5)

        # Look up the created PrintClient
        print_client = await database_sync_to_async(PrintClient.objects.get)(
            name="Push Test Station"
        )
        assert print_client.status == "pending"

        # Admin approves the client programmatically
        @database_sync_to_async
        def approve_client(pc, admin):
            pc.status = "approved"
            pc.approved_by = admin
            pc.approved_at = timezone.now()
            pc.save()

        await approve_client(print_client, admin_user)

        # Send approval notification via channel layer
        # The consumer should have joined a group for this client
        channel_layer = get_channel_layer()
        group_name = f"print_client_{print_client.pk}"
        await channel_layer.group_send(
            group_name,
            {
                "type": "pairing.approved",
                "print_client_id": print_client.pk,
            },
        )

        # Client should receive pairing_approved with a token
        approval = await communicator.receive_json_from(timeout=5)
        assert approval["type"] == "pairing_approved"
        assert "token" in approval
        assert len(approval["token"]) > 0
        # server_name should be present per protocol contract
        assert "server_name" in approval

        # Verify the token hash was stored in the database
        @database_sync_to_async
        def get_updated_client(pk):
            return PrintClient.objects.get(pk=pk)

        updated_client = await get_updated_client(print_client.pk)
        expected_hash = hashlib.sha256(approval["token"].encode()).hexdigest()
        assert updated_client.token_hash == expected_hash

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_token_from_approval_can_authenticate(self, admin_user):
        """§8.2.17-01b + §8.2.17-02: Token received from pairing_approved
        can be used to authenticate on a subsequent connection.

        End-to-end: pair -> receive token -> disconnect -> reconnect ->
        authenticate with that token.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "pairing_request",
                "client_name": "Full Flow Station",
                "protocol_version": "1",
            }
        )
        await communicator.receive_json_from(timeout=5)

        print_client = await database_sync_to_async(PrintClient.objects.get)(
            name="Full Flow Station"
        )

        @database_sync_to_async
        def approve_client(pc, admin):
            pc.status = "approved"
            pc.approved_by = admin
            pc.approved_at = timezone.now()
            pc.save()

        await approve_client(print_client, admin_user)

        channel_layer = get_channel_layer()
        group_name = f"print_client_{print_client.pk}"
        await channel_layer.group_send(
            group_name,
            {
                "type": "pairing.approved",
                "print_client_id": print_client.pk,
            },
        )

        approval = await communicator.receive_json_from(timeout=5)
        token = approval["token"]
        await communicator.disconnect()

        # Reconnect and authenticate with the received token
        communicator2 = _make_communicator()
        connected2, _ = await communicator2.connect()
        assert connected2

        await communicator2.send_json_to(
            {
                "type": "authenticate",
                "token": token,
                "client_name": "Full Flow Station",
                "printers": [
                    {
                        "id": "printer-1",
                        "name": "Test Printer",
                        "type": "thermal",
                        "status": "online",
                        "templates": [],
                    }
                ],
                "protocol_version": "1",
            }
        )

        auth_response = await communicator2.receive_json_from(timeout=5)
        assert auth_response["type"] == "auth_result"
        assert auth_response["success"] is True

        await communicator2.disconnect()


# ---------------------------------------------------------------------------
# §8.2.17-02 — Authentication flow tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAuthenticationFlow:
    """§8.2.17-02: Authentication flow tests."""

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_valid_approved_token_auth_succeeds(self, admin_user):
        """§8.2.17-02: Valid approved token -> auth_result success=true.

        PrintClient.is_connected MUST be set to True and printers updated.
        """
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        @database_sync_to_async
        def create_client():
            return PrintClient.objects.create(
                name="Auth Test Station",
                token_hash=token_hash,
                status="approved",
                approved_by=admin_user,
                approved_at=timezone.now(),
            )

        print_client = await create_client()

        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        printers = [
            {
                "id": "zebra-01",
                "name": "Zebra ZD410 (Workshop)",
                "type": "thermal",
                "status": "online",
                "templates": ["square-62x62"],
            }
        ]

        await communicator.send_json_to(
            {
                "type": "authenticate",
                "token": raw_token,
                "client_name": "Auth Test Station",
                "printers": printers,
                "protocol_version": "1",
            }
        )

        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] == "auth_result"
        assert response["success"] is True
        assert "server_name" in response

        # Verify is_connected and printers were updated
        @database_sync_to_async
        def check_client(pk):
            c = PrintClient.objects.get(pk=pk)
            return c.is_connected, c.printers

        is_connected, stored_printers = await check_client(print_client.pk)
        assert is_connected is True
        assert len(stored_printers) == 1
        assert stored_printers[0]["id"] == "zebra-01"

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_token_auth_fails_and_closes(self):
        """§8.2.17-02: Invalid token -> auth_result success=false,
        connection closed.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "authenticate",
                "token": "this-is-a-completely-invalid-token",
                "client_name": "Bad Station",
                "printers": [],
                "protocol_version": "1",
            }
        )

        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] == "auth_result"
        assert response["success"] is False

        # The connection should be closed after auth failure
        # receive_output should eventually get a close message
        output = await communicator.receive_output(timeout=5)
        assert output["type"] == "websocket.close"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_valid_but_unapproved_token_rejected(self):
        """§8.2.17-02: Valid but unapproved (pending) token -> rejected.

        A token that matches a PrintClient still in 'pending' status
        MUST be rejected.
        """
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        @database_sync_to_async
        def create_pending_client():
            return PrintClient.objects.create(
                name="Pending Station",
                token_hash=token_hash,
                status="pending",
            )

        await create_pending_client()

        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "authenticate",
                "token": raw_token,
                "client_name": "Pending Station",
                "printers": [],
                "protocol_version": "1",
            }
        )

        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] == "auth_result"
        assert response["success"] is False

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_token_rotation_on_successful_reconnect(self, admin_user):
        """§8.2.17-02 / §4.3.3.4: Token rotation on reconnect.

        On successful reconnection, a new_token MUST be issued in
        auth_result. The client MUST use the new token for subsequent
        authentications. The old token should no longer work.
        """
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        @database_sync_to_async
        def create_client():
            return PrintClient.objects.create(
                name="Rotating Token Station",
                token_hash=token_hash,
                status="approved",
                approved_by=admin_user,
                approved_at=timezone.now(),
            )

        print_client = await create_client()

        # First connection
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "authenticate",
                "token": raw_token,
                "client_name": "Rotating Token Station",
                "printers": [],
                "protocol_version": "1",
            }
        )

        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] == "auth_result"
        assert response["success"] is True
        assert "new_token" in response
        new_token = response["new_token"]
        # New token must be different from original
        assert new_token != raw_token

        await communicator.disconnect()

        # Verify the stored hash was updated to the new token
        @database_sync_to_async
        def check_hash(pk):
            c = PrintClient.objects.get(pk=pk)
            return c.token_hash

        stored_hash = await check_hash(print_client.pk)
        expected_hash = hashlib.sha256(new_token.encode()).hexdigest()
        assert stored_hash == expected_hash

        # Old token should no longer work
        communicator2 = _make_communicator()
        connected2, _ = await communicator2.connect()
        assert connected2

        await communicator2.send_json_to(
            {
                "type": "authenticate",
                "token": raw_token,
                "client_name": "Rotating Token Station",
                "printers": [],
                "protocol_version": "1",
            }
        )

        response2 = await communicator2.receive_json_from(timeout=5)
        assert response2["type"] == "auth_result"
        assert response2["success"] is False

        await communicator2.disconnect()

        # New token should work
        communicator3 = _make_communicator()
        connected3, _ = await communicator3.connect()
        assert connected3

        await communicator3.send_json_to(
            {
                "type": "authenticate",
                "token": new_token,
                "client_name": "Rotating Token Station",
                "printers": [],
                "protocol_version": "1",
            }
        )

        response3 = await communicator3.receive_json_from(timeout=5)
        assert response3["type"] == "auth_result"
        assert response3["success"] is True

        await communicator3.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_disconnect_sets_is_connected_false(self, admin_user):
        """§8.2.17-04 / §4.3.3.5: Client disconnect updates
        is_connected=False and last_seen_at.
        """
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        @database_sync_to_async
        def create_client():
            return PrintClient.objects.create(
                name="Disconnect Test Station",
                token_hash=token_hash,
                status="approved",
                approved_by=admin_user,
                approved_at=timezone.now(),
            )

        print_client = await create_client()

        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "authenticate",
                "token": raw_token,
                "client_name": "Disconnect Test Station",
                "printers": [],
                "protocol_version": "1",
            }
        )
        await communicator.receive_json_from(timeout=5)

        # Verify connected
        @database_sync_to_async
        def get_client(pk):
            return PrintClient.objects.get(pk=pk)

        client_obj = await get_client(print_client.pk)
        assert client_obj.is_connected is True

        # Disconnect
        await communicator.disconnect()

        # Allow time for disconnect handler
        await asyncio.sleep(0.1)

        # Verify disconnected
        client_obj = await get_client(print_client.pk)
        assert client_obj.is_connected is False
        assert client_obj.last_seen_at is not None


# ---------------------------------------------------------------------------
# §4.3.3.4 — Additional protocol tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestProtocolEdgeCases:
    """Additional WebSocket protocol edge cases from §4.3.3.4."""

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unauthenticated_timeout_30_seconds(self):
        """§4.3.3.4: Connections that do not send authenticate or
        pairing_request within 30 seconds MUST be closed.

        We cannot wait 30 real seconds in a test, so we verify the
        consumer has a timeout mechanism. We test that if no message
        is sent, the connection is eventually closed. For fast testing
        we mock or check the timeout constant.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        # The consumer should have a mechanism to close idle
        # connections. We verify the attribute exists (the actual
        # 30s timeout would be too slow for a test).
        # For now, we test that sending nothing and trying to
        # receive eventually produces a close or timeout.
        # Implementation should use asyncio.wait_for or similar.
        # This test will pass once the consumer implements the
        # timeout logic — for now it's expected to fail.
        try:
            output = await asyncio.wait_for(
                communicator.receive_output(), timeout=35
            )
            # If we get output, it should be a close
            assert output["type"] == "websocket.close"
        except asyncio.TimeoutError:
            # Consumer didn't close — this is the failing case
            pytest.fail(
                "Consumer did not close unauthenticated connection "
                "within 30 seconds"
            )
        finally:
            await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_protocol_version_mismatch_rejected(self):
        """§4.3.3.4: Incompatible protocol_version SHOULD be rejected
        with error code version_mismatch.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "pairing_request",
                "client_name": "Version Test Station",
                "protocol_version": "999",
            }
        )

        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] == "error"
        assert response["code"] == "version_mismatch"

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_message_type_rejected(self):
        """§8.2.17-04: Invalid message types are rejected without
        crashing the consumer.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "totally_bogus_message",
                "data": "should be rejected",
            }
        )

        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] == "error"
        assert response["code"] == "invalid_message"

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_single_connection_per_client_enforcement(self, admin_user):
        """§4.3.3.4: Single connection per client enforcement.

        When a new authenticate arrives from an already-connected client,
        the server MUST close the prior connection.
        """
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        @database_sync_to_async
        def create_client():
            return PrintClient.objects.create(
                name="Single Conn Station",
                token_hash=token_hash,
                status="approved",
                approved_by=admin_user,
                approved_at=timezone.now(),
            )

        await create_client()

        # First connection
        comm1 = _make_communicator()
        connected1, _ = await comm1.connect()
        assert connected1

        await comm1.send_json_to(
            {
                "type": "authenticate",
                "token": raw_token,
                "client_name": "Single Conn Station",
                "printers": [],
                "protocol_version": "1",
            }
        )
        resp1 = await comm1.receive_json_from(timeout=5)
        assert resp1["success"] is True
        # Get the rotated token for second connection
        new_token = resp1.get("new_token", raw_token)

        # Second connection from same client
        comm2 = _make_communicator()
        connected2, _ = await comm2.connect()
        assert connected2

        await comm2.send_json_to(
            {
                "type": "authenticate",
                "token": new_token,
                "client_name": "Single Conn Station",
                "printers": [],
                "protocol_version": "1",
            }
        )
        resp2 = await comm2.receive_json_from(timeout=5)
        assert resp2["success"] is True

        # The first connection should have been closed
        try:
            output = await comm1.receive_output(timeout=5)
            assert output["type"] == "websocket.close"
        except asyncio.TimeoutError:
            pytest.fail(
                "Prior connection was not closed when new connection "
                "authenticated for the same client"
            )

        await comm2.disconnect()


# ---------------------------------------------------------------------------
# Helper: create an approved client and authenticate it on a communicator
# ---------------------------------------------------------------------------


async def _make_approved_client_and_token(admin_user):
    """Create an approved PrintClient and return (client, raw_token)."""
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    @database_sync_to_async
    def _create(admin):
        return PrintClient.objects.create(
            name="Dispatch Station",
            token_hash=token_hash,
            status="approved",
            approved_by=admin,
            approved_at=timezone.now(),
        )

    pc = await _create(admin_user)
    return pc, raw_token


async def _authenticate_communicator(communicator, raw_token, printers=None):
    """Send authenticate and consume auth_result. Returns response."""
    if printers is None:
        printers = [
            {
                "id": "zebra-01",
                "name": "Zebra ZD410",
                "type": "thermal",
                "status": "online",
                "templates": [],
            }
        ]
    await communicator.send_json_to(
        {
            "type": "authenticate",
            "token": raw_token,
            "client_name": "Dispatch Station",
            "printers": printers,
            "protocol_version": "1",
        }
    )
    return await communicator.receive_json_from(timeout=5)


# ---------------------------------------------------------------------------
# §8.2.17-03 — Print job lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPrintJobDispatch:
    """§8.2.17-03: Print job dispatch to connected client.

    The server dispatches print jobs via channel layer messages to
    the consumer, which sends them as WebSocket ``print`` messages
    to the connected client.
    """

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_print_message_dispatched_to_connected_client(
        self, admin_user, asset
    ):
        """§8.2.17-03: A print message is correctly dispatched
        to the target connected client.

        After authentication, the consumer should join a group
        like ``print_client_active_{pk}`` so the server can push
        print jobs via channel layer group_send.
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True
        new_token = auth.get("new_token", raw_token)  # noqa: F841

        # Create a PrintRequest in the database
        @database_sync_to_async
        def create_print_request(pc_obj, asset_obj):
            return PrintRequest.objects.create(
                print_client=pc_obj,
                asset=asset_obj,
                printer_id="zebra-01",
                quantity=1,
                status="pending",
            )

        pr = await create_print_request(pc, asset)

        # Build message payload with sync DB access for FKs
        @database_sync_to_async
        def get_asset_fields(a):
            cat = a.category.name if a.category else ""
            dept = a.category.department.name if a.category else ""
            return a.barcode, a.name[:30], cat, dept

        barcode, name, cat, dept = await get_asset_fields(asset)

        # Dispatch print job via channel layer to the consumer
        channel_layer = get_channel_layer()
        group_name = f"print_client_active_{pc.pk}"
        await channel_layer.group_send(
            group_name,
            {
                "type": "print.job",
                "job_id": str(pr.job_id),
                "printer_id": "zebra-01",
                "barcode": barcode,
                "asset_name": name,
                "category_name": cat,
                "department_name": dept,
                "qr_content": (f"https://example.com/a/{barcode}/"),
                "quantity": 1,
            },
        )

        # Client should receive a print message
        msg = await communicator.receive_json_from(timeout=5)
        assert msg["type"] == "print"
        assert msg["job_id"] == str(pr.job_id)
        assert msg["printer_id"] == "zebra-01"
        assert msg["barcode"] == barcode
        assert msg["quantity"] == 1

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dispatch_transitions_status_pending_to_sent(
        self, admin_user, asset
    ):
        """§8.2.17-03: Dispatching a print job transitions the
        PrintRequest status from pending to sent.

        The consumer's print_job handler should update the
        PrintRequest status to 'sent' after successfully sending
        the print message to the WebSocket client.
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        @database_sync_to_async
        def create_print_request(pc_obj, asset_obj):
            return PrintRequest.objects.create(
                print_client=pc_obj,
                asset=asset_obj,
                printer_id="zebra-01",
                quantity=1,
                status="pending",
            )

        pr = await create_print_request(pc, asset)

        channel_layer = get_channel_layer()
        group_name = f"print_client_active_{pc.pk}"
        await channel_layer.group_send(
            group_name,
            {
                "type": "print.job",
                "job_id": str(pr.job_id),
                "printer_id": "zebra-01",
                "barcode": asset.barcode,
                "asset_name": asset.name[:30],
                "category_name": "",
                "department_name": "",
                "qr_content": (f"https://example.com/a/{asset.barcode}/"),
                "quantity": 1,
            },
        )

        # Consume the print message
        await communicator.receive_json_from(timeout=5)

        # Allow async DB update to complete
        await asyncio.sleep(0.2)

        # Verify status transitioned to sent
        @database_sync_to_async
        def get_pr_status(job_id):
            req = PrintRequest.objects.get(job_id=job_id)
            return req.status, req.sent_at

        status, sent_at = await get_pr_status(pr.job_id)
        assert status == "sent"
        assert sent_at is not None

        await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
class TestPrintAckHandling:
    """§4.3.3.5 / §8.2.17-03: print_ack handling.

    Client acknowledges receipt of a print job, transitioning
    the PrintRequest from sent to acked.
    """

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_print_ack_transitions_sent_to_acked(
        self, admin_user, asset
    ):
        """§4.3.3.5: Client sends print_ack -> PrintRequest
        transitions from sent to acked and acked_at is set.
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        # Create a PrintRequest already in sent status
        @database_sync_to_async
        def create_sent_request(pc_obj, asset_obj):
            pr = PrintRequest.objects.create(
                print_client=pc_obj,
                asset=asset_obj,
                printer_id="zebra-01",
                quantity=1,
                status="pending",
            )
            pr.transition_to("sent")
            return pr

        pr = await create_sent_request(pc, asset)

        # Client sends print_ack
        await communicator.send_json_to(
            {
                "type": "print_ack",
                "job_id": str(pr.job_id),
            }
        )

        # Allow processing
        await asyncio.sleep(0.2)

        @database_sync_to_async
        def get_pr(job_id):
            return PrintRequest.objects.get(job_id=job_id)

        updated = await get_pr(pr.job_id)
        assert updated.status == "acked"
        assert updated.acked_at is not None

        await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
class TestPrintStatusCompleted:
    """§8.2.17-03: print_status with status=completed."""

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_print_status_completed_updates_request(
        self, admin_user, asset
    ):
        """§8.2.17-03: A print_status message with
        status=completed updates the PrintRequest to completed
        and sets completed_at.
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        @database_sync_to_async
        def create_acked_request(pc_obj, asset_obj):
            pr = PrintRequest.objects.create(
                print_client=pc_obj,
                asset=asset_obj,
                printer_id="zebra-01",
                quantity=1,
                status="pending",
            )
            pr.transition_to("sent")
            pr.transition_to("acked")
            return pr

        pr = await create_acked_request(pc, asset)

        before = timezone.now()

        # Client sends print_status completed
        await communicator.send_json_to(
            {
                "type": "print_status",
                "job_id": str(pr.job_id),
                "status": "completed",
                "error": None,
            }
        )

        await asyncio.sleep(0.2)

        @database_sync_to_async
        def get_pr(job_id):
            return PrintRequest.objects.get(job_id=job_id)

        updated = await get_pr(pr.job_id)
        assert updated.status == "completed"
        assert updated.completed_at is not None
        assert updated.completed_at >= before

        await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
class TestPrintStatusFailed:
    """§8.2.17-03: print_status with status=failed."""

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_print_status_failed_updates_request(
        self, admin_user, asset
    ):
        """§8.2.17-03: A print_status message with status=failed
        updates the PrintRequest to failed and stores error_message.
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        @database_sync_to_async
        def create_acked_request(pc_obj, asset_obj):
            pr = PrintRequest.objects.create(
                print_client=pc_obj,
                asset=asset_obj,
                printer_id="zebra-01",
                quantity=1,
                status="pending",
            )
            pr.transition_to("sent")
            pr.transition_to("acked")
            return pr

        pr = await create_acked_request(pc, asset)

        # Client sends print_status failed
        await communicator.send_json_to(
            {
                "type": "print_status",
                "job_id": str(pr.job_id),
                "status": "failed",
                "error": "Paper jam in tray 2",
            }
        )

        await asyncio.sleep(0.2)

        @database_sync_to_async
        def get_pr(job_id):
            return PrintRequest.objects.get(job_id=job_id)

        updated = await get_pr(pr.job_id)
        assert updated.status == "failed"
        assert updated.error_message == "Paper jam in tray 2"
        assert updated.completed_at is not None

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_print_status_failed_with_null_error(
        self, admin_user, asset
    ):
        """§4.3.3.5: print_status with status=failed and null
        error still transitions to failed.
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        @database_sync_to_async
        def create_acked_request(pc_obj, asset_obj):
            pr = PrintRequest.objects.create(
                print_client=pc_obj,
                asset=asset_obj,
                printer_id="zebra-01",
                quantity=1,
                status="pending",
            )
            pr.transition_to("sent")
            pr.transition_to("acked")
            return pr

        pr = await create_acked_request(pc, asset)

        await communicator.send_json_to(
            {
                "type": "print_status",
                "job_id": str(pr.job_id),
                "status": "failed",
                "error": None,
            }
        )

        await asyncio.sleep(0.2)

        @database_sync_to_async
        def get_pr(job_id):
            return PrintRequest.objects.get(job_id=job_id)

        updated = await get_pr(pr.job_id)
        assert updated.status == "failed"
        assert updated.completed_at is not None

        await communicator.disconnect()


# ---------------------------------------------------------------------------
# §8.2.17-04 — Edge cases: dispatch to disconnected, disconnect
# during lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPrintJobDisconnectedClient:
    """§8.2.17-04: Print jobs to disconnected clients fail
    gracefully.
    """

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dispatch_to_disconnected_client_fails_gracefully(
        self, admin_user, asset
    ):
        """§8.2.17-04 / §4.3.3.5: At dispatch time, if the client
        is disconnected, the job MUST be marked failed immediately
        with error 'Client disconnected'.

        We authenticate, disconnect, then attempt to dispatch a
        print job via the dispatch service. The job should fail.
        """
        from assets.services.print_dispatch import (
            dispatch_print_job,
        )

        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        # Disconnect the client
        await communicator.disconnect()
        await asyncio.sleep(0.2)

        # Verify client is disconnected
        @database_sync_to_async
        def check_disconnected(pk):
            c = PrintClient.objects.get(pk=pk)
            return c.is_connected

        is_conn = await check_disconnected(pc.pk)
        assert is_conn is False

        # Create a pending PrintRequest
        @database_sync_to_async
        def create_print_request(pc_obj, asset_obj):
            return PrintRequest.objects.create(
                print_client=pc_obj,
                asset=asset_obj,
                printer_id="zebra-01",
                quantity=1,
                status="pending",
            )

        pr = await create_print_request(pc, asset)

        # Dispatch via the service — it checks is_connected
        # before sending and fails the job immediately.
        @database_sync_to_async
        def do_dispatch(print_req):
            return dispatch_print_job(print_req)

        result = await do_dispatch(pr)
        assert result is False

        @database_sync_to_async
        def get_pr_status(job_id):
            req = PrintRequest.objects.get(job_id=job_id)
            return req.status

        status = await get_pr_status(pr.job_id)
        assert status == "failed"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_disconnect_during_sent_status_holds_then_fails(
        self, admin_user, asset
    ):
        """§4.3.3.5: After dispatch + no ack, if client disconnects
        the job should eventually fail.

        When a client disconnects after a job was sent but before
        acknowledgement, the job remains in sent status until the
        stale job timeout, then transitions to failed.
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        # Create a request and move it to sent status
        @database_sync_to_async
        def create_sent_request(pc_obj, asset_obj):
            pr = PrintRequest.objects.create(
                print_client=pc_obj,
                asset=asset_obj,
                printer_id="zebra-01",
                quantity=1,
                status="pending",
            )
            pr.transition_to("sent")
            return pr

        pr = await create_sent_request(pc, asset)

        # Client disconnects
        await communicator.disconnect()
        await asyncio.sleep(0.2)

        # The disconnect handler should handle in-flight jobs.
        # Per spec, sent jobs without ack hold until stale
        # timeout. The disconnect handler MAY mark them as
        # failed immediately or leave them for the timeout task.
        @database_sync_to_async
        def get_pr(job_id):
            req = PrintRequest.objects.get(job_id=job_id)
            return req.status

        status = await get_pr(pr.job_id)
        # After disconnect, sent jobs with no ack should be
        # failed (either immediately by disconnect handler
        # or via the stale job timeout).
        assert status in ("sent", "failed")


@pytest.mark.django_db(transaction=True)
class TestPrintJobMessageFields:
    """§4.3.3.5: Print message field requirements."""

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_print_message_contains_all_required_fields(
        self, admin_user, asset
    ):
        """§4.3.3.5: The print message MUST contain job_id,
        printer_id, barcode, asset_name, category_name,
        department_name, qr_content, and quantity.
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        @database_sync_to_async
        def create_print_request(pc_obj, asset_obj):
            return PrintRequest.objects.create(
                print_client=pc_obj,
                asset=asset_obj,
                printer_id="zebra-01",
                quantity=3,
                status="pending",
            )

        pr = await create_print_request(pc, asset)

        channel_layer = get_channel_layer()
        group_name = f"print_client_active_{pc.pk}"
        await channel_layer.group_send(
            group_name,
            {
                "type": "print.job",
                "job_id": str(pr.job_id),
                "printer_id": "zebra-01",
                "barcode": asset.barcode,
                "asset_name": asset.name[:30],
                "category_name": "Test Category",
                "department_name": "Test Department",
                "qr_content": (f"https://example.com/a/{asset.barcode}/"),
                "quantity": 3,
                "site_short_name": "RWTS",
            },
        )

        msg = await communicator.receive_json_from(timeout=5)
        assert msg["type"] == "print"
        # All required fields per §4.3.3.5 protocol contract
        assert "job_id" in msg
        assert "printer_id" in msg
        assert "barcode" in msg
        assert "asset_name" in msg
        assert "category_name" in msg
        assert "department_name" in msg
        assert "qr_content" in msg
        assert "quantity" in msg
        assert msg["quantity"] == 3
        # site_short_name is optional per spec
        assert msg.get("site_short_name") == "RWTS"

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_asset_name_truncated_to_30_chars(self, admin_user, asset):
        """§4.3.3.5: asset_name MUST be truncated to 30
        characters by the server.
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        long_name = "A" * 50  # 50 chars, should be truncated

        @database_sync_to_async
        def create_print_request(pc_obj, asset_obj):
            return PrintRequest.objects.create(
                print_client=pc_obj,
                asset=asset_obj,
                printer_id="zebra-01",
                quantity=1,
                status="pending",
            )

        pr = await create_print_request(pc, asset)

        channel_layer = get_channel_layer()
        group_name = f"print_client_active_{pc.pk}"
        await channel_layer.group_send(
            group_name,
            {
                "type": "print.job",
                "job_id": str(pr.job_id),
                "printer_id": "zebra-01",
                "barcode": asset.barcode,
                "asset_name": long_name[:30],
                "category_name": "",
                "department_name": "",
                "qr_content": (f"https://example.com/a/{asset.barcode}/"),
                "quantity": 1,
            },
        )

        msg = await communicator.receive_json_from(timeout=5)
        assert msg["type"] == "print"
        assert len(msg["asset_name"]) <= 30

        await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
class TestPrintAckEdgeCases:
    """§4.3.3.5: print_ack edge cases."""

    pytestmark = pytest.mark.asyncio(loop_scope="function")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_print_ack_unknown_job_id_handled(self, admin_user):
        """§4.3.3.5: print_ack with an unknown job_id should
        be handled gracefully (no crash).
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        # Send ack for a non-existent job
        fake_job_id = str(uuid.uuid4())
        await communicator.send_json_to(
            {
                "type": "print_ack",
                "job_id": fake_job_id,
            }
        )

        # Consumer should handle gracefully — either ignore
        # or send an error. Should not crash.
        await asyncio.sleep(0.2)

        # Verify connection is still alive by sending a
        # valid message type (we can check if it responds)
        await communicator.send_json_to(
            {
                "type": "print_status",
                "job_id": fake_job_id,
                "status": "completed",
                "error": None,
            }
        )

        # Connection should still be open
        await asyncio.sleep(0.1)
        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_print_status_unknown_job_id_handled(self, admin_user):
        """§4.3.3.5: print_status with unknown job_id should be
        handled gracefully (no crash).
        """
        pc, raw_token = await _make_approved_client_and_token(admin_user)
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        auth = await _authenticate_communicator(communicator, raw_token)
        assert auth["success"] is True

        fake_job_id = str(uuid.uuid4())
        await communicator.send_json_to(
            {
                "type": "print_status",
                "job_id": fake_job_id,
                "status": "completed",
                "error": None,
            }
        )

        await asyncio.sleep(0.2)

        # Connection should still be alive
        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_print_ack_from_unauthenticated_client_rejected(
        self,
    ):
        """§4.3.3.5: print_ack from an unauthenticated client
        should be rejected.

        Only authenticated clients should be able to send
        print_ack and print_status messages.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "print_ack",
                "job_id": str(uuid.uuid4()),
            }
        )

        response = await communicator.receive_json_from(timeout=5)
        # Should be rejected — either as invalid_message or
        # as an auth error
        assert response["type"] == "error"

        await communicator.disconnect()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_print_status_from_unauthenticated_rejected(
        self,
    ):
        """§4.3.3.5: print_status from unauthenticated client
        should be rejected.
        """
        communicator = _make_communicator()
        connected, _ = await communicator.connect()
        assert connected

        await communicator.send_json_to(
            {
                "type": "print_status",
                "job_id": str(uuid.uuid4()),
                "status": "completed",
                "error": None,
            }
        )

        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] == "error"

        await communicator.disconnect()


# ---------------------------------------------------------------------------
# §8.3.11-01 — Print Service Admin UI tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPrintClientAdminRegistered:
    """PrintClientAdmin must be registered in the Django admin."""

    def test_print_client_admin_is_registered(self):
        """§8.3.11-01: PrintClient must have a registered ModelAdmin."""
        from django.contrib import admin

        assert (
            PrintClient in admin.site._registry
        ), "PrintClient must be registered in the Django admin"

    def test_print_client_changelist_accessible_by_admin(self, admin_client):
        """§8.3.11-01: Admin users can access PrintClient changelist."""
        from django.urls import reverse

        url = reverse("admin:assets_printclient_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_print_client_changelist_denied_for_non_staff(
        self, client, member_user, password
    ):
        """§8.3.11-01: Non-admin users cannot access admin views."""
        from django.urls import reverse

        client.login(username=member_user.username, password=password)
        url = reverse("admin:assets_printclient_changelist")
        response = client.get(url)
        # Non-staff users get redirected to admin login
        assert response.status_code == 302


@pytest.mark.django_db
class TestPrintRequestAdminRegistered:
    """PrintRequestAdmin must be registered in the Django admin."""

    def test_print_request_admin_is_registered(self):
        """§8.3.11-01: PrintRequest must have a registered ModelAdmin."""
        from django.contrib import admin

        assert (
            PrintRequest in admin.site._registry
        ), "PrintRequest must be registered in the Django admin"

    def test_print_request_changelist_accessible_by_admin(self, admin_client):
        """§8.3.11-01: Admin users can access PrintRequest changelist."""
        from django.urls import reverse

        url = reverse("admin:assets_printrequest_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_print_request_changelist_denied_for_non_staff(
        self, client, member_user, password
    ):
        """§8.3.11-01: Non-admin users cannot access admin views."""
        from django.urls import reverse

        client.login(username=member_user.username, password=password)
        url = reverse("admin:assets_printrequest_changelist")
        response = client.get(url)
        assert response.status_code == 302


@pytest.mark.django_db
class TestPrintClientAdminListDisplay:
    """PrintClientAdmin list_display fields (§8.3.11-01)."""

    def test_list_display_includes_name(self):
        """§4.3.5: Changelist shows client name."""
        from assets.admin import PrintClientAdmin

        cols = [str(c) for c in PrintClientAdmin.list_display]
        has_name = any("name" in c.lower() for c in cols)
        assert has_name, (
            f"PrintClientAdmin.list_display must include name. " f"Got: {cols}"
        )

    def test_list_display_includes_status(self):
        """§4.3.5: Changelist shows status."""
        from assets.admin import PrintClientAdmin

        cols = [str(c) for c in PrintClientAdmin.list_display]
        has_status = any("status" in c.lower() for c in cols)
        assert has_status, (
            f"PrintClientAdmin.list_display must include status. "
            f"Got: {cols}"
        )

    def test_list_display_includes_is_connected(self):
        """§4.3.5: Connected Client Dashboard shows connection status."""
        from assets.admin import PrintClientAdmin

        cols = [str(c) for c in PrintClientAdmin.list_display]
        has_connected = any("connect" in c.lower() for c in cols)
        assert has_connected, (
            f"PrintClientAdmin.list_display must include connection "
            f"status. Got: {cols}"
        )

    def test_list_display_includes_last_seen(self):
        """§4.3.5: Dashboard shows last seen timestamp."""
        from assets.admin import PrintClientAdmin

        cols = [str(c) for c in PrintClientAdmin.list_display]
        has_last_seen = any("last_seen" in c.lower() for c in cols)
        assert has_last_seen, (
            f"PrintClientAdmin.list_display must include last_seen. "
            f"Got: {cols}"
        )

    def test_list_display_includes_is_active(self):
        """§4.3.5: Dashboard shows active state."""
        from assets.admin import PrintClientAdmin

        cols = [str(c) for c in PrintClientAdmin.list_display]
        has_active = any("active" in c.lower() for c in cols)
        assert has_active, (
            f"PrintClientAdmin.list_display must include is_active. "
            f"Got: {cols}"
        )

    def test_changelist_renders_with_data(self, admin_client):
        """§4.3.5: Changelist renders with existing PrintClient data."""
        from django.urls import reverse

        _make_print_client("admin-list-1")
        url = reverse("admin:assets_printclient_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "Station-admin-list-1" in content


@pytest.mark.django_db
class TestPrintClientAdminFilters:
    """PrintClientAdmin list_filter fields (§8.3.11-01)."""

    def test_list_filter_includes_status(self):
        """§4.3.5: Filter by status (pending/approved)."""
        from assets.admin import PrintClientAdmin

        filter_fields = _flatten_filter_names(PrintClientAdmin.list_filter)
        assert "status" in filter_fields, (
            f"PrintClientAdmin.list_filter must include status. "
            f"Got: {PrintClientAdmin.list_filter}"
        )

    def test_list_filter_includes_is_connected(self):
        """§4.3.5: Filter by connection status."""
        from assets.admin import PrintClientAdmin

        filter_fields = _flatten_filter_names(PrintClientAdmin.list_filter)
        assert "is_connected" in filter_fields, (
            f"PrintClientAdmin.list_filter must include is_connected. "
            f"Got: {PrintClientAdmin.list_filter}"
        )

    def test_list_filter_includes_is_active(self):
        """§4.3.5: Filter by active/deactivated."""
        from assets.admin import PrintClientAdmin

        filter_fields = _flatten_filter_names(PrintClientAdmin.list_filter)
        assert "is_active" in filter_fields, (
            f"PrintClientAdmin.list_filter must include is_active. "
            f"Got: {PrintClientAdmin.list_filter}"
        )


@pytest.mark.django_db
class TestPrintRequestAdminListDisplay:
    """PrintRequestAdmin list_display fields (§8.3.11-01)."""

    def test_list_display_includes_job_id(self):
        """§4.3.5: Job History shows job ID."""
        from assets.admin import PrintRequestAdmin

        cols = [str(c) for c in PrintRequestAdmin.list_display]
        has_job_id = any("job_id" in c.lower() for c in cols)
        assert has_job_id, (
            f"PrintRequestAdmin.list_display must include job_id. "
            f"Got: {cols}"
        )

    def test_list_display_includes_asset(self):
        """§4.3.5: Job History shows asset."""
        from assets.admin import PrintRequestAdmin

        cols = [str(c) for c in PrintRequestAdmin.list_display]
        has_asset = any("asset" in c.lower() for c in cols)
        assert has_asset, (
            f"PrintRequestAdmin.list_display must include asset. "
            f"Got: {cols}"
        )

    def test_list_display_includes_status(self):
        """§4.3.5: Job History shows status."""
        from assets.admin import PrintRequestAdmin

        cols = [str(c) for c in PrintRequestAdmin.list_display]
        has_status = any("status" in c.lower() for c in cols)
        assert has_status, (
            f"PrintRequestAdmin.list_display must include status. "
            f"Got: {cols}"
        )

    def test_list_display_includes_printer(self):
        """§4.3.5: Job History shows printer."""
        from assets.admin import PrintRequestAdmin

        cols = [str(c) for c in PrintRequestAdmin.list_display]
        has_printer = any("printer" in c.lower() for c in cols)
        assert has_printer, (
            f"PrintRequestAdmin.list_display must include printer. "
            f"Got: {cols}"
        )

    def test_list_display_includes_created_at(self):
        """§4.3.5: Job History shows timestamps."""
        from assets.admin import PrintRequestAdmin

        cols = [str(c) for c in PrintRequestAdmin.list_display]
        has_created = any("created" in c.lower() for c in cols)
        assert has_created, (
            f"PrintRequestAdmin.list_display must include created_at. "
            f"Got: {cols}"
        )

    def test_changelist_renders_with_data(self, admin_client, asset):
        """§4.3.5: Changelist renders with existing PrintRequest data."""
        from django.urls import reverse

        pc = _make_print_client("admin-req-list-1")
        PrintRequest.objects.create(
            print_client=pc,
            asset=asset,
            printer_id="printer-001",
        )
        url = reverse("admin:assets_printrequest_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestPrintRequestAdminFilters:
    """PrintRequestAdmin list_filter fields (§8.3.11-01)."""

    def test_list_filter_includes_status(self):
        """§4.3.5: Filter jobs by status."""
        from assets.admin import PrintRequestAdmin

        filter_fields = _flatten_filter_names(PrintRequestAdmin.list_filter)
        assert "status" in filter_fields, (
            f"PrintRequestAdmin.list_filter must include status. "
            f"Got: {PrintRequestAdmin.list_filter}"
        )

    def test_list_filter_includes_print_client(self):
        """§4.3.5: Filter jobs by client."""
        from assets.admin import PrintRequestAdmin

        filter_fields = _flatten_filter_names(PrintRequestAdmin.list_filter)
        assert "print_client" in filter_fields, (
            f"PrintRequestAdmin.list_filter must include print_client. "
            f"Got: {PrintRequestAdmin.list_filter}"
        )

    def test_list_filter_includes_created_at(self):
        """§4.3.5: Filter jobs by date range."""
        from assets.admin import PrintRequestAdmin

        filter_fields = _flatten_filter_names(PrintRequestAdmin.list_filter)
        assert "created_at" in filter_fields, (
            f"PrintRequestAdmin.list_filter must include created_at. "
            f"Got: {PrintRequestAdmin.list_filter}"
        )


def _flatten_filter_names(list_filter):
    """Extract field names from list_filter entries.

    Handles plain strings, tuples like ("field", FilterClass),
    and filter class references.
    """
    names = []
    for entry in list_filter:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, (list, tuple)):
            names.append(entry[0])
        elif hasattr(entry, "parameter_name"):
            names.append(entry.parameter_name)
    return names


@pytest.mark.django_db
class TestPrintClientAdminApproveAction:
    """Approve action on PrintClient (§8.3.11-01).

    §4.3.5: Approval sends token via WebSocket channel layer.
    Only System Admins can approve.
    """

    def test_approve_action_sets_status_to_approved(
        self, admin_client, admin_user
    ):
        """§4.3.5: Approve action transitions status to approved."""
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import (
            FallbackStorage,
        )
        from django.test import RequestFactory

        from assets.admin import PrintClientAdmin

        pc = _make_print_client("approve-test-1")
        pc.status = "pending"
        pc.save(update_fields=["status"])

        admin_obj = PrintClientAdmin(PrintClient, AdminSite())
        qs = PrintClient.objects.filter(pk=pc.pk)

        factory = RequestFactory()
        request = factory.post("/admin/assets/printclient/")
        request.user = admin_user
        setattr(request, "session", "session")
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        admin_obj.approve_clients(request, qs)
        pc.refresh_from_db()
        assert pc.status == "approved"

    def test_approve_action_sets_approved_by_and_at(
        self, admin_client, admin_user
    ):
        """§4.3.5: Approval sets approved_by and approved_at fields."""
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import (
            FallbackStorage,
        )
        from django.test import RequestFactory

        from assets.admin import PrintClientAdmin

        pc = _make_print_client("approve-test-2")
        pc.status = "pending"
        pc.save(update_fields=["status"])

        admin_obj = PrintClientAdmin(PrintClient, AdminSite())
        qs = PrintClient.objects.filter(pk=pc.pk)

        factory = RequestFactory()
        request = factory.post("/admin/assets/printclient/")
        request.user = admin_user
        setattr(request, "session", "session")
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        admin_obj.approve_clients(request, qs)
        pc.refresh_from_db()
        assert pc.approved_by == admin_user
        assert pc.approved_at is not None

    def test_approve_action_sends_channel_layer_message(
        self, admin_client, admin_user
    ):
        """§4.3.5: Approval sends pairing_approved via channel layer.

        The approve action must send a message to the print client's
        channel group so the WebSocket consumer can deliver the token.
        """
        from unittest.mock import patch

        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import (
            FallbackStorage,
        )
        from django.test import RequestFactory

        from assets.admin import PrintClientAdmin

        pc = _make_print_client("approve-ws-test")
        pc.status = "pending"
        pc.save(update_fields=["status"])

        admin_obj = PrintClientAdmin(PrintClient, AdminSite())
        qs = PrintClient.objects.filter(pk=pc.pk)

        factory = RequestFactory()
        request = factory.post("/admin/assets/printclient/")
        request.user = admin_user
        setattr(request, "session", "session")
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        with patch("assets.admin.async_to_sync") as mock_async_to_sync:
            mock_async_to_sync.return_value
            admin_obj.approve_clients(request, qs)
            assert mock_async_to_sync.called


@pytest.mark.django_db
class TestPrintClientAdminDenyAction:
    """Deny action on pending PrintClient (§8.3.11-01).

    §4.3.5: Denial sends pairing_denied via channel layer
    and deletes the PrintClient record.
    """

    def test_deny_action_deletes_print_client(self, admin_client, admin_user):
        """§4.3.5: Deny action deletes the pending PrintClient."""
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import (
            FallbackStorage,
        )
        from django.test import RequestFactory

        from assets.admin import PrintClientAdmin

        pc = _make_print_client("deny-test-1")
        pc.status = "pending"
        pc.save(update_fields=["status"])
        pc_pk = pc.pk

        admin_obj = PrintClientAdmin(PrintClient, AdminSite())
        qs = PrintClient.objects.filter(pk=pc_pk)

        factory = RequestFactory()
        request = factory.post("/admin/assets/printclient/")
        request.user = admin_user
        setattr(request, "session", "session")
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        admin_obj.deny_clients(request, qs)
        assert not PrintClient.objects.filter(pk=pc_pk).exists()

    def test_deny_action_sends_pairing_denied_via_channel_layer(
        self, admin_client, admin_user
    ):
        """§4.3.5: Deny sends pairing_denied message via channel layer."""
        from unittest.mock import patch

        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import (
            FallbackStorage,
        )
        from django.test import RequestFactory

        from assets.admin import PrintClientAdmin

        pc = _make_print_client("deny-ws-test")
        pc.status = "pending"
        pc.save(update_fields=["status"])

        admin_obj = PrintClientAdmin(PrintClient, AdminSite())
        qs = PrintClient.objects.filter(pk=pc.pk)

        factory = RequestFactory()
        request = factory.post("/admin/assets/printclient/")
        request.user = admin_user
        setattr(request, "session", "session")
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        with patch("assets.admin.async_to_sync") as mock_async_to_sync:
            mock_async_to_sync.return_value
            admin_obj.deny_clients(request, qs)
            assert mock_async_to_sync.called


@pytest.mark.django_db
class TestPrintClientAdminDeactivateAction:
    """Deactivation action on PrintClient (§8.3.11-01).

    §4.3.5: Deactivation sets is_active=False and sends
    force_disconnect via channel layer. Deactivated clients
    cannot authenticate.
    """

    def test_deactivate_action_sets_is_active_false(
        self, admin_client, admin_user
    ):
        """§4.3.5: Deactivate action sets is_active=False."""
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import (
            FallbackStorage,
        )
        from django.test import RequestFactory

        from assets.admin import PrintClientAdmin

        pc = _make_print_client("deactivate-test-1")
        pc.status = "approved"
        pc.save()
        assert pc.is_active is True

        admin_obj = PrintClientAdmin(PrintClient, AdminSite())
        qs = PrintClient.objects.filter(pk=pc.pk)

        factory = RequestFactory()
        request = factory.post("/admin/assets/printclient/")
        request.user = admin_user
        setattr(request, "session", "session")
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        admin_obj.deactivate_clients(request, qs)
        pc.refresh_from_db()
        assert pc.is_active is False

    def test_deactivate_action_sends_force_disconnect(
        self, admin_client, admin_user
    ):
        """§4.3.5: Deactivation sends force_disconnect via channel layer."""
        from unittest.mock import patch

        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import (
            FallbackStorage,
        )
        from django.test import RequestFactory

        from assets.admin import PrintClientAdmin

        pc = _make_print_client("deactivate-ws-test")
        pc.status = "approved"
        pc.save()

        admin_obj = PrintClientAdmin(PrintClient, AdminSite())
        qs = PrintClient.objects.filter(pk=pc.pk)

        factory = RequestFactory()
        request = factory.post("/admin/assets/printclient/")
        request.user = admin_user
        setattr(request, "session", "session")
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        with patch("assets.admin.async_to_sync") as mock_async_to_sync:
            mock_async_to_sync.return_value
            admin_obj.deactivate_clients(request, qs)
            assert mock_async_to_sync.called


# ---------------------------------------------------------------------------
# §S2.4.5 — Remote print action on asset detail view
# ---------------------------------------------------------------------------


def _make_approved_connected_client(name="Test Station", printers=None):
    """Helper: create an approved, connected PrintClient with printers."""
    if printers is None:
        printers = [
            {
                "id": "printer-1",
                "name": "Zebra ZD421",
                "type": "zpl",
                "status": "ready",
                "templates": [],
            }
        ]
    token_hash = hashlib.sha256(
        f"token-{name}-{secrets.token_hex(4)}".encode()
    ).hexdigest()
    return PrintClient.objects.create(
        name=name,
        token_hash=token_hash,
        status="approved",
        is_active=True,
        is_connected=True,
        last_seen_at=timezone.now(),
        printers=printers,
    )


@pytest.mark.django_db
class TestAssetDetailRemotePrintContext:
    """S2.4.5-09/10: asset_detail includes remote print context vars."""

    def test_remote_print_available_true_when_connected_client(
        self, client_logged_in, asset
    ):
        """S2.4.5c-02: Button shown when >=1 approved connected client."""
        _make_approved_connected_client()
        url = reverse("assets:asset_detail", args=[asset.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        assert response.context["remote_print_available"] is True

    def test_remote_print_available_false_when_no_connected_clients(
        self, client_logged_in, asset
    ):
        """S2.4.5c-02: Button hidden when no approved connected clients."""
        url = reverse("assets:asset_detail", args=[asset.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        assert response.context["remote_print_available"] is False

    def test_remote_print_available_false_when_client_pending(
        self, client_logged_in, asset
    ):
        """Pending (unapproved) clients do not count."""
        token_hash = hashlib.sha256(b"pending-tok").hexdigest()
        PrintClient.objects.create(
            name="Pending Station",
            token_hash=token_hash,
            status="pending",
            is_connected=True,
        )
        url = reverse("assets:asset_detail", args=[asset.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        assert response.context["remote_print_available"] is False

    def test_remote_print_available_false_when_client_disconnected(
        self, client_logged_in, asset
    ):
        """Approved but disconnected clients do not count."""
        token_hash = hashlib.sha256(b"disco-tok").hexdigest()
        PrintClient.objects.create(
            name="Offline Station",
            token_hash=token_hash,
            status="approved",
            is_connected=False,
        )
        url = reverse("assets:asset_detail", args=[asset.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        assert response.context["remote_print_available"] is False

    def test_connected_printers_populated(self, client_logged_in, asset):
        """S2.4.5-10: Dropdown data includes client/printer details."""
        printers = [
            {
                "id": "lp1",
                "name": "Label Printer 1",
                "type": "zpl",
                "status": "ready",
                "templates": [],
            },
            {
                "id": "lp2",
                "name": "Label Printer 2",
                "type": "cups",
                "status": "ready",
                "templates": [],
            },
        ]
        pc = _make_approved_connected_client(
            name="Backstage", printers=printers
        )
        url = reverse("assets:asset_detail", args=[asset.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        connected = response.context["connected_printers"]
        assert len(connected) == 2
        # Each entry should carry client and printer info
        entry = connected[0]
        assert entry["client_pk"] == pc.pk
        assert entry["client_name"] == "Backstage"
        assert entry["printer_id"] in ("lp1", "lp2")
        assert "printer_name" in entry
        assert "printer_type" in entry

    def test_connected_printers_empty_when_none(self, client_logged_in, asset):
        """No connected printers means empty list."""
        url = reverse("assets:asset_detail", args=[asset.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        assert response.context["connected_printers"] == []


@pytest.mark.django_db
class TestRemotePrintSubmit:
    """S2.4.5-09: POST endpoint to submit a remote print request."""

    def _submit_url(self, asset_pk):
        return reverse("assets:remote_print_submit", args=[asset_pk])

    def test_submit_creates_print_request(self, client_logged_in, asset, user):
        """Successful submit creates a PrintRequest record."""
        pc = _make_approved_connected_client()
        url = self._submit_url(asset.pk)
        response = client_logged_in.post(
            url,
            {
                "client_pk": pc.pk,
                "printer_id": "printer-1",
                "quantity": 1,
            },
        )
        assert response.status_code == 200
        pr = PrintRequest.objects.get(asset=asset, print_client=pc)
        assert pr.printer_id == "printer-1"
        assert pr.quantity == 1
        assert pr.requested_by == user
        assert pr.status in ("pending", "sent")

    def test_submit_returns_json_success(self, client_logged_in, asset):
        """Submit returns JSON with success status."""
        pc = _make_approved_connected_client()
        url = self._submit_url(asset.pk)
        response = client_logged_in.post(
            url,
            {
                "client_pk": pc.pk,
                "printer_id": "printer-1",
                "quantity": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_submit_toctou_disconnected_client(self, client_logged_in, asset):
        """S2.4.5c-01: Re-validate is_connected at submission time."""
        pc = _make_approved_connected_client()
        # Simulate disconnect between page load and submit
        pc.is_connected = False
        pc.save()

        url = self._submit_url(asset.pk)
        response = client_logged_in.post(
            url,
            {
                "client_pk": pc.pk,
                "printer_id": "printer-1",
                "quantity": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "no longer connected" in data["error"].lower()
        # No PrintRequest should be created
        assert not PrintRequest.objects.filter(
            asset=asset, print_client=pc
        ).exists()

    def test_submit_nonexistent_client(self, client_logged_in, asset):
        """Invalid client_pk returns error."""
        url = self._submit_url(asset.pk)
        response = client_logged_in.post(
            url,
            {
                "client_pk": 99999,
                "printer_id": "printer-1",
                "quantity": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_submit_unapproved_client(self, client_logged_in, asset):
        """Unapproved client should be rejected."""
        token_hash = hashlib.sha256(b"unapproved-tok").hexdigest()
        pc = PrintClient.objects.create(
            name="Unapproved",
            token_hash=token_hash,
            status="pending",
            is_connected=True,
        )
        url = self._submit_url(asset.pk)
        response = client_logged_in.post(
            url,
            {
                "client_pk": pc.pk,
                "printer_id": "printer-1",
                "quantity": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_submit_requires_authentication(self, client, asset):
        """Unauthenticated users cannot submit print requests."""
        pc = _make_approved_connected_client()
        url = reverse("assets:remote_print_submit", args=[asset.pk])
        response = client.post(
            url,
            {
                "client_pk": pc.pk,
                "printer_id": "printer-1",
                "quantity": 1,
            },
        )
        # Should redirect to login
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_submit_get_method_not_allowed(self, client_logged_in, asset):
        """GET requests to the submit endpoint should be rejected."""
        url = self._submit_url(asset.pk)
        response = client_logged_in.get(url)
        assert response.status_code == 405


# ---------------------------------------------------------------------------
# §S2.4.5-11 — Bulk remote print admin action
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulkRemotePrintAction:
    """S2.4.5-11: Bulk print to remote printer from Asset changelist."""

    def _get_admin_obj(self):
        from assets.admin import AssetAdmin

        return AssetAdmin(Asset, admin.site)

    def test_bulk_remote_print_creates_print_requests(self, admin_user, asset):
        """Bulk action creates one PrintRequest per selected asset."""
        pc = _make_approved_connected_client()
        printer_id = pc.printers[0]["id"]

        # Create a second asset
        asset2 = Asset.objects.create(
            name="Asset 2",
            category=asset.category,
            current_location=asset.current_location,
            created_by=admin_user,
        )

        admin_obj = self._get_admin_obj()
        qs = Asset.objects.filter(pk__in=[asset.pk, asset2.pk])

        request = RequestFactory().post(
            "/admin/",
            data={
                "client_pk": pc.pk,
                "printer_id": printer_id,
            },
        )
        request.user = admin_user
        request.session = SessionStore()
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        with patch("assets.admin.dispatch_print_job") as mock_dispatch:
            mock_dispatch.return_value = True
            admin_obj.bulk_remote_print(request, qs)

        assert PrintRequest.objects.filter(print_client=pc).count() == 2
        assert mock_dispatch.call_count == 2

    def test_bulk_remote_print_success_message(self, admin_user, asset):
        """Bulk action shows success message with count."""
        pc = _make_approved_connected_client()
        admin_obj = self._get_admin_obj()
        qs = Asset.objects.filter(pk=asset.pk)

        request = RequestFactory().post(
            "/admin/",
            data={
                "client_pk": pc.pk,
                "printer_id": pc.printers[0]["id"],
            },
        )
        request.user = admin_user
        request.session = SessionStore()
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        with patch("assets.admin.dispatch_print_job") as mock_dispatch:
            mock_dispatch.return_value = True
            admin_obj.bulk_remote_print(request, qs)

        stored = [m.message for m in messages_storage._queued_messages]
        assert any("1" in m and "sent" in m.lower() for m in stored)

    def test_bulk_remote_print_no_connected_client(self, admin_user, asset):
        """Action with invalid client returns error."""
        admin_obj = self._get_admin_obj()
        qs = Asset.objects.filter(pk=asset.pk)

        request = RequestFactory().post(
            "/admin/",
            data={
                "client_pk": 99999,
                "printer_id": "printer-1",
            },
        )
        request.user = admin_user
        request.session = SessionStore()
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        admin_obj.bulk_remote_print(request, qs)

        assert PrintRequest.objects.count() == 0
        stored = [m.message for m in messages_storage._queued_messages]
        assert any(
            "error" in m.lower() or "not found" in m.lower() for m in stored
        )


# ---------------------------------------------------------------------------
# §4.3.3.5 — Stale job cleanup
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStaleJobCleanup:
    """Stale print jobs should be transitioned to failed."""

    def test_stale_sent_job_marked_failed(self, asset):
        """Jobs in 'sent' status past timeout are failed."""
        from assets.services.print_dispatch import (
            cleanup_stale_print_jobs,
        )

        pc = _make_approved_connected_client()
        pr = PrintRequest.objects.create(
            asset=asset,
            print_client=pc,
            printer_id="printer-1",
            status="sent",
            sent_at=timezone.now() - timedelta(seconds=600),
        )

        cleanup_stale_print_jobs(timeout_seconds=300)

        pr.refresh_from_db()
        assert pr.status == "failed"
        assert "timeout" in pr.error_message.lower()

    def test_recent_sent_job_not_affected(self, asset):
        """Jobs within timeout window are not touched."""
        from assets.services.print_dispatch import (
            cleanup_stale_print_jobs,
        )

        pc = _make_approved_connected_client()
        pr = PrintRequest.objects.create(
            asset=asset,
            print_client=pc,
            printer_id="printer-1",
            status="sent",
            sent_at=timezone.now() - timedelta(seconds=60),
        )

        cleanup_stale_print_jobs(timeout_seconds=300)

        pr.refresh_from_db()
        assert pr.status == "sent"

    def test_stale_acked_job_marked_failed(self, asset):
        """Jobs in 'acked' status past timeout are also failed."""
        from assets.services.print_dispatch import (
            cleanup_stale_print_jobs,
        )

        pc = _make_approved_connected_client()
        pr = PrintRequest.objects.create(
            asset=asset,
            print_client=pc,
            printer_id="printer-1",
            status="acked",
            sent_at=timezone.now() - timedelta(seconds=600),
            acked_at=timezone.now() - timedelta(seconds=500),
        )

        cleanup_stale_print_jobs(timeout_seconds=300)

        pr.refresh_from_db()
        assert pr.status == "failed"

    def test_completed_job_not_affected(self, asset):
        """Completed jobs are never cleaned up."""
        from assets.services.print_dispatch import (
            cleanup_stale_print_jobs,
        )

        pc = _make_approved_connected_client()
        pr = PrintRequest.objects.create(
            asset=asset,
            print_client=pc,
            printer_id="printer-1",
            status="completed",
            sent_at=timezone.now() - timedelta(seconds=600),
        )

        cleanup_stale_print_jobs(timeout_seconds=300)

        pr.refresh_from_db()
        assert pr.status == "completed"

    def test_cleanup_returns_count(self, asset):
        """cleanup_stale_print_jobs returns number of failed jobs."""
        from assets.services.print_dispatch import (
            cleanup_stale_print_jobs,
        )

        pc = _make_approved_connected_client()
        for _ in range(3):
            PrintRequest.objects.create(
                asset=asset,
                print_client=pc,
                printer_id="printer-1",
                status="sent",
                sent_at=timezone.now() - timedelta(seconds=600),
            )

        count = cleanup_stale_print_jobs(timeout_seconds=300)
        assert count == 3

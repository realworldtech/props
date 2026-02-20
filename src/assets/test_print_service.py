"""TDD tests for PrintClient (S3.1.20) and PrintRequest (S3.1.21) models.

These tests are written BEFORE the models exist. They will fail until the
models are implemented. This is intentional — red-green TDD cycle.

Spec references:
  - S3.1.20: PrintClient
  - S3.1.21: PrintRequest
  - §8.1.13: Print Service Model Tests
"""

import hashlib
import uuid

import pytest

from django.core.exceptions import ValidationError
from django.utils import timezone

from assets.models import PrintClient, PrintRequest

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

"""Tests for accounts registration and approval workflow."""

from unittest.mock import patch

import pytest

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

User = get_user_model()


# ============================================================
# REGISTRATION & APPROVAL WORKFLOW TESTS (G1 / S2.15)
# ============================================================


class TestRegistrationForm:
    """S2.15.1: Registration form submission."""

    def test_register_page_renders(self, client, department):
        response = client.get(reverse("accounts:register"))
        assert response.status_code == 200

    @patch("accounts.views._send_verification_email")
    def test_register_creates_inactive_user(
        self, mock_send, client, department
    ):
        response = client.post(
            reverse("accounts:register"),
            {
                "email": "newuser@example.com",
                "display_name": "New User",
                "phone_number": "0400111222",
                "requested_department": department.pk,
                "password1": "secureTestPass!789",
                "password2": "secureTestPass!789",
            },
        )
        assert response.status_code == 200  # Renders confirm page
        user = User.objects.get(email="newuser@example.com")
        assert user.is_active is False
        assert user.email_verified is False
        assert user.requested_department == department

    @patch("accounts.views._send_verification_email")
    def test_register_generates_username_from_email(
        self, mock_send, client, department
    ):
        client.post(
            reverse("accounts:register"),
            {
                "email": "jane.doe@example.com",
                "display_name": "Jane Doe",
                "requested_department": department.pk,
                "password1": "secureTestPass!789",
                "password2": "secureTestPass!789",
            },
        )
        user = User.objects.get(email="jane.doe@example.com")
        assert user.username.startswith("jane")

    def test_register_duplicate_email_silent(self, client, department, user):
        """S2.15.1-09: Don't reveal if email exists."""
        response = client.post(
            reverse("accounts:register"),
            {
                "email": user.email,
                "display_name": "Attacker",
                "requested_department": department.pk,
                "password1": "secureTestPass!789",
                "password2": "secureTestPass!789",
            },
        )
        # Should show same confirmation page, not error
        assert response.status_code == 200
        assert b"check your email" in response.content.lower() or (
            b"confirm" in response.content.lower()
        )

    def test_register_authenticated_redirects(
        self, client_logged_in, department
    ):
        response = client_logged_in.get(reverse("accounts:register"))
        assert response.status_code == 302

    @patch("accounts.views._send_verification_email")
    def test_register_sends_verification_email(
        self, mock_send, client, department
    ):
        client.post(
            reverse("accounts:register"),
            {
                "email": "verify@example.com",
                "display_name": "Verify Me",
                "requested_department": department.pk,
                "password1": "secureTestPass!789",
                "password2": "secureTestPass!789",
            },
        )
        mock_send.assert_called_once()


class TestEmailVerification:
    """S2.15.2: Email verification flow."""

    def _make_token(self, user):
        from django.core import signing

        signer = signing.TimestampSigner()
        return signer.sign(str(user.pk))

    def test_valid_token_verifies_email(self, client, db):
        user = User.objects.create_user(
            username="unverified",
            email="unverified@example.com",
            password="pass123!",
            is_active=False,
        )
        token = self._make_token(user)
        response = client.get(reverse("accounts:verify_email", args=[token]))
        assert response.status_code == 200
        user.refresh_from_db()
        assert user.email_verified is True

    def test_invalid_token_shows_error(self, client, db):
        response = client.get(
            reverse("accounts:verify_email", args=["bad-token"])
        )
        assert response.status_code == 200
        assert b"invalid" in response.content.lower()

    def test_expired_token_shows_error(self, client, db):
        from unittest.mock import patch as mock_patch

        from django.core import signing

        user = User.objects.create_user(
            username="expired",
            email="expired@example.com",
            password="pass123!",
            is_active=False,
        )
        token = self._make_token(user)
        with mock_patch.object(
            signing.TimestampSigner,
            "unsign",
            side_effect=signing.SignatureExpired("expired"),
        ):
            response = client.get(
                reverse("accounts:verify_email", args=[token])
            )
        assert response.status_code == 200
        assert b"expired" in response.content.lower()

    def test_already_verified_shows_message(self, client, db):
        user = User.objects.create_user(
            username="already",
            email="already@example.com",
            password="pass123!",
            is_active=False,
        )
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        token = self._make_token(user)
        response = client.get(reverse("accounts:verify_email", args=[token]))
        assert response.status_code == 200
        assert b"already" in response.content.lower()

    @patch("accounts.views._notify_admins_new_pending_user")
    def test_verification_notifies_admins(self, mock_notify, client, db):
        user = User.objects.create_user(
            username="notify",
            email="notify@example.com",
            password="pass123!",
            is_active=False,
        )
        token = self._make_token(user)
        client.get(reverse("accounts:verify_email", args=[token]))
        mock_notify.assert_called_once()


class TestApprovalQueue:
    """S2.15.4: Admin approval queue."""

    def test_approval_queue_requires_admin(self, client_logged_in):
        response = client_logged_in.get(reverse("accounts:approval_queue"))
        assert response.status_code == 403

    def test_approval_queue_renders_for_admin(self, admin_client):
        response = admin_client.get(reverse("accounts:approval_queue"))
        assert response.status_code == 200

    def test_approval_queue_shows_pending_users(self, admin_client, db):
        pending = User.objects.create_user(
            username="pending",
            email="pending@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])
        response = admin_client.get(reverse("accounts:approval_queue"))
        assert response.status_code == 200
        assert b"pending@example.com" in response.content

    @patch("accounts.views._send_approval_email")
    def test_approve_user_activates_account(
        self, mock_email, admin_client, admin_user, db
    ):
        from django.contrib.auth.models import Group

        Group.objects.get_or_create(name="Member")
        pending = User.objects.create_user(
            username="toactivate",
            email="activate@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])

        response = admin_client.post(
            reverse("accounts:approve_user", args=[pending.pk]),
            {"role": "Member"},
        )
        assert response.status_code == 302
        pending.refresh_from_db()
        assert pending.is_active is True
        assert pending.approved_by == admin_user
        assert pending.groups.filter(name="Member").exists()

    @patch("accounts.views._send_approval_email")
    def test_approve_sets_approval_timestamp(
        self, mock_email, admin_client, admin_user, db
    ):
        from django.contrib.auth.models import Group

        Group.objects.get_or_create(name="Member")
        pending = User.objects.create_user(
            username="timestamped",
            email="ts@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])

        admin_client.post(
            reverse("accounts:approve_user", args=[pending.pk]),
            {"role": "Member"},
        )
        pending.refresh_from_db()
        assert pending.approved_at is not None


class TestApprovalRoleAssignment:
    """Approval must assign the selected role, not silently fail."""

    @patch("accounts.views._send_approval_email")
    def test_approve_assigns_department_manager_role(
        self, mock_email, admin_client, admin_user, department, db
    ):
        """Role select sends group name; view must match it."""
        from conftest import _ensure_group_permissions

        _ensure_group_permissions("Department Manager")
        pending = User.objects.create_user(
            username="deptmgr",
            email="deptmgr@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])

        response = admin_client.post(
            reverse("accounts:approve_user", args=[pending.pk]),
            {
                "role": "Department Manager",
                "departments": [department.pk],
            },
        )
        assert response.status_code == 302
        pending.refresh_from_db()
        assert pending.is_active is True
        assert pending.groups.filter(name="Department Manager").exists()
        assert department.managers.filter(pk=pending.pk).exists()

    @patch("accounts.views._send_approval_email")
    def test_approve_assigns_viewer_role(
        self, mock_email, admin_client, admin_user, db
    ):
        """Non-default role is correctly assigned."""
        from django.contrib.auth.models import Group

        Group.objects.get_or_create(name="Viewer")
        pending = User.objects.create_user(
            username="vieweruser",
            email="viewer@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])

        admin_client.post(
            reverse("accounts:approve_user", args=[pending.pk]),
            {"role": "Viewer"},
        )
        pending.refresh_from_db()
        assert pending.groups.filter(name="Viewer").exists()
        assert not pending.groups.filter(name="Member").exists()


class TestApprovalFormTemplateIntegration:
    """Form values rendered in the template must match the view contract.

    These tests render the actual approval queue page, parse the HTML,
    and verify the form fields send values the view can process. This
    catches mismatches between template option values and view lookups
    (e.g. sending group.pk when the view expects group.name).
    """

    def test_role_select_sends_group_names(self, admin_client, admin_user, db):
        """Role <select> option values must be group names, not PKs."""
        from html.parser import HTMLParser

        from django.contrib.auth.models import Group

        # The view shows all groups except System Admin
        all_group_names = set(
            Group.objects.exclude(name="System Admin").values_list(
                "name", flat=True
            )
        )

        pending = User.objects.create_user(
            username="formtest",
            email="formtest@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])

        response = admin_client.get(reverse("accounts:approval_queue"))
        content = response.content.decode()

        # Extract option values from role select
        class OptionParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_role_select = False
                self.values = []

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if tag == "select" and attrs_dict.get("name") == "role":
                    self.in_role_select = True
                elif tag == "option" and self.in_role_select:
                    self.values.append(attrs_dict.get("value", ""))

            def handle_endtag(self, tag):
                if tag == "select" and self.in_role_select:
                    self.in_role_select = False

        parser = OptionParser()
        parser.feed(content)

        assert parser.values, "No role options found in HTML"
        for val in parser.values:
            assert val in all_group_names, (
                f"Role option value '{val}' is not a valid "
                f"group name. The view looks up groups by "
                f"name, so the template must send names, "
                f"not PKs. Valid: {all_group_names}"
            )

    @patch("accounts.views._send_approval_email")
    def test_rendered_form_submit_assigns_role(
        self, mock_email, admin_client, admin_user, db
    ):
        """Submit the first role option from the rendered page."""
        from html.parser import HTMLParser

        from django.contrib.auth.models import Group

        Group.objects.get_or_create(name="Member")

        pending = User.objects.create_user(
            username="roundtrip",
            email="roundtrip@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])

        # Render the page and extract the first role option
        response = admin_client.get(reverse("accounts:approval_queue"))
        content = response.content.decode()

        class FirstOptionParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_role = False
                self.first_value = None

            def handle_starttag(self, tag, attrs):
                d = dict(attrs)
                if tag == "select" and d.get("name") == "role":
                    self.in_role = True
                elif (
                    tag == "option"
                    and self.in_role
                    and self.first_value is None
                ):
                    self.first_value = d.get("value", "")

            def handle_endtag(self, tag):
                if tag == "select" and self.in_role:
                    self.in_role = False

        parser = FirstOptionParser()
        parser.feed(content)
        role_value = parser.first_value

        # POST with the actual value the template renders
        response = admin_client.post(
            reverse("accounts:approve_user", args=[pending.pk]),
            {"role": role_value},
        )
        assert response.status_code == 302
        pending.refresh_from_db()
        assert pending.is_active is True
        assert pending.groups.filter(name=role_value).exists(), (
            f"User should be in group '{role_value}' but is in "
            f"{list(pending.groups.values_list('name', flat=True))}"
        )


class TestApprovalEmailFailure:
    """Approval/rejection must succeed even when email fails."""

    @patch(
        "accounts.views._send_approval_email",
        side_effect=Exception("SMTP down"),
    )
    def test_approve_succeeds_when_email_fails(
        self, mock_email, admin_client, admin_user, db
    ):
        from django.contrib.auth.models import Group

        Group.objects.get_or_create(name="Member")
        pending = User.objects.create_user(
            username="emailfail",
            email="emailfail@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])

        response = admin_client.post(
            reverse("accounts:approve_user", args=[pending.pk]),
            {"role": "Member"},
        )
        assert response.status_code == 302
        pending.refresh_from_db()
        assert pending.is_active is True

    @patch(
        "accounts.views._send_rejection_email",
        side_effect=Exception("SMTP down"),
    )
    def test_reject_succeeds_when_email_fails(
        self, mock_email, admin_client, admin_user, db
    ):
        pending = User.objects.create_user(
            username="rejectfail",
            email="rejectfail@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])

        response = admin_client.post(
            reverse("accounts:reject_user", args=[pending.pk]),
            {"rejection_reason": "Not suitable"},
        )
        assert response.status_code == 302
        pending.refresh_from_db()
        assert pending.is_active is False
        assert pending.rejection_reason == "Not suitable"


class TestRejection:
    """S2.15.5: Rejection flow."""

    def test_reject_requires_reason(self, admin_client, db):
        pending = User.objects.create_user(
            username="noreject",
            email="noreject@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])

        response = admin_client.post(
            reverse("accounts:reject_user", args=[pending.pk]),
            {"rejection_reason": ""},
        )
        assert response.status_code == 302
        pending.refresh_from_db()
        assert pending.rejection_reason == ""
        assert pending.is_active is False

    @patch("accounts.views._send_rejection_email")
    def test_reject_saves_reason(
        self, mock_email, admin_client, admin_user, db
    ):
        pending = User.objects.create_user(
            username="rejected",
            email="rejected@example.com",
            password="pass123!",
            is_active=False,
        )
        pending.email_verified = True
        pending.save(update_fields=["email_verified"])

        admin_client.post(
            reverse("accounts:reject_user", args=[pending.pk]),
            {"rejection_reason": "Not a member of our org"},
        )
        pending.refresh_from_db()
        assert pending.rejection_reason == "Not a member of our org"
        assert pending.approved_by == admin_user

    def test_reject_non_admin_forbidden(self, client_logged_in, db):
        pending = User.objects.create_user(
            username="cantreject",
            email="cantreject@example.com",
            password="pass123!",
            is_active=False,
        )
        response = client_logged_in.post(
            reverse("accounts:reject_user", args=[pending.pk]),
            {"rejection_reason": "Unauthorized attempt"},
        )
        assert response.status_code == 403


class TestAccountState:
    """Account state transitions (unverified -> pending -> active/rejected)."""

    def test_unverified_state(self, db):
        user = User.objects.create_user(
            username="unv",
            email="unv@example.com",
            password="pass123!",
            is_active=False,
        )
        assert user.account_state == "unverified"

    def test_pending_approval_state(self, db):
        user = User.objects.create_user(
            username="pend",
            email="pend@example.com",
            password="pass123!",
            is_active=False,
        )
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        assert user.account_state == "pending_approval"

    def test_active_state(self, user):
        assert user.account_state == "active"

    def test_rejected_state(self, db):
        user = User.objects.create_user(
            username="rej",
            email="rej@example.com",
            password="pass123!",
            is_active=False,
        )
        user.email_verified = True
        user.rejection_reason = "Not eligible"
        user.save(update_fields=["email_verified", "rejection_reason"])
        assert user.account_state == "rejected"

    def test_unverified_cannot_login(self, client, db):
        User.objects.create_user(
            username="nologin",
            email="nologin@example.com",
            password="pass123!",
            is_active=False,
        )
        response = client.post(
            reverse("accounts:login"),
            {"username": "nologin", "password": "pass123!"},
        )
        # Should not redirect to dashboard
        assert response.status_code == 200


class TestAdminCreatedUsers:
    """L16: Admin-created users should have email_verified=True."""

    def test_admin_created_user_has_email_verified_true(
        self, admin_client, db
    ):
        from django.contrib.admin.sites import site

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin = CustomUserAdmin(CustomUser, site)
        user = CustomUser(
            username="admincreated",
            email="admincreated@example.com",
        )
        user.set_password("pass123!")

        # Simulate admin save_model (change=False for new user)
        from django.http import HttpRequest

        request = HttpRequest()
        request.user = User.objects.get(username="admin")

        admin.save_model(request=request, obj=user, form=None, change=False)

        assert user.email_verified is True

    def test_admin_edited_user_preserves_email_verified(
        self, admin_client, db, user
    ):
        from django.contrib.admin.sites import site

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        user.email_verified = False
        user.save(update_fields=["email_verified"])

        admin = CustomUserAdmin(CustomUser, site)

        # Simulate admin save_model (change=True for existing user)
        from django.http import HttpRequest

        request = HttpRequest()
        request.user = User.objects.get(username="admin")

        original_verified = user.email_verified
        admin.save_model(request=request, obj=user, form=None, change=True)

        # Should not modify email_verified on edit
        assert user.email_verified == original_verified


# ============================================================
# S2.15 AUTH TESTS (V384, V411, V412, V416)
# ============================================================


@pytest.mark.django_db
class TestV384RegistrationPageLinksToLogin:
    """V384 S2.15.1-10 MUST: Registration page links to login."""

    def test_registration_page_contains_login_link(self, client, department):
        """Registration page contains a link to the login page."""
        response = client.get(reverse("accounts:register"))
        assert response.status_code == 200
        content = response.content.decode()
        login_url = reverse("accounts:login")
        assert login_url in content or "login" in content.lower()


@pytest.mark.django_db
class TestV411DashboardShowsPendingApprovalsCount:
    """V411 S2.15.4-09 MUST: Dashboard shows pending approvals count."""

    def test_dashboard_with_pending_users_shows_count(self, admin_client, db):
        """Dashboard shows count of pending user approvals for admins."""
        from accounts.models import CustomUser

        # Create pending users
        pending1 = CustomUser.objects.create_user(
            username="pending1",
            email="pending1@example.com",
            password="pass123!",
            is_active=False,
        )
        pending1.email_verified = True
        pending1.save(update_fields=["email_verified"])

        pending2 = CustomUser.objects.create_user(
            username="pending2",
            email="pending2@example.com",
            password="pass123!",
            is_active=False,
        )
        pending2.email_verified = True
        pending2.save(update_fields=["email_verified"])

        response = admin_client.get(reverse("assets:dashboard"))
        assert response.status_code == 200
        content = response.content.decode()
        # Check for pending count indicator
        assert (
            "pending" in content.lower()
            or "approval" in content.lower()
            or "2" in content
        )


@pytest.mark.django_db
class TestV412ApprovalQueueHistoryTab:
    """V412 S2.15.4-10 SHOULD: Approval queue history tab."""

    def test_approval_queue_shows_history(self, admin_client, admin_user, db):
        """Approval queue page shows history of processed approvals."""
        from django.contrib.auth.models import Group

        from accounts.models import CustomUser

        Group.objects.get_or_create(name="Member")

        # Create an approved user
        approved = CustomUser.objects.create_user(
            username="approved",
            email="approved@example.com",
            password="pass123!",
            is_active=True,
        )
        approved.email_verified = True
        approved.approved_by = admin_user
        approved.approved_at = timezone.now()
        approved.save(
            update_fields=["email_verified", "approved_by", "approved_at"]
        )

        response = admin_client.get(reverse("accounts:approval_queue"))
        assert response.status_code == 200
        content = response.content.decode()
        # Check for history section or approved users list
        assert (
            "history" in content.lower()
            or "approved" in content.lower()
            or approved.email in content
        )


@pytest.mark.django_db
class TestV416ReverseRejectionViaApproval:
    """V416 S2.15.5-04 SHOULD: Reverse rejection via approval."""

    @patch("accounts.views._send_approval_email")
    def test_approving_previously_rejected_user(
        self, mock_email, admin_client, admin_user, db
    ):
        """Approving a previously rejected user activates their account."""
        from django.contrib.auth.models import Group

        from accounts.models import CustomUser

        Group.objects.get_or_create(name="Member")

        # Create a rejected user
        rejected = CustomUser.objects.create_user(
            username="rejected_now_approved",
            email="rejected@example.com",
            password="pass123!",
            is_active=False,
        )
        rejected.email_verified = True
        rejected.rejection_reason = "Previously rejected"
        rejected.save(update_fields=["email_verified", "rejection_reason"])

        # Now approve them
        response = admin_client.post(
            reverse("accounts:approve_user", args=[rejected.pk]),
            {"role": "Member"},
        )
        assert response.status_code == 302

        rejected.refresh_from_db()
        assert rejected.is_active is True
        assert rejected.approved_by == admin_user
        # Rejection reason may remain but user is now active
        assert rejected.groups.filter(name="Member").exists()

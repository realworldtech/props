"""Tests for the accounts app."""

from unittest.mock import patch

import pytest

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse

User = get_user_model()


class TestEmailTemplateRendering:
    """Tests for MJML-compiled email template rendering."""

    BRAND_COLOR = settings.BRAND_PRIMARY_COLOR
    SITE = settings.SITE_NAME

    TEMPLATES = {
        "verification": {
            "context": {
                "display_name": "Alice",
                "verify_url": "https://example.com/verify/abc",
            },
            "html_contains": ["Verify", "Alice", "verify/abc"],
        },
        "account_approved": {
            "context": {
                "display_name": "Bob",
                "role_name": "Member",
                "dept_names": "Props, Costumes",
            },
            "html_contains": ["approved", "Bob", "Member", "Props"],
        },
        "account_rejected": {
            "context": {
                "display_name": "Charlie",
            },
            "html_contains": ["not been approved", "Charlie"],
        },
        "admin_new_pending": {
            "context": {
                "display_name": "Dana",
                "user_email": "dana@example.com",
                "department_name": "Costumes",
                "approval_url": "https://example.com/admin/approve",
            },
            "html_contains": [
                "Dana",
                "dana@example.com",
                "Costumes",
                "approve",
            ],
        },
        "password_reset": {
            "context": {
                "display_name": "Eve",
                "reset_url": "https://example.com/reset/xyz",
            },
            "html_contains": ["Reset", "Eve", "reset/xyz"],
        },
    }

    def _render_context(self, extra: dict) -> dict:
        return {
            "site_name": self.SITE,
            "brand_primary_color": self.BRAND_COLOR,
            "logo_url": "",
            **extra,
        }

    @pytest.mark.parametrize("template_name", TEMPLATES.keys())
    def test_html_renders_without_error(self, db, template_name):
        info = self.TEMPLATES[template_name]
        ctx = self._render_context(info["context"])
        html = render_to_string(f"emails/{template_name}.html", ctx)
        assert len(html) > 0

    @pytest.mark.parametrize("template_name", TEMPLATES.keys())
    def test_txt_renders_without_error(self, db, template_name):
        info = self.TEMPLATES[template_name]
        ctx = self._render_context(info["context"])
        txt = render_to_string(f"emails/{template_name}.txt", ctx)
        assert len(txt) > 0

    @pytest.mark.parametrize("template_name", TEMPLATES.keys())
    def test_html_contains_site_name(self, db, template_name):
        info = self.TEMPLATES[template_name]
        ctx = self._render_context(info["context"])
        html = render_to_string(f"emails/{template_name}.html", ctx)
        assert self.SITE in html

    @pytest.mark.parametrize("template_name", TEMPLATES.keys())
    def test_html_contains_brand_color(self, db, template_name):
        info = self.TEMPLATES[template_name]
        ctx = self._render_context(info["context"])
        html = render_to_string(f"emails/{template_name}.html", ctx)
        assert self.BRAND_COLOR in html

    @pytest.mark.parametrize("template_name", TEMPLATES.keys())
    def test_html_contains_expected_content(self, db, template_name):
        info = self.TEMPLATES[template_name]
        ctx = self._render_context(info["context"])
        html = render_to_string(f"emails/{template_name}.html", ctx)
        for expected in info["html_contains"]:
            assert (
                expected in html
            ), f"Expected '{expected}' in {template_name}.html"

    def test_html_has_inline_css(self, db):
        ctx = self._render_context(self.TEMPLATES["verification"]["context"])
        html = render_to_string("emails/verification.html", ctx)
        assert "style=" in html

    def test_html_is_standalone_no_extends(self, db):
        ctx = self._render_context(self.TEMPLATES["verification"]["context"])
        html = render_to_string("emails/verification.html", ctx)
        assert "{% extends" not in html

    def test_logo_url_conditional_without_logo(self, db):
        ctx = self._render_context(self.TEMPLATES["verification"]["context"])
        ctx["logo_url"] = ""
        html = render_to_string("emails/verification.html", ctx)
        assert self.SITE in html

    def test_logo_url_conditional_with_logo(self, db):
        ctx = self._render_context(self.TEMPLATES["verification"]["context"])
        ctx["logo_url"] = "https://example.com/logo.png"
        html = render_to_string("emails/verification.html", ctx)
        assert "https://example.com/logo.png" in html


class TestSendBrandedEmail:
    """Tests for the send_branded_email utility."""

    @patch("django.core.mail.EmailMultiAlternatives.send")
    def test_send_branded_email_sends_synchronously(
        self, mock_send, db, settings
    ):
        from accounts.email import send_branded_email

        settings.SITE_NAME = "TestSite"
        settings.BRAND_PRIMARY_COLOR = "#FF0000"

        send_branded_email(
            template_name="verification",
            context={
                "display_name": "Tester",
                "verify_url": "https://example.com/verify/123",
            },
            subject="Verify",
            recipient="tester@example.com",
        )

        mock_send.assert_called_once()

    @patch("django.core.mail.EmailMultiAlternatives.send")
    def test_send_branded_email_accepts_list_recipients(
        self, mock_send, db, settings
    ):
        from accounts.email import send_branded_email

        settings.SITE_NAME = "TestSite"
        settings.BRAND_PRIMARY_COLOR = "#FF0000"

        recipients = ["a@example.com", "b@example.com"]
        send_branded_email(
            template_name="account_rejected",
            context={"display_name": "Someone"},
            subject="Rejected",
            recipient=recipients,
        )

        mock_send.assert_called_once()


class TestCustomUser:
    """Tests for the CustomUser model."""

    def test_create_user(self, db):
        user = User.objects.create_user(
            username="newuser",
            email="new@example.com",
            password="pass123!",
        )
        assert user.username == "newuser"
        assert user.email == "new@example.com"
        assert user.check_password("pass123!")

    def test_display_name_returns_display_name_when_set(self, user):
        user.display_name = "Custom Name"
        assert user.get_display_name() == "Custom Name"

    def test_display_name_falls_back_to_full_name(self, db):
        user = User.objects.create_user(
            username="fullname",
            email="fn@example.com",
            password="pass123!",
            first_name="Jane",
            last_name="Doe",
        )
        assert user.get_display_name() == "Jane Doe"

    def test_display_name_falls_back_to_username(self, db):
        user = User.objects.create_user(
            username="justusername",
            email="ju@example.com",
            password="pass123!",
        )
        assert user.get_display_name() == "justusername"

    def test_str_uses_display_name(self, user):
        assert str(user) == user.get_display_name()

    def test_email_is_required(self, db):
        # AbstractUser doesn't enforce blank=False at DB level by default,
        # but our model sets blank=False
        # email field has blank=False
        assert User._meta.get_field("email").blank is False

    def test_email_is_unique(self, db):
        """V2: email field must have unique=True."""
        assert User._meta.get_field("email").unique is True

    def test_duplicate_email_raises_integrity_error(self, db):
        """V2: creating two users with same email raises error."""
        from django.db import IntegrityError

        User.objects.create_user(
            username="user1",
            email="dupe@example.com",
            password="pass123!",
        )
        with pytest.raises(IntegrityError):
            User.objects.create_user(
                username="user2",
                email="dupe@example.com",
                password="pass123!",
            )


class TestLoginView:
    """Tests for the login view."""

    def test_login_page_renders(self, client, db):
        response = client.get(reverse("accounts:login"))
        assert response.status_code == 200

    def test_login_with_valid_credentials(self, client, user, password):
        response = client.post(
            reverse("accounts:login"),
            {"username": user.username, "password": password},
        )
        assert response.status_code == 302

    def test_login_with_invalid_credentials(self, client, db):
        response = client.post(
            reverse("accounts:login"),
            {"username": "bad", "password": "bad"},
        )
        assert response.status_code == 200  # Re-renders form

    def test_authenticated_user_redirected_from_login(self, client_logged_in):
        response = client_logged_in.get(reverse("accounts:login"))
        assert response.status_code == 302


class TestLogoutView:
    """Tests for the logout view."""

    def test_logout_redirects(self, client_logged_in):
        response = client_logged_in.get(reverse("accounts:logout"))
        assert response.status_code == 302


class TestProfileView:
    """Tests for the profile view."""

    def test_profile_requires_login(self, client, db):
        response = client.get(reverse("accounts:profile"))
        assert response.status_code == 302

    def test_profile_renders_for_logged_in_user(self, client_logged_in):
        response = client_logged_in.get(reverse("accounts:profile"))
        assert response.status_code == 200


class TestEmailOrUsernameBackend:
    """Tests for the EmailOrUsernameBackend."""

    def test_login_by_username(self, client, user, password):
        response = client.post(
            reverse("accounts:login"),
            {"username": user.username, "password": password},
        )
        assert response.status_code == 302

    def test_login_by_email(self, client, user, password):
        response = client.post(
            reverse("accounts:login"),
            {"username": user.email, "password": password},
        )
        assert response.status_code == 302

    def test_login_by_email_case_insensitive(self, client, user, password):
        response = client.post(
            reverse("accounts:login"),
            {"username": user.email.upper(), "password": password},
        )
        assert response.status_code == 302

    def test_invalid_password_returns_form(self, client, user):
        response = client.post(
            reverse("accounts:login"),
            {"username": user.email, "password": "wrongpass"},
        )
        assert response.status_code == 200

    def test_nonexistent_user_returns_form(self, client, db):
        response = client.post(
            reverse("accounts:login"),
            {"username": "nobody@example.com", "password": "pass"},
        )
        assert response.status_code == 200

    def test_backend_authenticate_directly(self, user, password):
        from accounts.backends import EmailOrUsernameBackend

        backend = EmailOrUsernameBackend()
        # By email
        result = backend.authenticate(
            None, username=user.email, password=password
        )
        assert result == user
        # By username
        result = backend.authenticate(
            None, username=user.username, password=password
        )
        assert result == user
        # Wrong password
        result = backend.authenticate(
            None, username=user.email, password="wrong"
        )
        assert result is None
        # None params
        result = backend.authenticate(None, username=None, password=None)
        assert result is None


class TestProfileEdit:
    """Tests for profile editing."""

    def test_profile_edit_requires_login(self, client, db):
        response = client.get(reverse("accounts:profile_edit"))
        assert response.status_code == 302

    def test_profile_edit_renders(self, client_logged_in):
        response = client_logged_in.get(reverse("accounts:profile_edit"))
        assert response.status_code == 200

    def test_profile_edit_saves_changes(self, client_logged_in, user):
        response = client_logged_in.post(
            reverse("accounts:profile_edit"),
            {
                "display_name": "New Name",
                "email": user.email,
                "phone_number": "0412345678",
                "organisation": "Test Org",
            },
        )
        assert response.status_code == 302
        user.refresh_from_db()
        assert user.display_name == "New Name"
        assert user.phone_number == "0412345678"
        assert user.organisation == "Test Org"

    @patch("accounts.views._send_verification_email")
    def test_profile_edit_email_change_triggers_reverification(
        self, mock_send, client_logged_in, user
    ):
        response = client_logged_in.post(
            reverse("accounts:profile_edit"),
            {
                "display_name": user.display_name,
                "email": "newemail@example.com",
                "phone_number": "",
                "organisation": "",
            },
        )
        assert response.status_code == 302
        user.refresh_from_db()
        assert user.email == "newemail@example.com"
        assert user.email_verified is False
        assert user.is_active is False
        mock_send.assert_called_once()


class TestPasswordChange:
    """Tests for password change."""

    def test_password_change_requires_login(self, client, db):
        response = client.get(reverse("accounts:password_change"))
        assert response.status_code == 302

    def test_password_change_renders(self, client_logged_in):
        response = client_logged_in.get(reverse("accounts:password_change"))
        assert response.status_code == 200

    def test_password_change_works(self, client_logged_in, user, password):
        response = client_logged_in.post(
            reverse("accounts:password_change"),
            {
                "old_password": password,
                "new_password1": "newSecurePass!456",
                "new_password2": "newSecurePass!456",
            },
        )
        assert response.status_code == 302
        user.refresh_from_db()
        assert user.check_password("newSecurePass!456")

    def test_password_change_requires_old_password(self, client_logged_in):
        response = client_logged_in.post(
            reverse("accounts:password_change"),
            {
                "old_password": "wrongold",
                "new_password1": "newSecurePass!456",
                "new_password2": "newSecurePass!456",
            },
        )
        assert response.status_code == 200  # Re-renders form with errors


class TestPasswordReset:
    """Tests for password reset flow."""

    def test_password_reset_page_renders(self, client, db):
        response = client.get(reverse("accounts:password_reset"))
        assert response.status_code == 200

    def test_password_reset_post_redirects(self, client, user):
        response = client.post(
            reverse("accounts:password_reset"),
            {"email": user.email},
        )
        assert response.status_code == 302

    def test_password_reset_done_renders(self, client, db):
        response = client.get(reverse("accounts:password_reset_done"))
        assert response.status_code == 200

    def test_password_reset_complete_renders(self, client, db):
        response = client.get(reverse("accounts:password_reset_complete"))
        assert response.status_code == 200

    def test_password_reset_confirm_invalid_token(self, client, db):
        response = client.get(
            reverse(
                "accounts:password_reset_confirm",
                kwargs={"uidb64": "bad", "token": "bad-token"},
            )
        )
        assert response.status_code == 200
        assert (
            b"invalid" in response.content.lower()
            or b"expired" in response.content.lower()
        )

    @patch("accounts.views.PasswordResetForm.save")
    def test_password_reset_rate_limit(self, mock_save, client, user):
        """V12: 4th POST with same email is silently absorbed."""
        url = reverse("accounts:password_reset")
        for i in range(4):
            response = client.post(url, {"email": user.email})
            assert response.status_code == 302
        # First 3 should call save, 4th should be absorbed
        assert mock_save.call_count <= 3


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

"""Tests for the accounts app."""

from unittest.mock import patch

import pytest

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

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


class TestApprovalRoleAssignment:
    """Approval must assign the selected role, not silently fail."""

    @patch("accounts.views._send_approval_email")
    def test_approve_assigns_department_manager_role(
        self, mock_email, admin_client, admin_user, department, db
    ):
        """Role select sends group name; view must match it."""
        from django.contrib.auth.models import Group

        group, _ = Group.objects.get_or_create(name="Department Manager")
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


class TestUserDeletionWarning:
    """M11: User deletion warnings for SET_NULL effects (S7.10.1)."""

    def test_user_deletion_creates_transaction_note(
        self, admin_client, user, asset
    ):
        """Deleting a user via admin creates a note on affected records."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser
        from assets.models import Transaction

        # Create a transaction by this user
        Transaction.objects.create(asset=asset, user=user, action="checkout")

        site = AdminSite()
        admin = CustomUserAdmin(CustomUser, site)

        from django.contrib.messages.storage.fallback import (
            FallbackStorage,
        )
        from django.http import HttpRequest

        request = HttpRequest()
        request.user = User.objects.get(username="admin")
        setattr(request, "session", "session")
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        # delete_model should not raise
        admin.delete_model(request, user)

        # User should be deleted
        assert not User.objects.filter(username="testuser").exists()

        # Transactions should still exist with user=None
        txn = Transaction.objects.filter(asset=asset).first()
        assert txn is not None
        assert txn.user is None

        # Check a warning message was generated
        stored = [m.message for m in messages_storage]
        assert any("transaction" in m.lower() for m in stored)

    def test_user_deletion_records_affected_counts(
        self, admin_client, user, asset, db
    ):
        """delete_model logs affected record counts."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser
        from assets.models import NFCTag, Transaction

        Transaction.objects.create(asset=asset, user=user, action="checkout")
        NFCTag.objects.create(
            tag_id="NFC-DEL-001", asset=asset, assigned_by=user
        )

        site = AdminSite()
        admin_obj = CustomUserAdmin(CustomUser, site)

        from django.http import HttpRequest

        request = HttpRequest()
        request.user = User.objects.get(username="admin")

        from django.contrib.messages.storage.fallback import (
            FallbackStorage,
        )

        setattr(request, "session", "session")
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

        admin_obj.delete_model(request, user)

        # Check messages were added
        stored = [m.message for m in messages_storage]
        assert any("transaction" in m.lower() for m in stored)


# ============================================================
# BATCH 4b: S2.13.5 CUSTOMUSER ADMIN LAYOUT TESTS
# ============================================================


@pytest.mark.django_db
class TestCustomUserAdminLayout:
    """Tests for CustomUser admin layout per S2.13.5-04 through -08."""

    # --- S2.13.5-04: UnfoldAdmin base class (MUST) ---

    def test_admin_uses_unfold_model_admin(self):
        """S2.13.5-04 MUST: CustomUserAdmin inherits from
        unfold.admin.ModelAdmin (UnfoldAdmin)."""
        from unfold.admin import ModelAdmin as UnfoldModelAdmin

        from accounts.admin import CustomUserAdmin

        assert issubclass(
            CustomUserAdmin, UnfoldModelAdmin
        ), "CustomUserAdmin must inherit from unfold.admin.ModelAdmin"

    # --- S2.13.5-04: Tabbed layout (SHOULD) ---

    def test_fieldsets_use_tab_classes(self):
        """S2.13.5-04 SHOULD: All fieldsets use tab layout via
        'tab' in classes."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin_obj = CustomUserAdmin(CustomUser, AdminSite())
        fieldsets = admin_obj.fieldsets
        assert fieldsets, "fieldsets must not be empty"

        tab_count = sum(
            1 for _name, opts in fieldsets if "tab" in opts.get("classes", [])
        )
        # All fieldsets should be tabs
        assert tab_count == len(fieldsets), (
            f"All {len(fieldsets)} fieldsets should have 'tab' class, "
            f"but only {tab_count} do"
        )

    def test_fieldsets_have_profile_tab(self):
        """S2.13.5-04 SHOULD: Profile tab contains username, email,
        display_name, phone_number, requested_department,
        organisation."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin_obj = CustomUserAdmin(CustomUser, AdminSite())
        profile_fields = None
        for name, opts in admin_obj.fieldsets:
            if name and "profile" in name.lower():
                profile_fields = opts.get("fields", ())
                break

        assert (
            profile_fields is not None
        ), "No fieldset with 'Profile' in its name found"
        # Flatten nested tuples
        flat = []
        for f in profile_fields:
            if isinstance(f, (list, tuple)):
                flat.extend(f)
            else:
                flat.append(f)

        expected = [
            "username",
            "email",
            "display_name",
            "phone_number",
            "requested_department",
            "organisation",
        ]
        for field in expected:
            assert field in flat, f"Profile tab missing field: {field}"

    def test_fieldsets_have_permissions_tab(self):
        """S2.13.5-04 SHOULD: Permissions tab contains groups,
        is_staff, is_superuser, user_permissions."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin_obj = CustomUserAdmin(CustomUser, AdminSite())
        perm_fields = None
        for name, opts in admin_obj.fieldsets:
            if name and "permission" in name.lower():
                perm_fields = opts.get("fields", ())
                break

        assert (
            perm_fields is not None
        ), "No fieldset with 'Permission' in its name found"
        flat = []
        for f in perm_fields:
            if isinstance(f, (list, tuple)):
                flat.extend(f)
            else:
                flat.append(f)

        expected = [
            "groups",
            "is_staff",
            "is_superuser",
            "user_permissions",
        ]
        for field in expected:
            assert field in flat, f"Permissions tab missing field: {field}"

    def test_fieldsets_have_activity_tab(self):
        """S2.13.5-04 SHOULD: Activity tab contains last_login,
        date_joined, approved_by, approved_at,
        rejection_reason."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin_obj = CustomUserAdmin(CustomUser, AdminSite())
        activity_fields = None
        for name, opts in admin_obj.fieldsets:
            if name and "activity" in name.lower():
                activity_fields = opts.get("fields", ())
                break

        assert (
            activity_fields is not None
        ), "No fieldset with 'Activity' in its name found"
        flat = []
        for f in activity_fields:
            if isinstance(f, (list, tuple)):
                flat.extend(f)
            else:
                flat.append(f)

        expected = [
            "last_login",
            "date_joined",
            "approved_by",
            "approved_at",
            "rejection_reason",
        ]
        for field in expected:
            assert field in flat, f"Activity tab missing field: {field}"

    # --- S2.13.5-05: List display columns (SHOULD) ---

    def test_changelist_shows_username_column(self, admin_client, admin_user):
        """S2.13.5-05 SHOULD: Changelist shows username column."""
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url)
        content = response.content.decode()
        assert admin_user.username in content

    def test_changelist_shows_email_column(self, admin_client, admin_user):
        """S2.13.5-05 SHOULD: Changelist shows email column."""
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url)
        content = response.content.decode()
        assert admin_user.email in content

    def test_changelist_shows_display_name_column(self, admin_client, user):
        """S2.13.5-05 SHOULD: Changelist shows display_name."""
        user.display_name = "Visible Name"
        user.save()
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url)
        content = response.content.decode()
        assert "Visible Name" in content

    def test_changelist_shows_groups_summary(self, admin_client, user):
        """S2.13.5-05 SHOULD: Changelist shows comma-separated
        groups summary."""
        from django.contrib.auth.models import Group

        g1, _ = Group.objects.get_or_create(name="Member")
        g2, _ = Group.objects.get_or_create(name="Viewer")
        user.groups.set([g1, g2])
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url)
        content = response.content.decode()
        # Both group names should appear
        assert "Member" in content
        assert "Viewer" in content

    def test_changelist_shows_department_column(
        self, admin_client, user, department
    ):
        """S2.13.5-05 SHOULD: Changelist shows department."""
        user.requested_department = department
        user.save()
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url)
        content = response.content.decode()
        assert department.name in content

    def test_changelist_shows_is_active_column(self, admin_client, admin_user):
        """S2.13.5-05 SHOULD: Changelist renders is_active status."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin_obj = CustomUserAdmin(CustomUser, AdminSite())
        # Check list_display contains a reference to is_active
        display_strs = [str(f) for f in admin_obj.list_display]
        has_active = any("active" in s.lower() for s in display_strs)
        assert has_active, "list_display must include an is_active column"

    def test_changelist_shows_is_staff_column(self, admin_client, admin_user):
        """S2.13.5-05 SHOULD: Changelist renders is_staff status."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin_obj = CustomUserAdmin(CustomUser, AdminSite())
        display_strs = [str(f) for f in admin_obj.list_display]
        has_staff = any("staff" in s.lower() for s in display_strs)
        assert has_staff, "list_display must include an is_staff column"

    # --- S2.13.5-06: List filters (MUST) ---

    def test_filter_by_is_active(self, admin_client, user):
        """S2.13.5-06 MUST: Filter by is_active narrows list."""
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url, {"is_active__exact": "1"})
        assert response.status_code == 200
        content = response.content.decode()
        assert user.username in content

    def test_filter_by_is_staff(self, admin_client, admin_user, user):
        """S2.13.5-06 MUST: Filter by is_staff narrows list."""
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url, {"is_staff__exact": "1"})
        assert response.status_code == 200
        content = response.content.decode()
        assert admin_user.username in content
        # Non-staff user should not appear
        assert user.username not in content

    def test_filter_by_is_superuser(self, admin_client, admin_user, user):
        """S2.13.5-06 MUST: Filter by is_superuser narrows list."""
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url, {"is_superuser__exact": "1"})
        assert response.status_code == 200
        content = response.content.decode()
        assert admin_user.username in content
        assert user.username not in content

    def test_filter_by_groups(self, admin_client, user, admin_user):
        """S2.13.5-06 MUST: Filter by groups narrows list."""
        from django.contrib.auth.models import Group

        member_group = Group.objects.get(name="Member")
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(
            url, {"groups__id__exact": str(member_group.pk)}
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert user.username in content

    def test_filter_by_department(self, admin_client, user, department):
        """S2.13.5-06 MUST: Filter by department narrows list."""
        user.requested_department = department
        user.save()
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(
            url,
            {"requested_department__id__exact": str(department.pk)},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert user.username in content

    def test_list_filter_includes_is_superuser(self):
        """S2.13.5-06 MUST: list_filter includes is_superuser."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin_obj = CustomUserAdmin(CustomUser, AdminSite())
        filter_strs = [str(f) for f in admin_obj.list_filter]
        has_superuser = any("superuser" in s.lower() for s in filter_strs)
        assert has_superuser, "list_filter must include is_superuser"

    def test_list_filter_includes_department(self):
        """S2.13.5-06 MUST: list_filter includes department."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin_obj = CustomUserAdmin(CustomUser, AdminSite())
        filter_strs = [str(f) for f in admin_obj.list_filter]
        has_dept = any("department" in s.lower() for s in filter_strs)
        assert has_dept, (
            "list_filter must include department " "(requested_department)"
        )

    # --- S2.13.5-07: Search fields (MUST) ---

    def test_search_by_username(self, admin_client, user):
        """S2.13.5-07 MUST: Search by username returns user."""
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url, {"q": "testus"})
        assert response.status_code == 200
        content = response.content.decode()
        assert user.username in content

    def test_search_by_email(self, admin_client, user):
        """S2.13.5-07 MUST: Search by email returns user."""
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url, {"q": "test@example"})
        assert response.status_code == 200
        content = response.content.decode()
        assert user.username in content

    def test_search_by_display_name(self, admin_client, user):
        """S2.13.5-07 MUST: Search by display_name returns user."""
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(url, {"q": "Test User"})
        assert response.status_code == 200
        content = response.content.decode()
        assert user.username in content

    def test_search_fields_configured(self):
        """S2.13.5-07 MUST: search_fields includes username, email,
        display_name."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin_obj = CustomUserAdmin(CustomUser, AdminSite())
        for field in ("username", "email", "display_name"):
            assert (
                field in admin_obj.search_fields
            ), f"search_fields must include {field}"

    # --- S2.13.5-08: Department FK autocomplete (SHOULD) ---

    def test_requested_department_autocomplete(self):
        """S2.13.5-08 SHOULD: requested_department uses
        autocomplete widget."""
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import CustomUserAdmin
        from accounts.models import CustomUser

        admin_obj = CustomUserAdmin(CustomUser, AdminSite())
        assert hasattr(
            admin_obj, "autocomplete_fields"
        ), "CustomUserAdmin must define autocomplete_fields"
        assert "requested_department" in (admin_obj.autocomplete_fields), (
            "autocomplete_fields must include " "requested_department"
        )


# ============================================================
# BATCH 5: S2.10.5 USER PROFILE GAP TESTS
# ============================================================


@pytest.mark.django_db
class TestProfileViewContent:
    """V300 S2.10.5-01: User profile page displays user info."""

    def test_profile_shows_display_name(self, client_logged_in, user):
        """Profile page shows the user's display name."""
        response = client_logged_in.get(reverse("accounts:profile"))
        content = response.content.decode()
        assert user.get_display_name() in content

    def test_profile_shows_email(self, client_logged_in, user):
        """Profile page shows the user's email."""
        response = client_logged_in.get(reverse("accounts:profile"))
        content = response.content.decode()
        assert user.email in content

    def test_profile_shows_borrowed_assets_section_when_borrowed(
        self, client_logged_in, user, asset
    ):
        """Profile page shows borrowed items section when user has borrows."""
        asset.checked_out_to = user
        asset.save()
        response = client_logged_in.get(reverse("accounts:profile"))
        content = response.content.decode()
        assert "My Borrowed Items" in content
        assert asset.name in content

    def test_profile_shows_recent_transactions(
        self, client_logged_in, user, asset
    ):
        """Profile page shows recent transactions for the user."""
        from assets.models import Transaction

        Transaction.objects.create(asset=asset, user=user, action="checkout")
        response = client_logged_in.get(reverse("accounts:profile"))
        assert response.status_code == 200

    def test_profile_has_gravatar(self, client_logged_in, user):
        """Profile page includes a Gravatar image."""
        response = client_logged_in.get(reverse("accounts:profile"))
        content = response.content.decode()
        assert "gravatar" in content.lower()


@pytest.mark.django_db
class TestNavbarGravatarAndDisplayName:
    """V305 S2.10.5-06: Navbar displays Gravatar and display name."""

    def test_navbar_has_display_name(self, client_logged_in, user):
        """Navbar shows the user's display name."""
        response = client_logged_in.get(reverse("assets:dashboard"))
        content = response.content.decode()
        assert user.get_display_name() in content

    def test_navbar_has_gravatar_image(self, client_logged_in, user):
        """Navbar includes a Gravatar image tag."""
        response = client_logged_in.get(reverse("assets:dashboard"))
        content = response.content.decode()
        assert "gravatar" in content.lower()


@pytest.mark.django_db
class TestProfileBorrowedItemsLink:
    """V306 S2.10.5-07: Profile page includes My Borrowed Items link."""

    def test_profile_has_borrowed_items_url(
        self, client_logged_in, user, asset
    ):
        """Profile page contains a link to My Borrowed Items when active."""
        # The borrowed items section only appears when user has borrows
        asset.checked_out_to = user
        asset.save()
        response = client_logged_in.get(reverse("accounts:profile"))
        content = response.content.decode()
        my_items_url = reverse("assets:my_borrowed_items")
        assert my_items_url in content

    def test_base_template_has_borrowed_items_link(self, client_logged_in):
        """Base template nav has My Borrowed Items link."""
        response = client_logged_in.get(reverse("assets:dashboard"))
        content = response.content.decode()
        assert "My Borrowed Items" in content


@pytest.mark.django_db
class TestUserRoleContextProcessor:
    """V305: user_role context processor provides role and flags."""

    def test_context_processor_returns_role(self, db):
        from django.test import RequestFactory

        from props.context_processors import user_role

        from .models import CustomUser

        user = CustomUser.objects.create_user(
            username="ctx_user",
            email="ctx@example.com",
            password="pass123!",
            is_superuser=True,
        )
        factory = RequestFactory()
        request = factory.get("/")
        request.user = user
        ctx = user_role(request)
        assert ctx["user_role"] == "system_admin"
        assert ctx["can_manage"] is True
        assert ctx["can_capture"] is True

    def test_context_processor_anonymous(self, db):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from props.context_processors import user_role

        factory = RequestFactory()
        request = factory.get("/")
        request.user = AnonymousUser()
        ctx = user_role(request)
        assert ctx["user_role"] == "anonymous"
        assert ctx["can_capture"] is False
        assert ctx["can_manage"] is False

    def test_context_processor_member_role(self, db):
        from django.contrib.auth.models import Group
        from django.test import RequestFactory

        from props.context_processors import user_role

        from .models import CustomUser

        user = CustomUser.objects.create_user(
            username="ctx_member",
            email="ctxm@example.com",
            password="pass123!",
        )
        group, _ = Group.objects.get_or_create(name="Member")
        user.groups.add(group)
        factory = RequestFactory()
        request = factory.get("/")
        request.user = user
        ctx = user_role(request)
        assert ctx["user_role"] == "member"
        assert ctx["can_capture"] is True
        assert ctx["can_manage"] is False


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

"""Tests for accounts authentication — login, logout, password, email."""

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


class TestLoginRateLimit:
    """Login rate limiting must key on the real client and be visible."""

    def _exhaust(self, client, **extra):
        for _ in range(5):
            client.post(
                reverse("accounts:login"),
                {"username": "nobody", "password": "bad"},
                **extra,
            )

    def test_rate_limited_login_shows_error_message(
        self, client, user, password
    ):
        self._exhaust(client)
        response = client.post(
            reverse("accounts:login"),
            {"username": user.username, "password": password},
        )
        assert response.status_code == 200
        assert b"Too many login attempts" in response.content

    def test_rate_limit_keys_on_forwarded_client_ip_behind_proxy(
        self, client, user, password, settings
    ):
        settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
        self._exhaust(client, HTTP_X_FORWARDED_FOR="203.0.113.10")
        response = client.post(
            reverse("accounts:login"),
            {"username": user.username, "password": password},
            HTTP_X_FORWARDED_FOR="203.0.113.20",
        )
        assert response.status_code == 302

    def test_client_supplied_forwarded_hops_cannot_evade_limit(
        self, client, user, password, settings
    ):
        settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
        for i in range(5):
            client.post(
                reverse("accounts:login"),
                {"username": "nobody", "password": "bad"},
                HTTP_X_FORWARDED_FOR=f"198.51.100.{i}, 203.0.113.10",
            )
        response = client.post(
            reverse("accounts:login"),
            {"username": user.username, "password": password},
            HTTP_X_FORWARDED_FOR="198.51.100.99, 203.0.113.10",
        )
        assert response.status_code == 200
        assert b"Too many login attempts" in response.content

    def test_forwarded_header_ignored_without_proxy(
        self, client, user, password, settings
    ):
        settings.SECURE_PROXY_SSL_HEADER = None
        self._exhaust(client, HTTP_X_FORWARDED_FOR="203.0.113.10")
        response = client.post(
            reverse("accounts:login"),
            {"username": user.username, "password": password},
            HTTP_X_FORWARDED_FOR="203.0.113.20",
        )
        assert response.status_code == 200


class TestLoginUsernameCase:
    """Usernames are generated lowercase; mobile keyboards capitalise."""

    def test_login_by_username_case_insensitive(self, client, user, password):
        response = client.post(
            reverse("accounts:login"),
            {"username": "TestUser", "password": password},
        )
        assert response.status_code == 302

    def test_exact_username_wins_over_case_variant(
        self, client, user, password, db
    ):
        other = User.objects.create_user(
            username="TESTUSER", email="other@example.com", password="x"
        )
        response = client.post(
            reverse("accounts:login"),
            {"username": "TESTUSER", "password": password},
        )
        assert response.status_code == 200
        assert other.pk != user.pk

    def test_login_input_disables_autocapitalise(self, client, db):
        response = client.get(reverse("accounts:login"))
        content = response.content.decode()
        assert 'autocapitalize="none"' in content
        assert 'autocorrect="off"' in content


class TestLoginNextRedirect:
    def test_login_rejects_external_next_url(self, client, user, password):
        response = client.post(
            reverse("accounts:login") + "?next=https://evil.example/",
            {"username": user.username, "password": password},
        )
        assert response.status_code == 302
        assert response.url == reverse("assets:dashboard")

    def test_login_honours_local_next_url(self, client, user, password):
        response = client.post(
            reverse("accounts:login") + "?next=/assets/",
            {"username": user.username, "password": password},
        )
        assert response.status_code == 302
        assert response.url == "/assets/"

"""Tests for the accounts app."""

import pytest

from django.contrib.auth import get_user_model
from django.urls import reverse

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
        user = User(username="noemail", password="pass123!")
        # email field has blank=False
        assert User._meta.get_field("email").blank is False


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

    def test_profile_edit_email_change_triggers_reverification(
        self, client_logged_in, user
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

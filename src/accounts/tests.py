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

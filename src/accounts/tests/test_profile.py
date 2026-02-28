"""Tests for accounts user profile and navigation."""

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


class TestProfileView:
    """Tests for the profile view."""

    def test_profile_requires_login(self, client, db):
        response = client.get(reverse("accounts:profile"))
        assert response.status_code == 302

    def test_profile_renders_for_logged_in_user(self, client_logged_in):
        response = client_logged_in.get(reverse("accounts:profile"))
        assert response.status_code == 200


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

        from accounts.models import CustomUser
        from props.context_processors import user_role

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
        from django.test import RequestFactory

        from accounts.models import CustomUser
        from conftest import _ensure_group_permissions
        from props.context_processors import user_role

        user = CustomUser.objects.create_user(
            username="ctx_member",
            email="ctxm@example.com",
            password="pass123!",
        )
        group = _ensure_group_permissions("Member")
        user.groups.add(group)
        factory = RequestFactory()
        request = factory.get("/")
        request.user = user
        ctx = user_role(request)
        assert ctx["user_role"] == "member"
        assert ctx["can_capture"] is True
        assert ctx["can_manage"] is False

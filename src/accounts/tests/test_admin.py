"""Tests for accounts Django admin interface."""

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
        """S2.13.5-05 SHOULD: Changelist shows managed departments."""
        department.managers.add(user)
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
        """S2.13.5-06 MUST: Filter by managed department narrows list."""
        department.managers.add(user)
        url = reverse("admin:accounts_customuser_changelist")
        response = admin_client.get(
            url,
            {"managed_departments__id__exact": str(department.pk)},
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
            "list_filter must include department " "(managed_departments)"
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


@pytest.mark.django_db
class TestGroupAdminLayout:
    """S2.13.5-09: Group admin uses UnfoldAdmin with user count."""

    # --- S2.13.5-09 MUST: Group admin uses UnfoldAdmin ---

    def test_group_admin_registered(self):
        """S2.13.5-09 MUST: Group model has a custom admin registered."""
        from django.contrib.admin.sites import site
        from django.contrib.auth.models import Group

        assert (
            Group in site._registry
        ), "Group must be registered in the admin site"

    def test_group_admin_uses_unfold(self):
        """S2.13.5-09 MUST: Group admin uses UnfoldAdmin as base class
        (per S2.13.1-01)."""
        from unfold.admin import ModelAdmin as UnfoldModelAdmin

        from django.contrib.admin.sites import site
        from django.contrib.auth.models import Group

        admin_obj = site._registry[Group]
        assert isinstance(admin_obj, UnfoldModelAdmin), (
            "Group admin must use UnfoldAdmin (unfold ModelAdmin) "
            f"as base class, got {type(admin_obj).__mro__}"
        )

    # --- S2.13.5-09 MUST: list view accessible ---

    def test_group_changelist_accessible(self, admin_client):
        """S2.13.5-09 MUST: Group changelist is accessible to admin."""
        url = reverse("admin:auth_group_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200

    # --- S2.13.5-09 MUST: user count annotation + display ---

    def test_user_count_in_list_display(self):
        """S2.13.5-09 MUST: list_display includes user_count."""
        from django.contrib.admin.sites import site
        from django.contrib.auth.models import Group

        admin_obj = site._registry[Group]
        # Check that there is a user_count column in list_display
        # It might be a method name or a field name
        list_display = admin_obj.list_display
        has_user_count = any(
            "user_count" in str(col) or "member_count" in str(col)
            for col in list_display
        )
        assert has_user_count, (
            "Group admin list_display must include a user count column, "
            f"got {list_display}"
        )

    def test_user_count_accuracy_populated_group(self, admin_client, db):
        """S2.13.5-09 MUST: Group with 3 users shows count of 3."""
        from django.contrib.admin.sites import site
        from django.contrib.auth.models import Group
        from django.test import RequestFactory

        from accounts.models import CustomUser

        group = Group.objects.create(name="Test Populated Group")
        for i in range(3):
            u = CustomUser.objects.create_user(
                username=f"counttest{i}",
                email=f"counttest{i}@example.com",
                password="testpass123!",
            )
            u.groups.add(group)

        # Verify via annotated queryset
        admin_obj = site._registry[Group]
        factory = RequestFactory()
        request = factory.get(reverse("admin:auth_group_changelist"))
        request.user = CustomUser.objects.filter(is_superuser=True).first()
        qs = admin_obj.get_queryset(request)
        annotated = qs.get(pk=group.pk)
        assert hasattr(
            annotated, "user_count"
        ), "Queryset must annotate user_count"
        assert (
            annotated.user_count == 3
        ), f"Expected user_count=3, got {annotated.user_count}"

    def test_user_count_accuracy_empty_group(self, admin_client, db):
        """S2.13.5-09 MUST: Group with 0 users shows count of 0."""
        from django.contrib.admin.sites import site
        from django.contrib.auth.models import Group
        from django.test import RequestFactory

        from accounts.models import CustomUser

        group = Group.objects.create(name="Empty Test Group")

        # Verify via annotated queryset
        admin_obj = site._registry[Group]
        factory = RequestFactory()
        request = factory.get(reverse("admin:auth_group_changelist"))
        request.user = CustomUser.objects.filter(is_superuser=True).first()
        qs = admin_obj.get_queryset(request)
        annotated = qs.get(pk=group.pk)
        assert hasattr(
            annotated, "user_count"
        ), "Queryset must annotate user_count"
        assert (
            annotated.user_count == 0
        ), f"Expected user_count=0, got {annotated.user_count}"

    def test_queryset_annotates_user_count(self, admin_client, db):
        """S2.13.5-09 MUST: queryset is annotated with user count."""
        from django.contrib.admin.sites import site
        from django.contrib.auth.models import Group
        from django.test import RequestFactory

        from accounts.models import CustomUser

        group = Group.objects.create(name="Annotated Count Group")
        for i in range(2):
            u = CustomUser.objects.create_user(
                username=f"annottest{i}",
                email=f"annottest{i}@example.com",
                password="testpass123!",
            )
            u.groups.add(group)

        admin_obj = site._registry[Group]
        factory = RequestFactory()
        request = factory.get(reverse("admin:auth_group_changelist"))
        # Superuser needed for admin access
        superuser = CustomUser.objects.filter(is_superuser=True).first()
        request.user = superuser

        qs = admin_obj.get_queryset(request)
        annotated_group = qs.get(pk=group.pk)
        assert hasattr(
            annotated_group, "user_count"
        ), "Group queryset must be annotated with 'user_count'"
        assert (
            annotated_group.user_count == 2
        ), f"Expected user_count=2, got {annotated_group.user_count}"

    # --- S2.13.5-09 SHOULD: link to filtered user list ---

    def test_filtered_user_list_link(self, admin_client, db):
        """S2.13.5-09 SHOULD: Group list row contains link to
        filtered user changelist."""
        from django.contrib.auth.models import Group

        group = Group.objects.create(name="Linked Group")

        url = reverse("admin:auth_group_changelist")
        response = admin_client.get(url)
        content = response.content.decode()

        # Expected link pattern: user changelist filtered by group ID
        expected_filter = f"groups__id__exact={group.pk}"
        assert expected_filter in content, (
            f"Group changelist must contain a link to filtered user "
            f"list with '{expected_filter}', but it was not found "
            f"in the response"
        )


# ============================================================
# BATCH 4c: S2.13.5-10/11/12 BULK USER MANAGEMENT ACTIONS
# ============================================================


@pytest.mark.django_db
class TestBulkUserActions:
    """S2.13.5-10: Bulk actions on CustomUser admin changelist.

    These tests are written BEFORE implementation exists and
    should FAIL until the 7 bulk actions are implemented.
    """

    CHANGELIST_URL = "admin:accounts_customuser_changelist"

    @pytest.fixture
    def target_users(self, db, password):
        """Create two target users for bulk operations."""
        u1 = User.objects.create_user(
            username="bulk_target1",
            email="bulk1@example.com",
            password=password,
        )
        u2 = User.objects.create_user(
            username="bulk_target2",
            email="bulk2@example.com",
            password=password,
        )
        return [u1, u2]

    @pytest.fixture
    def bystander_user(self, db, password):
        """A user NOT selected in the bulk action."""
        return User.objects.create_user(
            username="bystander",
            email="bystander@example.com",
            password=password,
        )

    @pytest.fixture
    def staff_client(self, client, db, password):
        """A staff (non-superuser) client for permission tests."""
        staff = User.objects.create_user(
            username="staffonly",
            email="staffonly@example.com",
            password=password,
            is_staff=True,
            is_superuser=False,
        )
        client.login(username=staff.username, password=password)
        return client

    # --- S2.13.5-10: assign_groups (intermediate form) ---

    def test_assign_groups_adds_groups_to_selected_users(
        self, admin_client, target_users
    ):
        """S2.13.5-10: assign_groups adds selected groups to
        all selected users."""
        from django.contrib.auth.models import Group

        g1, _ = Group.objects.get_or_create(name="Member")
        g2, _ = Group.objects.get_or_create(name="Viewer")

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "assign_groups",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "groups": [g1.pk, g2.pk],
            },
        )

        for u in target_users:
            u.refresh_from_db()
            assert u.groups.filter(
                name="Member"
            ).exists(), f"User {u.username} should be in Member group"
            assert u.groups.filter(
                name="Viewer"
            ).exists(), f"User {u.username} should be in Viewer group"

    def test_assign_groups_does_not_affect_unselected(
        self, admin_client, target_users, bystander_user
    ):
        """S2.13.5-10: assign_groups must not modify unselected
        users."""
        from django.contrib.auth.models import Group

        g, _ = Group.objects.get_or_create(name="Member")

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "assign_groups",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "groups": [g.pk],
            },
        )

        bystander_user.refresh_from_db()
        assert not bystander_user.groups.filter(name="Member").exists()

    # --- S2.13.5-10: remove_groups (intermediate form) ---

    def test_remove_groups_removes_groups_from_selected_users(
        self, admin_client, target_users
    ):
        """S2.13.5-10: remove_groups removes selected groups from
        all selected users."""
        from django.contrib.auth.models import Group

        g, _ = Group.objects.get_or_create(name="Member")
        for u in target_users:
            u.groups.add(g)

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "remove_groups",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "groups": [g.pk],
            },
        )

        for u in target_users:
            u.refresh_from_db()
            assert not u.groups.filter(name="Member").exists(), (
                f"User {u.username} should no longer be " f"in Member group"
            )

    def test_remove_groups_does_not_affect_unselected(
        self, admin_client, target_users, bystander_user
    ):
        """S2.13.5-10: remove_groups must not modify unselected
        users."""
        from django.contrib.auth.models import Group

        g, _ = Group.objects.get_or_create(name="Member")
        bystander_user.groups.add(g)
        for u in target_users:
            u.groups.add(g)

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "remove_groups",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "groups": [g.pk],
            },
        )

        bystander_user.refresh_from_db()
        assert bystander_user.groups.filter(name="Member").exists()

    # --- S2.13.5-10: set_is_staff (direct) ---

    def test_set_is_staff_sets_flag_on_selected_users(
        self, admin_client, target_users
    ):
        """S2.13.5-10: set_is_staff sets is_staff=True on selected
        users."""
        for u in target_users:
            assert u.is_staff is False

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "set_is_staff",
                "_selected_action": [u.pk for u in target_users],
            },
        )

        for u in target_users:
            u.refresh_from_db()
            assert (
                u.is_staff is True
            ), f"User {u.username} should have is_staff=True"

    def test_set_is_staff_does_not_affect_unselected(
        self, admin_client, target_users, bystander_user
    ):
        """S2.13.5-10: set_is_staff must not modify unselected
        users."""
        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "set_is_staff",
                "_selected_action": [u.pk for u in target_users],
            },
        )

        bystander_user.refresh_from_db()
        assert bystander_user.is_staff is False

    # --- S2.13.5-10: clear_is_staff (direct) ---

    def test_clear_is_staff_clears_flag_on_selected_users(
        self, admin_client, target_users
    ):
        """S2.13.5-10: clear_is_staff sets is_staff=False on
        selected users."""
        for u in target_users:
            u.is_staff = True
            u.save(update_fields=["is_staff"])

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "clear_is_staff",
                "_selected_action": [u.pk for u in target_users],
            },
        )

        for u in target_users:
            u.refresh_from_db()
            assert (
                u.is_staff is False
            ), f"User {u.username} should have is_staff=False"

    # --- S2.13.5-10: assign_department (intermediate form) ---

    def test_assign_department_adds_users_to_department_managers(
        self, admin_client, target_users, department
    ):
        """S2.13.5-10/S2.13.5-02: assign_department adds selected
        users to Department.managers M2M."""
        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "assign_department",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "department": department.pk,
            },
        )

        for u in target_users:
            assert department in u.managed_departments.all(), (
                f"User {u.username} should be a manager of "
                f"{department.name}"
            )

    def test_assign_department_does_not_affect_unselected(
        self, admin_client, target_users, bystander_user, department
    ):
        """S2.13.5-10: assign_department must not modify
        unselected users."""
        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "assign_department",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "department": department.pk,
            },
        )

        assert department not in bystander_user.managed_departments.all()

    # --- S2.13.5-10: remove_from_department (intermediate form) ---

    def test_remove_from_department_removes_users_from_managers(
        self, admin_client, target_users, department
    ):
        """S2.13.5-10/S2.13.5-02: remove_from_department removes
        selected users from Department.managers M2M."""
        for u in target_users:
            department.managers.add(u)
        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "remove_from_department",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "department": department.pk,
            },
        )

        for u in target_users:
            assert department not in u.managed_departments.all(), (
                f"User {u.username} should no longer be a manager of "
                f"{department.name}"
            )

    def test_remove_from_department_does_not_affect_unselected(
        self, admin_client, target_users, bystander_user, department
    ):
        """S2.13.5-10: remove_from_department must not modify
        unselected users."""
        for u in target_users:
            department.managers.add(u)
        department.managers.add(bystander_user)
        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "remove_from_department",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "department": department.pk,
            },
        )

        assert department in bystander_user.managed_departments.all()

    def test_remove_from_department_creates_log_entries(
        self, admin_client, admin_user, target_users, department
    ):
        """S2.13.5-12 MUST: remove_from_department creates LogEntry
        audit records."""
        for u in target_users:
            department.managers.add(u)
        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "remove_from_department",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "department": department.pk,
            },
        )

        User = get_user_model()
        ct = ContentType.objects.get_for_model(User)
        for u in target_users:
            assert LogEntry.objects.filter(
                content_type=ct,
                object_id=str(u.pk),
                action_flag=CHANGE,
            ).exists(), f"LogEntry should exist for user {u.username}"

    # --- S2.13.5-11 MUST: set_is_superuser (superuser-only) ---

    def test_set_is_superuser_works_for_superuser(
        self, admin_client, target_users
    ):
        """S2.13.5-11 MUST: Superuser can set is_superuser=True
        on selected users."""
        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "set_is_superuser",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
            },
        )

        for u in target_users:
            u.refresh_from_db()
            assert u.is_superuser is True, (
                f"User {u.username} should have " f"is_superuser=True"
            )

    def test_set_is_superuser_denied_for_non_superuser(
        self, staff_client, target_users
    ):
        """S2.13.5-11 MUST: Non-superuser staff cannot execute
        set_is_superuser."""
        staff_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "set_is_superuser",
                "_selected_action": [u.pk for u in target_users],
            },
        )

        for u in target_users:
            u.refresh_from_db()
            assert u.is_superuser is False, (
                f"User {u.username} must NOT have "
                f"is_superuser set by non-superuser"
            )

    # --- S2.13.5-11 MUST: clear_is_superuser (superuser-only) ---

    def test_clear_is_superuser_works_for_superuser(
        self, admin_client, target_users
    ):
        """S2.13.5-11 MUST: Superuser can set is_superuser=False
        on selected users."""
        for u in target_users:
            u.is_superuser = True
            u.save(update_fields=["is_superuser"])

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "clear_is_superuser",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
            },
        )

        for u in target_users:
            u.refresh_from_db()
            assert u.is_superuser is False, (
                f"User {u.username} should have " f"is_superuser=False"
            )

    def test_clear_is_superuser_denied_for_non_superuser(
        self, staff_client, target_users
    ):
        """S2.13.5-11 MUST: Non-superuser staff cannot execute
        clear_is_superuser."""
        for u in target_users:
            u.is_superuser = True
            u.save(update_fields=["is_superuser"])

        staff_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "clear_is_superuser",
                "_selected_action": [u.pk for u in target_users],
            },
        )

        for u in target_users:
            u.refresh_from_db()
            assert u.is_superuser is True, (
                f"User {u.username} must still have "
                f"is_superuser=True (non-superuser cannot clear)"
            )

    # --- S2.13.5-12 MUST: LogEntry audit records ---

    def test_set_is_staff_creates_log_entries(
        self, admin_client, target_users
    ):
        """S2.13.5-12 MUST: set_is_staff creates LogEntry for
        each modified user."""
        from django.contrib.admin.models import CHANGE, LogEntry

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "set_is_staff",
                "_selected_action": [u.pk for u in target_users],
            },
        )

        for u in target_users:
            log = LogEntry.objects.filter(
                content_type__app_label="accounts",
                content_type__model="customuser",
                object_id=str(u.pk),
                action_flag=CHANGE,
            )
            assert log.exists(), (
                f"LogEntry must exist for user {u.username} "
                f"after set_is_staff"
            )
            assert (
                "staff" in log.first().change_message.lower()
            ), "LogEntry change_message must mention 'staff'"

    def test_assign_groups_creates_log_entries(
        self, admin_client, target_users
    ):
        """S2.13.5-12 MUST: assign_groups creates LogEntry for
        each modified user."""
        from django.contrib.admin.models import CHANGE, LogEntry
        from django.contrib.auth.models import Group

        g, _ = Group.objects.get_or_create(name="Member")

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "assign_groups",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "groups": [g.pk],
            },
        )

        for u in target_users:
            log = LogEntry.objects.filter(
                content_type__app_label="accounts",
                content_type__model="customuser",
                object_id=str(u.pk),
                action_flag=CHANGE,
            )
            assert log.exists(), (
                f"LogEntry must exist for user {u.username} "
                f"after assign_groups"
            )

    def test_set_is_superuser_creates_log_entries(
        self, admin_client, target_users
    ):
        """S2.13.5-12 MUST: set_is_superuser creates LogEntry
        for each modified user."""
        from django.contrib.admin.models import CHANGE, LogEntry

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "set_is_superuser",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
            },
        )

        for u in target_users:
            log = LogEntry.objects.filter(
                content_type__app_label="accounts",
                content_type__model="customuser",
                object_id=str(u.pk),
                action_flag=CHANGE,
            )
            assert log.exists(), (
                f"LogEntry must exist for user {u.username} "
                f"after set_is_superuser"
            )
            assert (
                "superuser" in log.first().change_message.lower()
            ), "LogEntry change_message must mention 'superuser'"

    def test_remove_groups_creates_log_entries(
        self, admin_client, target_users
    ):
        """S2.13.5-12 MUST: remove_groups creates LogEntry for
        each modified user."""
        from django.contrib.admin.models import CHANGE, LogEntry
        from django.contrib.auth.models import Group

        g, _ = Group.objects.get_or_create(name="Member")
        for u in target_users:
            u.groups.add(g)

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "remove_groups",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "groups": [g.pk],
            },
        )

        for u in target_users:
            log = LogEntry.objects.filter(
                content_type__app_label="accounts",
                content_type__model="customuser",
                object_id=str(u.pk),
                action_flag=CHANGE,
            )
            assert log.exists(), (
                f"LogEntry must exist for user {u.username} "
                f"after remove_groups"
            )

    def test_clear_is_staff_creates_log_entries(
        self, admin_client, target_users
    ):
        """S2.13.5-12 MUST: clear_is_staff creates LogEntry for
        each modified user."""
        from django.contrib.admin.models import CHANGE, LogEntry

        for u in target_users:
            u.is_staff = True
            u.save(update_fields=["is_staff"])

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "clear_is_staff",
                "_selected_action": [u.pk for u in target_users],
            },
        )

        for u in target_users:
            log = LogEntry.objects.filter(
                content_type__app_label="accounts",
                content_type__model="customuser",
                object_id=str(u.pk),
                action_flag=CHANGE,
            )
            assert log.exists(), (
                f"LogEntry must exist for user {u.username} "
                f"after clear_is_staff"
            )

    def test_clear_is_superuser_creates_log_entries(
        self, admin_client, target_users
    ):
        """S2.13.5-12 MUST: clear_is_superuser creates LogEntry
        for each modified user."""
        from django.contrib.admin.models import CHANGE, LogEntry

        for u in target_users:
            u.is_superuser = True
            u.save(update_fields=["is_superuser"])

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "clear_is_superuser",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
            },
        )

        for u in target_users:
            log = LogEntry.objects.filter(
                content_type__app_label="accounts",
                content_type__model="customuser",
                object_id=str(u.pk),
                action_flag=CHANGE,
            )
            assert log.exists(), (
                f"LogEntry must exist for user {u.username} "
                f"after clear_is_superuser"
            )

    def test_assign_department_creates_log_entries(
        self, admin_client, target_users, department
    ):
        """S2.13.5-12 MUST: assign_department creates LogEntry
        for each modified user."""
        from django.contrib.admin.models import CHANGE, LogEntry

        admin_client.post(
            reverse(self.CHANGELIST_URL),
            {
                "action": "assign_department",
                "_selected_action": [u.pk for u in target_users],
                "apply": "1",
                "department": department.pk,
            },
        )

        for u in target_users:
            log = LogEntry.objects.filter(
                content_type__app_label="accounts",
                content_type__model="customuser",
                object_id=str(u.pk),
                action_flag=CHANGE,
            )
            assert log.exists(), (
                f"LogEntry must exist for user {u.username} "
                f"after assign_department"
            )


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

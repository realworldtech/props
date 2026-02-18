"""Admin configuration for accounts app."""

import logging

from unfold.admin import ModelAdmin
from unfold.decorators import action, display

from django.contrib import admin, messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html

from assets.models import Department

from .forms import CustomUserChangeForm, CustomUserCreationForm
from .models import CustomUser

logger = logging.getLogger(__name__)


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin, ModelAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser
    list_display = [
        "display_user",
        "email",
        "display_groups",
        "display_department",
        "display_staff",
        "display_active",
    ]
    list_filter = [
        "is_active",
        "is_staff",
        "is_superuser",
        "groups",
        "requested_department",
    ]
    search_fields = [
        "username",
        "email",
        "display_name",
        "first_name",
        "last_name",
    ]
    filter_horizontal = ["groups", "user_permissions"]
    autocomplete_fields = ["requested_department"]
    fieldsets = (
        (
            "Profile",
            {
                "classes": ["tab"],
                "fields": (
                    "username",
                    "password",
                    "display_name",
                    "first_name",
                    "last_name",
                    "email",
                    "phone_number",
                    "requested_department",
                    "organisation",
                ),
            },
        ),
        (
            "Permissions",
            {
                "classes": ["tab"],
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (
            "Activity",
            {
                "classes": ["tab"],
                "fields": (
                    "last_login",
                    "date_joined",
                    "approved_by",
                    "approved_at",
                    "rejection_reason",
                    "display_managed_departments",
                ),
            },
        ),
    )
    readonly_fields = [
        "display_managed_departments",
        "last_login",
        "date_joined",
        "approved_by",
        "approved_at",
        "rejection_reason",
    ]
    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "Additional Info",
            {"fields": ("email", "display_name", "phone_number")},
        ),
    )

    def display_managed_departments(self, obj):
        if not obj.pk:
            return "-"
        depts = obj.managed_departments.all()
        if depts:
            return ", ".join(d.name for d in depts)
        return "None"

    display_managed_departments.short_description = "Managed Departments"

    @display(description="User", header=True, ordering="username")
    def display_user(self, obj):
        name = obj.display_name or obj.get_full_name() or obj.username
        return name, obj.username

    @display(description="Groups")
    def display_groups(self, obj):
        groups = obj.groups.all()
        if groups:
            return ", ".join(g.name for g in groups)
        return "-"

    @display(description="Department")
    def display_department(self, obj):
        if obj.requested_department:
            return obj.requested_department.name
        return "-"

    @display(description="Staff", boolean=True)
    def display_staff(self, obj):
        return obj.is_staff

    @display(description="Active", boolean=True)
    def display_active(self, obj):
        return obj.is_active

    actions = [
        "assign_groups",
        "remove_groups",
        "set_is_staff",
        "clear_is_staff",
        "set_is_superuser",
        "clear_is_superuser",
        "assign_department",
    ]

    def _log_change(self, request, user, message):
        """Create a LogEntry for a bulk action change."""
        ct = ContentType.objects.get_for_model(user)
        LogEntry.objects.create(
            user_id=request.user.pk,
            content_type_id=ct.pk,
            object_id=str(user.pk),
            object_repr=str(user),
            action_flag=CHANGE,
            change_message=message,
        )

    @action(description="Assign groups")
    def assign_groups(self, request, queryset):
        if "apply" in request.POST:
            group_ids = request.POST.getlist("groups")
            groups = Group.objects.filter(pk__in=group_ids)
            for user in queryset:
                user.groups.add(*groups)
                self._log_change(
                    request,
                    user,
                    "Added groups via bulk action: "
                    + ", ".join(g.name for g in groups),
                )
            messages.success(
                request,
                f"Groups assigned to {queryset.count()} user(s).",
            )
            return None
        return TemplateResponse(
            request,
            "admin/accounts/assign_groups.html",
            {
                "users": queryset,
                "groups": Group.objects.all(),
                "action": "assign_groups",
                "opts": self.model._meta,
                "title": "Assign groups to users",
            },
        )

    @action(description="Remove groups")
    def remove_groups(self, request, queryset):
        if "apply" in request.POST:
            group_ids = request.POST.getlist("groups")
            groups = Group.objects.filter(pk__in=group_ids)
            for user in queryset:
                user.groups.remove(*groups)
                self._log_change(
                    request,
                    user,
                    "Removed groups via bulk action: "
                    + ", ".join(g.name for g in groups),
                )
            messages.success(
                request,
                f"Groups removed from {queryset.count()} user(s).",
            )
            return None
        return TemplateResponse(
            request,
            "admin/accounts/remove_groups.html",
            {
                "users": queryset,
                "groups": Group.objects.all(),
                "action": "remove_groups",
                "opts": self.model._meta,
                "title": "Remove groups from users",
            },
        )

    @action(description="Set is_staff")
    def set_is_staff(self, request, queryset):
        count = 0
        for user in queryset:
            user.is_staff = True
            user.save(update_fields=["is_staff"])
            self._log_change(
                request, user, "Set is_staff to True via bulk action"
            )
            count += 1
        messages.success(request, f"{count} user(s) updated.")

    @action(description="Clear is_staff")
    def clear_is_staff(self, request, queryset):
        count = 0
        for user in queryset:
            user.is_staff = False
            user.save(update_fields=["is_staff"])
            self._log_change(
                request, user, "Set is_staff to False via bulk action"
            )
            count += 1
        messages.success(request, f"{count} user(s) updated.")

    @action(description="Set is_superuser")
    def set_is_superuser(self, request, queryset):
        if not request.user.is_superuser:
            messages.error(
                request,
                "Only superusers can perform this action.",
            )
            return None
        if "apply" in request.POST:
            count = 0
            for user in queryset:
                user.is_superuser = True
                user.save(update_fields=["is_superuser"])
                self._log_change(
                    request,
                    user,
                    "Set is_superuser to True via bulk action",
                )
                count += 1
            messages.success(request, f"{count} user(s) updated.")
            return None
        return TemplateResponse(
            request,
            "admin/accounts/confirm_superuser.html",
            {
                "users": queryset,
                "action": "set_is_superuser",
                "action_label": "grant superuser status to",
                "opts": self.model._meta,
                "title": "Confirm set superuser",
            },
        )

    @action(description="Clear is_superuser")
    def clear_is_superuser(self, request, queryset):
        if not request.user.is_superuser:
            messages.error(
                request,
                "Only superusers can perform this action.",
            )
            return None
        if "apply" in request.POST:
            count = 0
            for user in queryset:
                user.is_superuser = False
                user.save(update_fields=["is_superuser"])
                self._log_change(
                    request,
                    user,
                    "Set is_superuser to False via bulk action",
                )
                count += 1
            messages.success(request, f"{count} user(s) updated.")
            return None
        return TemplateResponse(
            request,
            "admin/accounts/confirm_superuser.html",
            {
                "users": queryset,
                "action": "clear_is_superuser",
                "action_label": "remove superuser status from",
                "opts": self.model._meta,
                "title": "Confirm clear superuser",
            },
        )

    @action(description="Assign department")
    def assign_department(self, request, queryset):
        if "apply" in request.POST:
            dept_id = request.POST.get("department")
            dept = Department.objects.get(pk=dept_id)
            for user in queryset:
                user.requested_department = dept
                user.save(update_fields=["requested_department"])
                self._log_change(
                    request,
                    user,
                    f"Set department to {dept.name} via bulk action",
                )
            messages.success(
                request,
                f"Department assigned to {queryset.count()} user(s).",
            )
            return None
        return TemplateResponse(
            request,
            "admin/accounts/assign_department.html",
            {
                "users": queryset,
                "departments": Department.objects.all(),
                "action": "assign_department",
                "opts": self.model._meta,
                "title": "Assign department to users",
            },
        )

    def save_model(self, request, obj, form, change):
        """Override to set email_verified=True for admin-created users."""
        if not change:
            # New user being created via admin
            obj.email_verified = True
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        """Warn about SET_NULL effects before deleting a user."""
        from assets.models import Asset, NFCTag, Transaction

        affected = []
        display_name = obj.display_name or obj.get_full_name() or obj.username

        txn_count = Transaction.objects.filter(user=obj).count()
        if txn_count:
            affected.append(f"{txn_count} transaction(s) will lose their user")

        asset_count = Asset.objects.filter(created_by=obj).count()
        if asset_count:
            affected.append(f"{asset_count} asset(s) will lose their creator")

        nfc_assigned = NFCTag.objects.filter(assigned_by=obj).count()
        nfc_removed = NFCTag.objects.filter(removed_by=obj).count()
        nfc_total = nfc_assigned + nfc_removed
        if nfc_total:
            affected.append(
                f"{nfc_total} NFC tag record(s) will lose " f"user references"
            )

        checkout_count = Asset.objects.filter(checked_out_to=obj).count()
        if checkout_count:
            affected.append(
                f"{checkout_count} asset(s) currently checked out "
                f"to this user will be unlinked"
            )

        if affected:
            summary = "; ".join(affected)
            messages.warning(
                request,
                f"Deleting user '{display_name}': {summary}. "
                f"These fields will be set to NULL.",
            )
            logger.warning(
                "User deletion: %s (pk=%s) — %s",
                display_name,
                obj.pk,
                summary,
            )

        super().delete_model(request, obj)


# Unregister the default Group admin and register with UnfoldAdmin
admin.site.unregister(Group)


@admin.register(Group)
class CustomGroupAdmin(ModelAdmin):
    list_display = ["name", "display_user_count", "display_users_link"]
    search_fields = ["name"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(user_count=Count("user"))

    @display(description="Users", ordering="user_count")
    def display_user_count(self, obj):
        return obj.user_count

    @display(description="View Users")
    def display_users_link(self, obj):
        url = reverse("admin:accounts_customuser_changelist")
        return format_html(
            '<a href="{}?groups__id__exact={}">View users</a>',
            url,
            obj.pk,
        )

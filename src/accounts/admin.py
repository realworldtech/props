"""Admin configuration for accounts app."""

import logging

from unfold.admin import ModelAdmin
from unfold.decorators import display

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin

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

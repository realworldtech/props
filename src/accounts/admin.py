"""Admin configuration for accounts app."""

from unfold.admin import ModelAdmin
from unfold.decorators import display

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .forms import CustomUserChangeForm, CustomUserCreationForm
from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin, ModelAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser
    list_display = [
        "display_user",
        "email",
        "display_groups",
        "display_departments_list",
        "display_staff",
        "display_active",
    ]
    list_filter = ["is_staff", "is_active", "groups"]
    search_fields = [
        "username",
        "email",
        "display_name",
        "first_name",
        "last_name",
    ]
    filter_horizontal = ["groups", "user_permissions"]
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (
            "Personal Info",
            {
                "fields": (
                    "display_name",
                    "first_name",
                    "last_name",
                    "email",
                    "phone_number",
                )
            },
        ),
        (
            "Permissions",
            {
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
            "Department Management",
            {
                "fields": ("display_managed_departments",),
                "description": (
                    "Departments this user manages."
                    " Edit from the Department admin."
                ),
            },
        ),
        (
            "Important dates",
            {"fields": ("last_login", "date_joined")},
        ),
    )
    readonly_fields = [
        "display_managed_departments",
        "last_login",
        "date_joined",
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

    @display(description="Departments")
    def display_departments_list(self, obj):
        depts = obj.managed_departments.all()
        if depts:
            return ", ".join(d.name for d in depts)
        return "-"

    @display(description="Staff", boolean=True)
    def display_staff(self, obj):
        return obj.is_staff

    @display(description="Active", boolean=True)
    def display_active(self, obj):
        return obj.is_active

"""Admin configuration for assets app using django-unfold."""

from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    MultipleRelatedDropdownFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.html import format_html

from .models import (
    Asset,
    AssetImage,
    Category,
    Department,
    Location,
    NFCTag,
    SiteBranding,
    StocktakeSession,
    Tag,
    Transaction,
)


class AssetImageInline(TabularInline):
    model = AssetImage
    extra = 1
    fields = [
        "image",
        "caption",
        "is_primary",
        "ai_processing_status",
        "ai_description",
        "ai_category_suggestion",
        "ai_condition_suggestion",
        "ai_tag_suggestions",
        "ai_ocr_text",
        "ai_prompt_tokens",
        "ai_completion_tokens",
    ]
    readonly_fields = [
        "ai_processing_status",
        "ai_description",
        "ai_category_suggestion",
        "ai_condition_suggestion",
        "ai_tag_suggestions",
        "ai_ocr_text",
        "ai_prompt_tokens",
        "ai_completion_tokens",
    ]


class NFCTagInline(TabularInline):
    model = NFCTag
    extra = 0
    fields = [
        "tag_id",
        "assigned_at",
        "assigned_by",
        "removed_at",
        "removed_by",
        "notes",
    ]
    readonly_fields = ["assigned_at"]


@admin.register(Department)
class DepartmentAdmin(ModelAdmin):
    list_display = [
        "name",
        "display_active",
        "display_category_count",
        "display_asset_count",
    ]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]
    filter_horizontal = ["managers"]

    @display(description="Active", boolean=True)
    def display_active(self, obj):
        return obj.is_active

    @display(description="Categories", ordering="categories__count")
    def display_category_count(self, obj):
        return obj.categories.count()

    @display(description="Assets")
    def display_asset_count(self, obj):
        return Asset.objects.filter(category__department=obj).count()


@admin.register(Tag)
class TagAdmin(ModelAdmin):
    list_display = ["name", "color", "display_asset_count"]
    search_fields = ["name"]

    @display(description="Assets")
    def display_asset_count(self, obj):
        return obj.assets.count()


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ["name", "department", "icon", "display_asset_count"]
    list_filter = [("department", RelatedDropdownFilter)]
    search_fields = ["name", "description"]
    autocomplete_fields = ["department"]

    @display(description="Assets")
    def display_asset_count(self, obj):
        return obj.assets.count()


@admin.register(Location)
class LocationAdmin(ModelAdmin):
    list_display = ["name", "parent", "display_active", "display_asset_count"]
    list_filter = [
        "is_active",
        ("parent", RelatedDropdownFilter),
    ]
    search_fields = ["name", "address", "description"]

    @display(description="Active", boolean=True)
    def display_active(self, obj):
        return obj.is_active

    @display(description="Assets")
    def display_asset_count(self, obj):
        return obj.assets.count()


@admin.register(Asset)
class AssetAdmin(ModelAdmin):
    list_display = [
        "display_header",
        "display_status",
        "category",
        "current_location",
        "display_condition",
        "display_checked_out",
        "updated_at",
    ]
    list_filter = [
        ("status", ChoicesDropdownFilter),
        ("condition", ChoicesDropdownFilter),
        ("category__department", RelatedDropdownFilter),
        ("category", RelatedDropdownFilter),
        ("current_location", RelatedDropdownFilter),
        ("tags", MultipleRelatedDropdownFilter),
    ]
    list_filter_submit = True
    search_fields = ["name", "barcode", "description"]
    readonly_fields = [
        "barcode",
        "barcode_image_preview",
        "created_at",
        "updated_at",
    ]
    autocomplete_fields = ["category", "current_location", "checked_out_to"]
    filter_horizontal = ["tags"]
    inlines = [AssetImageInline, NFCTagInline]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "description",
                    "status",
                    "category",
                    "current_location",
                    "home_location",
                )
            },
        ),
        (
            "Details",
            {
                "fields": (
                    "quantity",
                    "condition",
                    "tags",
                    "notes",
                    "purchase_price",
                    "estimated_value",
                ),
                "classes": ["tab"],
            },
        ),
        (
            "Barcode",
            {
                "fields": (
                    "barcode",
                    "barcode_image_preview",
                ),
                "classes": ["tab"],
            },
        ),
        (
            "Tracking",
            {
                "fields": (
                    "checked_out_to",
                    "created_by",
                    "created_at",
                    "updated_at",
                ),
                "classes": ["tab"],
            },
        ),
    )

    actions_detail = ["print_label_action"]

    # --- Display methods ---

    @display(description="Asset", header=True, ordering="name")
    def display_header(self, obj):
        return obj.name, obj.barcode

    @display(
        description="Status",
        label={
            "draft": "info",
            "active": "success",
            "retired": "warning",
            "missing": "danger",
            "disposed": "default",
        },
    )
    def display_status(self, obj):
        return obj.status

    @display(
        description="Condition",
        label={
            "excellent": "success",
            "good": "success",
            "fair": "info",
            "poor": "warning",
            "damaged": "danger",
        },
    )
    def display_condition(self, obj):
        return obj.condition

    @display(description="Checked Out To", empty_value="-")
    def display_checked_out(self, obj):
        if obj.checked_out_to:
            return obj.checked_out_to.get_display_name()
        return None

    def barcode_image_preview(self, obj):
        if obj.barcode_image:
            return format_html(
                '<img src="{}" height="60" />',
                obj.barcode_image.url,
            )
        return "-"

    barcode_image_preview.short_description = "Barcode Image"

    # --- Actions ---

    @action(
        description="Export selected to Excel",
        icon="download",
        variant=ActionVariant.PRIMARY,
    )
    def export_selected_xlsx(self, request, queryset):
        from .services.export import export_assets_xlsx

        buffer = export_assets_xlsx(queryset)
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet",
        )
        from datetime import date

        filename = f"props-assets-export-{date.today().isoformat()}.xlsx"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    export_selected_xlsx.short_description = "Export selected to Excel"

    @action(description="Mark as active")
    def mark_active(self, request, queryset):
        updated = queryset.exclude(status="disposed").update(status="active")
        messages.success(request, f"{updated} asset(s) marked as active.")

    mark_active.short_description = "Mark as active"

    @action(description="Mark as retired")
    def mark_retired(self, request, queryset):
        updated = queryset.exclude(status="disposed").update(status="retired")
        messages.success(request, f"{updated} asset(s) marked as retired.")

    mark_retired.short_description = "Mark as retired"

    actions = ["export_selected_xlsx", "mark_active", "mark_retired"]

    @action(
        description="Print Label",
        icon="print",
        variant=ActionVariant.DEFAULT,
    )
    def print_label_action(self, request, object_id):
        return redirect(
            reverse_lazy("assets:asset_label", kwargs={"pk": object_id})
        )


@admin.register(AssetImage)
class AssetImageAdmin(ModelAdmin):
    list_display = [
        "asset",
        "caption",
        "display_primary",
        "display_ai_status",
        "display_tokens",
        "uploaded_at",
        "ai_processed_at",
    ]
    list_filter = [
        "is_primary",
        ("ai_processing_status", ChoicesDropdownFilter),
    ]
    search_fields = ["asset__name", "caption", "ai_description"]
    readonly_fields = [
        "ai_description",
        "ai_category_suggestion",
        "ai_condition_suggestion",
        "ai_tag_suggestions",
        "ai_ocr_text",
        "ai_prompt_tokens",
        "ai_completion_tokens",
        "ai_processed_at",
        "ai_processing_status",
        "ai_error_message",
    ]

    fieldsets = (
        (None, {"fields": ("asset", "image", "caption", "is_primary")}),
        (
            "AI Analysis",
            {
                "fields": (
                    "ai_processing_status",
                    "ai_error_message",
                    "ai_description",
                    "ai_category_suggestion",
                    "ai_condition_suggestion",
                    "ai_tag_suggestions",
                    "ai_ocr_text",
                ),
                "classes": ["tab"],
            },
        ),
        (
            "AI Usage",
            {
                "fields": (
                    "ai_prompt_tokens",
                    "ai_completion_tokens",
                    "ai_processed_at",
                ),
                "classes": ["tab"],
            },
        ),
    )

    @display(description="Primary", boolean=True)
    def display_primary(self, obj):
        return obj.is_primary

    @display(
        description="AI Status",
        label={
            "pending": "info",
            "processing": "warning",
            "completed": "success",
            "failed": "danger",
            "skipped": "default",
        },
    )
    def display_ai_status(self, obj):
        return obj.ai_processing_status

    @display(description="Tokens")
    def display_tokens(self, obj):
        if obj.ai_prompt_tokens or obj.ai_completion_tokens:
            return f"{obj.ai_prompt_tokens + obj.ai_completion_tokens}"
        return "-"


@admin.register(NFCTag)
class NFCTagAdmin(ModelAdmin):
    list_display = [
        "tag_id",
        "asset",
        "display_active",
        "assigned_at",
        "removed_at",
    ]
    list_filter = ["removed_at"]
    search_fields = ["tag_id", "asset__name", "asset__barcode"]

    @display(description="Active", boolean=True)
    def display_active(self, obj):
        return obj.is_active


@admin.register(Transaction)
class TransactionAdmin(ModelAdmin):
    list_display = [
        "asset",
        "display_action",
        "user",
        "borrower",
        "from_location",
        "to_location",
        "timestamp",
    ]
    list_filter = [
        ("action", ChoicesDropdownFilter),
        ("from_location", RelatedDropdownFilter),
        ("to_location", RelatedDropdownFilter),
    ]
    search_fields = ["asset__name", "asset__barcode", "notes"]
    date_hierarchy = "timestamp"
    readonly_fields = [
        "asset",
        "user",
        "action",
        "from_location",
        "to_location",
        "borrower",
        "notes",
        "timestamp",
    ]

    @display(
        description="Action",
        label={
            "checkout": "warning",
            "checkin": "success",
            "transfer": "info",
            "audit": "default",
        },
    )
    def display_action(self, obj):
        return obj.action


@admin.register(StocktakeSession)
class StocktakeSessionAdmin(ModelAdmin):
    list_display = [
        "location",
        "started_by",
        "display_status",
        "started_at",
        "ended_at",
    ]
    list_filter = [
        ("status", ChoicesDropdownFilter),
        ("location", RelatedDropdownFilter),
    ]
    search_fields = ["location__name", "notes"]
    readonly_fields = ["started_at"]

    @display(
        description="Status",
        label={
            "in_progress": "warning",
            "completed": "success",
            "abandoned": "danger",
        },
    )
    def display_status(self, obj):
        return obj.status


@admin.register(SiteBranding)
class SiteBrandingAdmin(ModelAdmin):
    list_display = ["__str__"]
    fields = ["logo_light", "logo_dark", "favicon"]

    def has_add_permission(self, request):
        if SiteBranding.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

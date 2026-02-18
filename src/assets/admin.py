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
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse_lazy
from django.utils.html import format_html

from .models import (
    Asset,
    AssetImage,
    AssetKit,
    AssetSerial,
    Category,
    Department,
    HoldList,
    HoldListItem,
    HoldListStatus,
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


class AssetSerialInline(TabularInline):
    model = AssetSerial
    extra = 0
    fields = [
        "serial_number",
        "barcode",
        "status",
        "condition",
        "checked_out_to",
        "current_location",
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_archived=False)


class AssetKitInline(TabularInline):
    model = AssetKit
    fk_name = "kit"
    extra = 0
    fields = [
        "component",
        "quantity",
        "is_required",
        "is_kit_only",
        "serial",
        "notes",
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
        "description",
        "get_managers",
        "display_active",
        "display_category_count",
        "display_asset_count",
    ]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]
    filter_horizontal = ["managers"]

    @display(description="Managers")
    def get_managers(self, obj):
        managers = obj.managers.all()
        return (
            ", ".join(
                m.display_name or m.get_full_name() or m.username
                for m in managers
            )
            or "-"
        )

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

    def change_view(self, request, object_id, form_url="", extra_context=None):
        category = self.get_object(request, object_id)
        if category:
            asset_count = category.assets.count()
            if asset_count > 0:
                messages.warning(
                    request,
                    f"Warning: This category contains {asset_count} "
                    f"asset(s). Changing the department means these "
                    f"assets will be moved and current department "
                    f"managers will lose access.",
                )
        return super().change_view(request, object_id, form_url, extra_context)


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


class OnHoldListFilter(admin.SimpleListFilter):
    """V598: Custom filter to show assets on active hold lists."""

    title = "on hold list"
    parameter_name = "on_hold_list"

    def lookups(self, request, model_admin):
        return [
            ("yes", "On active hold list"),
            ("no", "Not on hold list"),
        ]

    def queryset(self, request, queryset):
        active_statuses = HoldListStatus.objects.filter(
            is_terminal=False
        ).values_list("pk", flat=True)
        held_ids = (
            HoldListItem.objects.filter(
                hold_list__status_id__in=active_statuses
            )
            .values_list("asset_id", flat=True)
            .distinct()
        )
        if self.value() == "yes":
            return queryset.filter(pk__in=held_ids)
        if self.value() == "no":
            return queryset.exclude(pk__in=held_ids)
        return queryset


@admin.register(Asset)
class AssetAdmin(ModelAdmin):
    list_display = [
        "display_header",
        "display_status",
        "category",
        "current_location",
        "display_condition",
        "display_checked_out",
        "ai_analysis_summary",
        "updated_at",
    ]
    list_filter = [
        ("status", ChoicesDropdownFilter),
        ("condition", ChoicesDropdownFilter),
        ("category__department", RelatedDropdownFilter),
        ("category", RelatedDropdownFilter),
        ("current_location", RelatedDropdownFilter),
        ("tags", MultipleRelatedDropdownFilter),
        "is_serialised",
        "is_kit",
        OnHoldListFilter,
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
    inlines = [
        AssetSerialInline,
        AssetKitInline,
        AssetImageInline,
        NFCTagInline,
    ]

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
                    "is_serialised",
                    "is_kit",
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

    @display(description="AI Analysis")
    def ai_analysis_summary(self, obj):
        images = obj.images.all()
        total = images.count()
        if not total:
            return "-"
        completed = images.filter(ai_processing_status="completed").count()
        return f"{completed}/{total} analysed"

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

    @action(description="Mark as lost (requires notes)")
    def mark_lost(self, request, queryset):
        if "apply" in request.POST:
            notes = request.POST.get("notes", "").strip()
            if notes:
                qs = queryset.exclude(status__in=["disposed", "lost"])
                count = 0
                for asset in qs:
                    asset.status = "lost"
                    if asset.notes:
                        asset.notes += f"\n---\n{notes}"
                    else:
                        asset.notes = notes
                    asset.save(update_fields=["status", "notes"])
                    count += 1
                messages.success(
                    request,
                    f"{count} asset(s) marked as lost.",
                )
                return None
        return TemplateResponse(
            request,
            "admin/assets/mark_with_notes.html",
            {
                "assets": queryset,
                "action": "mark_lost",
                "target_status": "lost",
                "opts": self.model._meta,
                "title": "Mark assets as lost",
            },
        )

    mark_lost.short_description = "Mark as lost (requires notes)"

    @action(description="Mark as stolen (requires notes)")
    def mark_stolen(self, request, queryset):
        if "apply" in request.POST:
            notes = request.POST.get("notes", "").strip()
            if notes:
                qs = queryset.exclude(status__in=["disposed", "stolen"])
                count = 0
                for asset in qs:
                    asset.status = "stolen"
                    if asset.notes:
                        asset.notes += f"\n---\n{notes}"
                    else:
                        asset.notes = notes
                    asset.save(update_fields=["status", "notes"])
                    count += 1
                messages.success(
                    request,
                    f"{count} asset(s) marked as stolen.",
                )
                return None
        return TemplateResponse(
            request,
            "admin/assets/mark_with_notes.html",
            {
                "assets": queryset,
                "action": "mark_stolen",
                "target_status": "stolen",
                "opts": self.model._meta,
                "title": "Mark assets as stolen",
            },
        )

    mark_stolen.short_description = "Mark as stolen (requires notes)"

    @action(description="Print labels for selected")
    def print_labels(self, request, queryset):
        pks = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return redirect(
            f"{reverse_lazy('assets:barcode_pregenerate')}?ids={pks}"
        )

    print_labels.short_description = "Print labels for selected"

    @action(description="Transfer to location...")
    def bulk_transfer(self, request, queryset):
        if "apply" in request.POST:
            location_id = request.POST.get("location")
            if location_id:
                location = Location.objects.get(pk=location_id)
                count = 0
                for asset in queryset:
                    asset.current_location = location
                    asset.save(update_fields=["current_location"])
                    count += 1
                messages.success(
                    request,
                    f"{count} asset(s) transferred to {location.name}.",
                )
                return None
        locations = Location.objects.filter(is_active=True).order_by("name")
        return TemplateResponse(
            request,
            "admin/assets/bulk_transfer.html",
            {
                "assets": queryset,
                "locations": locations,
                "action": "bulk_transfer",
                "opts": self.model._meta,
                "title": "Transfer assets to location",
            },
        )

    bulk_transfer.short_description = "Transfer to location..."

    @action(description="Change category...")
    def bulk_change_category(self, request, queryset):
        if "apply" in request.POST:
            cat_id = request.POST.get("category")
            if cat_id:
                category = Category.objects.get(pk=cat_id)
                count = queryset.update(category=category)
                messages.success(
                    request,
                    f"{count} asset(s) category changed to {category.name}.",
                )
                return None
        categories = Category.objects.all().order_by("name")
        return TemplateResponse(
            request,
            "admin/assets/bulk_change_category.html",
            {
                "assets": queryset,
                "categories": categories,
                "action": "bulk_change_category",
                "opts": self.model._meta,
                "title": "Change category for assets",
            },
        )

    bulk_change_category.short_description = "Change category..."

    @action(description="Merge assets")
    def merge_assets(self, request, queryset):
        if queryset.count() != 2:
            messages.error(
                request,
                "Please select exactly 2 assets to merge.",
            )
            return None
        if "apply" in request.POST:
            primary_pk = request.POST.get("primary")
            if primary_pk:
                from .services.merge import merge_assets

                assets = list(queryset)
                primary = next(a for a in assets if str(a.pk) == primary_pk)
                duplicates = [a for a in assets if str(a.pk) != primary_pk]
                try:
                    merge_assets(primary, duplicates, request.user)
                    messages.success(
                        request,
                        f"Assets merged into '{primary.name}'.",
                    )
                except ValueError as e:
                    messages.error(request, str(e))
                return None
        return TemplateResponse(
            request,
            "admin/assets/merge_assets.html",
            {
                "assets": queryset,
                "action": "merge_assets",
                "opts": self.model._meta,
                "title": "Merge assets",
            },
        )

    merge_assets.short_description = "Merge assets"

    @action(description="Convert to serialised")
    def bulk_serialise(self, request, queryset):
        updated = queryset.filter(is_serialised=False).update(
            is_serialised=True
        )
        messages.success(
            request,
            f"{updated} asset(s) converted to serialised.",
        )

    bulk_serialise.short_description = "Convert to serialised"

    @action(description="Add to hold list...")
    def add_to_hold_list(self, request, queryset):
        if "apply" in request.POST:
            hold_list_pk = request.POST.get("hold_list")
            if hold_list_pk:
                hold_list = HoldList.objects.get(pk=hold_list_pk)
                count = 0
                for asset in queryset:
                    HoldListItem.objects.get_or_create(
                        hold_list=hold_list,
                        asset=asset,
                        defaults={
                            "added_by": request.user,
                            "quantity": 1,
                        },
                    )
                    count += 1
                messages.success(
                    request,
                    f"{count} asset(s) added to '{hold_list.name}'.",
                )
                return None
        hold_lists = HoldList.objects.filter(
            is_locked=False,
        ).order_by("name")
        return TemplateResponse(
            request,
            "admin/assets/add_to_hold_list.html",
            {
                "assets": queryset,
                "hold_lists": hold_lists,
                "action": "add_to_hold_list",
                "opts": self.model._meta,
                "title": "Add assets to hold list",
            },
        )

    add_to_hold_list.short_description = "Add to hold list..."

    @action(description="Generate kit labels")
    def generate_kit_labels(self, request, queryset):
        kit_assets = queryset.filter(is_kit=True)
        if not kit_assets.exists():
            messages.error(
                request,
                "No kit assets in the selection.",
            )
            return None
        component_pks = set()
        for kit in kit_assets:
            kit_components = AssetKit.objects.filter(kit=kit).select_related(
                "component"
            )
            for kc in kit_components:
                component_pks.add(kc.component.pk)
        if not component_pks:
            messages.warning(
                request,
                "Selected kits have no components.",
            )
            return None
        pks = ",".join(str(pk) for pk in component_pks)
        return redirect(
            f"{reverse_lazy('assets:barcode_pregenerate')}?ids={pks}"
        )

    generate_kit_labels.short_description = "Generate kit labels"

    actions = [
        "export_selected_xlsx",
        "mark_active",
        "mark_retired",
        "mark_lost",
        "mark_stolen",
        "print_labels",
        "bulk_transfer",
        "bulk_change_category",
        "merge_assets",
        "bulk_serialise",
        "add_to_hold_list",
        "generate_kit_labels",
    ]

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

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        qs = self.get_queryset(request)
        stats = qs.aggregate(
            total_images=Count("id"),
            analysed=Count("id", filter=Q(ai_processing_status="completed")),
            failed=Count("id", filter=Q(ai_processing_status="failed")),
            total_prompt_tokens=Sum("ai_prompt_tokens"),
            total_completion_tokens=Sum("ai_completion_tokens"),
        )
        extra_context["ai_stats"] = stats

        # Daily usage count and remaining quota (L29)
        import datetime

        from django.conf import settings as django_settings
        from django.utils import timezone

        today_local = timezone.localdate()
        today_start = timezone.make_aware(
            datetime.datetime.combine(today_local, datetime.time.min)
        )
        daily_usage = AssetImage.objects.filter(
            ai_processed_at__gte=today_start,
            ai_processing_status="completed",
        ).count()
        daily_limit = getattr(django_settings, "AI_ANALYSIS_DAILY_LIMIT", 100)
        extra_context["daily_usage"] = daily_usage
        extra_context["daily_limit"] = daily_limit
        extra_context["daily_remaining"] = max(0, daily_limit - daily_usage)

        return super().changelist_view(request, extra_context=extra_context)


@admin.register(AssetSerial)
class AssetSerialAdmin(ModelAdmin):
    list_display = [
        "serial_number",
        "asset",
        "barcode",
        "display_status",
        "display_condition",
        "checked_out_to",
        "current_location",
        "is_archived",
    ]
    list_filter = [
        ("status", ChoicesDropdownFilter),
        ("condition", ChoicesDropdownFilter),
        "is_archived",
    ]
    search_fields = [
        "serial_number",
        "barcode",
        "asset__name",
        "asset__barcode",
    ]

    @display(
        description="Status",
        label={
            "active": "success",
            "retired": "warning",
            "missing": "danger",
            "lost": "danger",
            "stolen": "danger",
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


@admin.register(AssetKit)
class AssetKitAdmin(ModelAdmin):
    list_display = [
        "kit",
        "component",
        "quantity",
        "is_required",
        "is_kit_only",
    ]
    search_fields = [
        "kit__name",
        "component__name",
    ]


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


@admin.register(HoldListStatus)
class HoldListStatusAdmin(ModelAdmin):
    list_display = ["name", "is_default", "is_terminal", "sort_order", "color"]
    list_filter = ["is_default", "is_terminal"]
    search_fields = ["name"]


@admin.register(SiteBranding)
class SiteBrandingAdmin(ModelAdmin):
    list_display = ["__str__"]
    fields = [
        "logo_light",
        "logo_dark",
        "favicon",
        "primary_color",
        "secondary_color",
        "accent_color",
        "color_mode",
    ]

    def has_add_permission(self, request):
        if SiteBranding.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

"""URL configuration for assets app."""

from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    # Dashboard
    path("", views.dashboard, name="dashboard"),
    path("my-items/", views.my_borrowed_items, name="my_borrowed_items"),
    # Assets
    path("assets/", views.asset_list, name="asset_list"),
    path("assets/create/", views.asset_create, name="asset_create"),
    path("assets/export/", views.export_assets, name="export_assets"),
    path("assets/bulk/", views.bulk_actions, name="bulk_actions"),
    path("assets/<int:pk>/", views.asset_detail, name="asset_detail"),
    path("assets/<int:pk>/edit/", views.asset_edit, name="asset_edit"),
    path("assets/<int:pk>/delete/", views.asset_delete, name="asset_delete"),
    # Quick Capture
    path("quick-capture/", views.quick_capture, name="quick_capture"),
    path("drafts/", views.drafts_queue, name="drafts_queue"),
    # Scanning
    path("scan/", views.scan_view, name="scan"),
    path("scan/lookup/", views.scan_lookup, name="scan_lookup"),
    # Unified lookup
    path(
        "a/<str:identifier>/",
        views.asset_by_identifier,
        name="asset_by_identifier",
    ),
    # Images
    path(
        "assets/<int:pk>/images/upload/",
        views.image_upload,
        name="image_upload",
    ),
    path(
        "assets/<int:pk>/images/<int:image_pk>/delete/",
        views.image_delete,
        name="image_delete",
    ),
    path(
        "assets/<int:pk>/images/<int:image_pk>/primary/",
        views.image_set_primary,
        name="image_set_primary",
    ),
    # NFC Tags
    path(
        "assets/<int:pk>/nfc/add/",
        views.nfc_add,
        name="nfc_add",
    ),
    path(
        "assets/<int:pk>/nfc/<int:nfc_pk>/remove/",
        views.nfc_remove,
        name="nfc_remove",
    ),
    # Check-out / Check-in / Transfer
    path(
        "assets/<int:pk>/checkout/",
        views.asset_checkout,
        name="asset_checkout",
    ),
    path(
        "assets/<int:pk>/checkin/",
        views.asset_checkin,
        name="asset_checkin",
    ),
    path(
        "assets/<int:pk>/transfer/",
        views.asset_transfer,
        name="asset_transfer",
    ),
    path(
        "assets/<int:pk>/relocate/",
        views.asset_relocate,
        name="asset_relocate",
    ),
    path(
        "assets/<int:pk>/handover/",
        views.asset_handover,
        name="asset_handover",
    ),
    # Labels
    path(
        "assets/<int:pk>/label/",
        views.asset_label,
        name="asset_label",
    ),
    path(
        "assets/<int:pk>/label/zpl/",
        views.asset_label_zpl,
        name="asset_label_zpl",
    ),
    path(
        "labels/pregenerate/",
        views.barcode_pregenerate,
        name="barcode_pregenerate",
    ),
    # Transactions
    path("transactions/", views.transaction_list, name="transaction_list"),
    # Categories
    path("categories/", views.category_list, name="category_list"),
    path(
        "categories/create/",
        views.category_create,
        name="category_create",
    ),
    path(
        "categories/<int:pk>/edit/",
        views.category_edit,
        name="category_edit",
    ),
    # Locations
    path("locations/", views.location_list, name="location_list"),
    path(
        "locations/create/",
        views.location_create,
        name="location_create",
    ),
    path(
        "locations/<int:pk>/",
        views.location_detail,
        name="location_detail",
    ),
    path(
        "locations/<int:pk>/edit/",
        views.location_edit,
        name="location_edit",
    ),
    path(
        "locations/<int:pk>/deactivate/",
        views.location_deactivate,
        name="location_deactivate",
    ),
    # Tags
    path("tags/", views.tag_list, name="tag_list"),
    path("tags/create/", views.tag_create, name="tag_create"),
    path("tags/search/", views.tag_search, name="tag_search"),
    path(
        "categories/search/",
        views.category_search,
        name="category_search",
    ),
    path(
        "locations/search/",
        views.location_search,
        name="location_search",
    ),
    path(
        "tags/create-inline/",
        views.tag_create_inline,
        name="tag_create_inline",
    ),
    path("tags/<int:pk>/edit/", views.tag_edit, name="tag_edit"),
    # Inline create/list endpoints (AJAX)
    path(
        "departments/json/",
        views.department_list_json,
        name="department_list_json",
    ),
    path(
        "departments/create-inline/",
        views.department_create_inline,
        name="department_create_inline",
    ),
    path(
        "categories/create-inline/",
        views.category_create_inline,
        name="category_create_inline",
    ),
    path(
        "locations/create-inline/",
        views.location_create_inline,
        name="location_create_inline",
    ),
    # Stocktake
    path("stocktake/", views.stocktake_list, name="stocktake_list"),
    path("stocktake/start/", views.stocktake_start, name="stocktake_start"),
    path(
        "stocktake/<int:pk>/",
        views.stocktake_detail,
        name="stocktake_detail",
    ),
    path(
        "stocktake/<int:pk>/confirm/",
        views.stocktake_confirm,
        name="stocktake_confirm",
    ),
    path(
        "stocktake/<int:pk>/complete/",
        views.stocktake_complete,
        name="stocktake_complete",
    ),
    path(
        "stocktake/<int:pk>/summary/",
        views.stocktake_summary,
        name="stocktake_summary",
    ),
    # Merge
    path(
        "assets/merge/select/",
        views.asset_merge_select,
        name="asset_merge_select",
    ),
    path(
        "assets/merge/execute/",
        views.asset_merge_execute,
        name="asset_merge_execute",
    ),
    # AI Analysis
    path(
        "assets/<int:pk>/images/<int:image_pk>/analyse/",
        views.ai_analyse,
        name="ai_analyse",
    ),
    path(
        "assets/<int:pk>/images/<int:image_pk>/ai-apply/",
        views.ai_apply_suggestions,
        name="ai_apply_suggestions",
    ),
    path(
        "assets/<int:pk>/images/<int:image_pk>/ai-status/",
        views.ai_status,
        name="ai_status",
    ),
    path(
        "assets/<int:pk>/images/<int:image_pk>/reanalyse/",
        views.ai_reanalyse,
        name="ai_reanalyse",
    ),
]

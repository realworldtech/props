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
    path(
        "assets/lost-stolen/",
        views.lost_stolen_report,
        name="lost_stolen_report",
    ),
    path("assets/bulk/", views.bulk_actions, name="bulk_actions"),
    path(
        "assets/labels/all-filtered/",
        views.print_all_filtered_labels,
        name="print_all_filtered_labels",
    ),
    path("assets/<int:pk>/", views.asset_detail, name="asset_detail"),
    path("assets/<int:pk>/edit/", views.asset_edit, name="asset_edit"),
    path("assets/<int:pk>/delete/", views.asset_delete, name="asset_delete"),
    path(
        "assets/<int:pk>/convert-serialisation/",
        views.asset_convert_serialisation,
        name="asset_convert_serialisation",
    ),
    # Quick Capture
    path("quick-capture/", views.quick_capture, name="quick_capture"),
    path("drafts/", views.drafts_queue, name="drafts_queue"),
    path(
        "drafts/bulk/",
        views.drafts_bulk_action,
        name="drafts_bulk_action",
    ),
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
    path(
        "nfc/<str:tag_uid>/history/",
        views.nfc_history,
        name="nfc_history",
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
        "assets/<int:pk>/remote-print/",
        views.remote_print_submit,
        name="remote_print_submit",
    ),
    path(
        "assets/<int:pk>/print-status/<uuid:job_id>/",
        views.print_job_status,
        name="print_job_status",
    ),
    path(
        "assets/<int:pk>/print-history/",
        views.print_history,
        name="print_history",
    ),
    path(
        "assets/<int:pk>/clear-barcode/",
        views.clear_barcode,
        name="clear_barcode",
    ),
    path(
        "labels/pregenerate/",
        views.barcode_pregenerate,
        name="barcode_pregenerate",
    ),
    path(
        "barcodes/virtual/",
        views.virtual_barcode_list,
        name="virtual_barcode_list",
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
    path(
        "locations/<int:pk>/checkout/",
        views.location_checkout,
        name="location_checkout",
    ),
    path(
        "locations/<int:pk>/checkin/",
        views.location_checkin,
        name="location_checkin",
    ),
    path(
        "locations/<int:pk>/print-label/",
        views.location_print_label,
        name="location_print_label",
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
        "assets/search/",
        views.asset_search,
        name="asset_search",
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
    # Hold Lists
    path("hold-lists/", views.holdlist_list, name="holdlist_list"),
    path(
        "hold-lists/create/",
        views.holdlist_create,
        name="holdlist_create",
    ),
    path(
        "hold-lists/<int:pk>/",
        views.holdlist_detail,
        name="holdlist_detail",
    ),
    path(
        "hold-lists/<int:pk>/edit/",
        views.holdlist_edit,
        name="holdlist_edit",
    ),
    path(
        "hold-lists/<int:pk>/delete/",
        views.holdlist_delete,
        name="holdlist_delete",
    ),
    path(
        "hold-lists/<int:pk>/lock/",
        views.holdlist_lock,
        name="holdlist_lock",
    ),
    path(
        "hold-lists/<int:pk>/unlock/",
        views.holdlist_unlock,
        name="holdlist_unlock",
    ),
    path(
        "hold-lists/<int:pk>/fulfil/",
        views.holdlist_fulfil,
        name="holdlist_fulfil",
    ),
    path(
        "hold-lists/<int:pk>/add-item/",
        views.holdlist_add_item,
        name="holdlist_add_item",
    ),
    path(
        "hold-lists/<int:pk>/remove-item/<int:item_pk>/",
        views.holdlist_remove_item,
        name="holdlist_remove_item",
    ),
    path(
        "hold-lists/<int:pk>/edit-item/<int:item_pk>/",
        views.holdlist_edit_item,
        name="holdlist_edit_item",
    ),
    path(
        "hold-lists/<int:pk>/pick-sheet/",
        views.holdlist_pick_sheet,
        name="holdlist_pick_sheet",
    ),
    path(
        "hold-lists/<int:pk>/items/<int:item_pk>/pull-status/",
        views.holdlist_update_pull_status,
        name="holdlist_update_pull_status",
    ),
    # Projects
    path("projects/", views.project_list, name="project_list"),
    path("projects/create/", views.project_create, name="project_create"),
    path("projects/<int:pk>/edit/", views.project_edit, name="project_edit"),
    path(
        "projects/<int:pk>/",
        views.project_detail,
        name="project_detail",
    ),
    path(
        "projects/<int:pk>/delete/",
        views.project_delete,
        name="project_delete",
    ),
    # Kit management
    path(
        "assets/<int:pk>/kit/",
        views.kit_contents,
        name="kit_contents",
    ),
    path(
        "assets/<int:pk>/kit/add/",
        views.kit_add_component,
        name="kit_add_component",
    ),
    path(
        "assets/<int:pk>/kit/remove/<int:component_pk>/",
        views.kit_remove_component,
        name="kit_remove_component",
    ),
]

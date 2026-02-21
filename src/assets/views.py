"""Views for the assets app."""

import json
import logging
import re

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django_ratelimit.decorators import ratelimit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.db.models.functions import Coalesce
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    AssetForm,
    AssetImageForm,
    CategoryForm,
    LocationForm,
    QuickCaptureForm,
    TagForm,
)
from .models import (
    Asset,
    AssetImage,
    AssetKit,
    AssetSerial,
    Category,
    Department,
    Location,
    NFCTag,
    PrintClient,
    PrintRequest,
    StocktakeItem,
    StocktakeSession,
    Tag,
    Transaction,
    VirtualBarcode,
)
from .services.permissions import (
    can_checkout_asset,
    can_delete_asset,
    can_edit_asset,
    can_handover_asset,
    get_user_role,
)

BARCODE_PATTERN = re.compile(r"^[A-Z]+-[A-Z0-9]+$", re.IGNORECASE)

logger = logging.getLogger(__name__)


# --- Dashboard ---


DASHBOARD_CACHE_TTL = 60  # seconds


def _compute_dashboard_aggregates(role, user_depts, dept_filter):
    """Compute expensive aggregate counts for the dashboard."""
    # Single query for all status counts (replaces 4 separate COUNTs)
    status_counts = Asset.objects.filter(dept_filter).aggregate(
        total_active=Coalesce(Count("pk", filter=Q(status="active")), 0),
        total_draft=Coalesce(Count("pk", filter=Q(status="draft")), 0),
        total_checked_out=Coalesce(
            Count("pk", filter=Q(checked_out_to__isnull=False)), 0
        ),
        total_missing=Coalesce(Count("pk", filter=Q(status="missing")), 0),
    )
    total_active = status_counts["total_active"]
    total_draft = status_counts["total_draft"]
    total_checked_out = status_counts["total_checked_out"]
    total_missing = status_counts["total_missing"]

    # Per-department counts
    dept_qs = Department.objects.filter(is_active=True)
    if role == "department_manager":
        dept_qs = dept_qs.filter(pk__in=user_depts)
    dept_counts = list(
        dept_qs.annotate(
            asset_count=Count(
                "categories__assets",
                filter=Q(categories__assets__status="active"),
            )
        )
        .order_by("-asset_count")
        .values("name", "asset_count")[:10]
    )

    # Per-category counts
    cat_qs = Category.objects.all()
    if role == "department_manager":
        cat_qs = cat_qs.filter(department__in=user_depts)
    cat_counts = list(
        cat_qs.annotate(
            asset_count=Count("assets", filter=Q(assets__status="active"))
        )
        .order_by("-asset_count")
        .values("name", "asset_count")[:10]
    )

    # Per-location counts
    loc_qs = Location.objects.filter(is_active=True)
    if role == "department_manager":
        loc_qs = loc_qs.annotate(
            asset_count=Count(
                "assets",
                filter=Q(assets__status="active")
                & Q(assets__category__department__in=user_depts),
            )
        )
    else:
        loc_qs = loc_qs.annotate(
            asset_count=Count(
                "assets",
                filter=Q(assets__status="active"),
            )
        )
    loc_counts = list(
        loc_qs.order_by("-asset_count").values("name", "asset_count")[:10]
    )

    # Top 10 tags
    if role == "department_manager":
        top_tags = list(
            Tag.objects.filter(assets__category__department__in=user_depts)
            .annotate(asset_count=Count("assets"))
            .order_by("-asset_count")
            .values("name", "asset_count")[:10]
        )
    else:
        top_tags = list(
            Tag.objects.annotate(asset_count=Count("assets"))
            .order_by("-asset_count")
            .values("name", "asset_count")[:10]
        )

    return {
        "total_active": total_active,
        "total_draft": total_draft,
        "total_checked_out": total_checked_out,
        "total_missing": total_missing,
        "dept_counts": dept_counts,
        "cat_counts": cat_counts,
        "loc_counts": loc_counts,
        "top_tags": top_tags,
    }


@login_required
def dashboard(request):
    """Display the dashboard with summary metrics."""
    role = get_user_role(request.user)
    show_actions = role != "viewer"

    # Department managers only see their departments
    if role == "department_manager":
        user_depts = Department.objects.filter(managers=request.user)
        dept_filter = Q(category__department__in=user_depts)
    else:
        user_depts = None
        dept_filter = Q()  # No filter for admin/member/viewer

    # Cached aggregate counts (60-second TTL)
    cache_key = f"dashboard_aggregates_{request.user.pk}"
    aggregates = cache.get(cache_key)
    if aggregates is None:
        aggregates = _compute_dashboard_aggregates(
            role, user_depts, dept_filter
        )
        cache.set(cache_key, aggregates, DASHBOARD_CACHE_TTL)

    # Real-time data: recent transactions, drafts, checked-out
    recent_transactions = Transaction.objects.select_related(
        "asset", "user", "borrower", "from_location", "to_location"
    )
    if role == "department_manager":
        recent_transactions = recent_transactions.filter(
            Q(asset__category__department__in=user_depts)
        )
    recent_transactions = recent_transactions[:10]

    recent_drafts = Asset.objects.filter(status="draft").select_related(
        "category", "created_by"
    )
    if role == "department_manager":
        recent_drafts = recent_drafts.filter(dept_filter)
    recent_drafts = recent_drafts[:5]

    checked_out_assets = Asset.objects.filter(
        checked_out_to__isnull=False
    ).select_related("checked_out_to", "category", "current_location")
    if role == "department_manager":
        checked_out_assets = checked_out_assets.filter(dept_filter)
    checked_out_assets = checked_out_assets[:10]

    # My borrowed items for members
    if role == "member":
        my_borrowed = Asset.objects.filter(
            checked_out_to=request.user
        ).select_related("category", "current_location")
    else:
        my_borrowed = Asset.objects.none()

    # Note: pending_approvals_count is provided by the user_role
    # context processor — no need to compute it here.

    # AI daily usage for admin dashboard (S2.14.5-03)
    ai_context = {}
    if role == "system_admin":
        import datetime

        from django.conf import settings as django_settings
        from django.utils import timezone

        from assets.models import AssetImage

        today_local = timezone.localdate()
        today_start = timezone.make_aware(
            datetime.datetime.combine(today_local, datetime.time.min)
        )
        ai_usage = AssetImage.objects.filter(
            ai_processed_at__gte=today_start,
            ai_processing_status="completed",
        ).count()
        ai_limit = getattr(django_settings, "AI_ANALYSIS_DAILY_LIMIT", 100)
        ai_context = {
            "ai_daily_usage": ai_usage,
            "ai_daily_remaining": max(0, ai_limit - ai_usage),
        }

    # L25: Active hold lists count
    from assets.models import HoldList

    hl_qs = HoldList.objects.filter(status__is_terminal=False)
    if role == "department_manager":
        hl_qs = hl_qs.filter(department__in=user_depts)
    active_hold_lists_count = hl_qs.count()

    return render(
        request,
        "assets/dashboard.html",
        {
            **aggregates,
            **ai_context,
            "recent_transactions": recent_transactions,
            "recent_drafts": recent_drafts,
            "checked_out_assets": checked_out_assets,
            "active_hold_lists_count": active_hold_lists_count,
            "show_actions": show_actions,
            "my_borrowed": my_borrowed,
        },
    )


@login_required
def my_borrowed_items(request):
    """Display items currently borrowed by the logged-in user."""
    assets = Asset.objects.filter(checked_out_to=request.user).select_related(
        "category", "current_location"
    )
    return render(
        request,
        "assets/my_borrowed_items.html",
        {"assets": assets},
    )


# --- Asset List ---


@login_required
def asset_list(request):
    """List assets with filtering and search."""
    queryset = Asset.objects.with_related()

    # Default to active assets only
    status = request.GET.get("status", "active")
    if status:
        queryset = queryset.filter(status=status)

    # Text search
    q = request.GET.get("q", "")
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q)
            | Q(description__icontains=q)
            | Q(barcode__icontains=q)
            | Q(tags__name__icontains=q)
            | Q(
                nfc_tags__tag_id__icontains=q,
                nfc_tags__removed_at__isnull=True,
            )
        ).distinct()

    # Filters
    department = request.GET.get("department")
    if department:
        queryset = queryset.filter(category__department_id=department)

    category = request.GET.get("category")
    if category:
        queryset = queryset.filter(category_id=category)

    location = request.GET.get("location")
    if location:
        if location == "checked_out":
            queryset = queryset.filter(checked_out_to__isnull=False)
        else:
            queryset = queryset.filter(current_location_id=location)

    tag = request.GET.get("tag")
    if tag:
        queryset = queryset.filter(tags__id=tag)

    condition = request.GET.get("condition")
    if condition:
        queryset = queryset.filter(condition=condition)

    is_kit_filter = request.GET.get("is_kit", "")
    if is_kit_filter == "1":
        queryset = queryset.filter(is_kit=True)
    elif is_kit_filter == "0":
        queryset = queryset.filter(is_kit=False)

    # Sorting
    SORT_FIELDS = {
        "name": "name",
        "-name": "-name",
        "status": "status",
        "-status": "-status",
        "category": "category__name",
        "-category": "-category__name",
        "location": "current_location__name",
        "-location": "-current_location__name",
        "updated": "updated_at",
        "-updated": "-updated_at",
        "condition": "condition",
        "-condition": "-condition",
        "created_at": "created_at",
        "-created_at": "-created_at",
    }
    sort = request.GET.get("sort", "-updated")
    order_by = SORT_FIELDS.get(sort, "-updated_at")
    queryset = queryset.order_by(order_by)

    # Pagination
    try:
        page_size = int(request.GET.get("page_size", 25))
    except (ValueError, TypeError):
        page_size = 25
    if page_size not in (25, 50, 100):
        page_size = 25
    paginator = Paginator(queryset, page_size)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    # View mode (list/grid)
    view_mode = request.GET.get(
        "view", request.COOKIES.get("view_mode", "list")
    )

    from django.contrib.auth import get_user_model

    UserModel = get_user_model()
    active_users = UserModel.objects.filter(is_active=True).order_by(
        "username"
    )

    # Connected remote printers for bulk remote print
    connected_clients = PrintClient.objects.filter(
        status="approved",
        is_active=True,
        is_connected=True,
    )
    connected_printers = []
    for pc in connected_clients:
        for printer in pc.printers or []:
            printer_id = printer.get("id", "")
            connected_printers.append(
                {
                    "client_pk": pc.pk,
                    "client_name": pc.name,
                    "printer_id": printer_id,
                    "printer_name": printer.get("name", ""),
                    "printer_type": printer.get("type", ""),
                    "key": f"{pc.pk}:{printer_id}",
                }
            )
    remote_print_available = len(connected_printers) > 0
    last_printer = request.session.get("last_printer", "")
    default_printer = None
    if last_printer and connected_printers:
        for p in connected_printers:
            if p["key"] == last_printer:
                default_printer = p
                break
    if default_printer is None and connected_printers:
        default_printer = connected_printers[0]

    context = {
        "page_obj": page_obj,
        "q": q,
        "current_status": status,
        "view_mode": view_mode,
        "departments": Department.objects.filter(is_active=True),
        "categories": Category.objects.all(),
        "locations": Location.objects.filter(is_active=True),
        "tags": Tag.objects.all(),
        "conditions": Asset.CONDITION_CHOICES,
        "statuses": Asset.STATUS_CHOICES,
        "page_size": page_size,
        "current_sort": sort,
        "active_users": active_users,
        "remote_print_available": remote_print_available,
        "connected_printers": connected_printers,
        "default_printer": default_printer,
    }

    # HTMX: Return partial template for AJAX requests
    template_name = "assets/asset_list.html"
    if request.htmx:
        template_name = "assets/partials/asset_list_results.html"

    response = render(request, template_name, context)
    if view_mode in ("list", "grid"):
        response.set_cookie("view_mode", view_mode, max_age=365 * 24 * 3600)
    return response


# --- Asset Detail ---


@login_required
def asset_detail(request, pk):
    """Display asset detail view."""
    # Build a single queryset with all prefetches for the detail view
    # (avoids the double-images query from with_related primary_images)
    detail_qs = Asset.objects.select_related(
        "category",
        "category__department",
        "current_location",
        "checked_out_to",
        "created_by",
    ).prefetch_related(
        "tags",
        "images",
        "nfc_tags",
        Prefetch(
            "transactions",
            queryset=Transaction.objects.select_related(
                "user",
                "borrower",
                "from_location",
                "to_location",
            ),
        ),
    )
    asset = get_object_or_404(detail_qs, pk=pk)
    transactions = asset.transactions.all()[:25]
    images = asset.images.all()
    # Use prefetched NFC tags and partition in Python
    all_nfc = list(asset.nfc_tags.all())
    active_nfc = [t for t in all_nfc if t.removed_at is None]
    removed_nfc = [t for t in all_nfc if t.removed_at is not None]

    can_edit = can_edit_asset(request.user, asset)
    can_delete = can_delete_asset(request.user, asset)
    can_checkout = can_checkout_asset(request.user, asset)
    can_handover = can_handover_asset(request.user, asset)

    from assets.services.holdlists import get_active_hold_items

    active_holds = get_active_hold_items(asset)

    # V494: Conversion link for managers/admins
    role = get_user_role(request.user)
    can_convert = role in ("system_admin", "department_manager")

    member_of_kits = AssetKit.objects.filter(component=asset).select_related(
        "kit"
    )

    # S7.5.4: Show a warning when the asset is checked out and
    # could be marked as missing
    checkout_warning = None
    if asset.is_checked_out and asset.status == "active":
        borrower = asset.checked_out_to
        borrower_name = (
            borrower.get_full_name() or borrower.username
            if borrower
            else "unknown"
        )
        checkout_warning = (
            f"Warning: This asset is currently checked out to "
            f"{borrower_name}. Changing the status to missing, "
            f"lost, or stolen requires explicit confirmation "
            f"acknowledging that the asset is checked out."
        )

    # V492: Serialised assets — active + archived serial sections
    active_serials = []
    archived_serials = []
    if asset.is_serialised:
        active_serials = list(
            asset.serials.filter(is_archived=False).select_related(
                "checked_out_to", "current_location"
            )
        )
        archived_serials = list(
            asset.serials.filter(is_archived=True).select_related(
                "current_location"
            )
        )

    # S2.4.5-09/10: Remote print availability
    connected_clients = PrintClient.objects.filter(
        status="approved",
        is_active=True,
        is_connected=True,
    )
    connected_printers = []
    for pc in connected_clients:
        for printer in pc.printers or []:
            printer_id = printer.get("id", "")
            connected_printers.append(
                {
                    "client_pk": pc.pk,
                    "client_name": pc.name,
                    "printer_id": printer_id,
                    "printer_name": printer.get("name", ""),
                    "printer_type": printer.get("type", ""),
                    "key": f"{pc.pk}:{printer_id}",
                }
            )
    remote_print_available = len(connected_printers) > 0

    # Determine default printer from session (last used) or first available
    last_printer = request.session.get("last_printer", "")
    default_printer = None
    if last_printer and connected_printers:
        for p in connected_printers:
            key = f"{p['client_pk']}:{p['printer_id']}"
            if key == last_printer:
                default_printer = p
                break
    if default_printer is None and connected_printers:
        default_printer = connected_printers[0]

    return render(
        request,
        "assets/asset_detail.html",
        {
            "asset": asset,
            "transactions": transactions,
            "images": images,
            "active_nfc": active_nfc,
            "removed_nfc": removed_nfc,
            "can_edit": can_edit,
            "can_delete": can_delete,
            "can_checkout": can_checkout,
            "can_handover": can_handover,
            "active_holds": active_holds,
            "member_of_kits": member_of_kits,
            "checkout_warning": checkout_warning,
            "can_convert": can_convert,
            "active_serials": active_serials,
            "archived_serials": archived_serials,
            "remote_print_available": remote_print_available,
            "connected_printers": connected_printers,
            "last_printer": last_printer,
            "default_printer": default_printer,
            "site_url": settings.SITE_URL,
        },
    )


# --- Asset Create/Edit ---


def _get_fk_display_names(form):
    """Extract display names for FK autocomplete fields from form data."""
    result = {"category_name": "", "location_name": ""}
    cat_id = (
        form.data.get("category")
        if form.is_bound
        else (
            form.initial.get("category")
            or (form.instance.category_id if form.instance.pk else None)
        )
    )
    loc_id = (
        form.data.get("current_location")
        if form.is_bound
        else (
            form.initial.get("current_location")
            or (
                form.instance.current_location_id if form.instance.pk else None
            )
        )
    )
    if cat_id:
        try:
            result["category_name"] = Category.objects.get(pk=cat_id).name
        except Category.DoesNotExist:
            pass
    if loc_id:
        try:
            result["location_name"] = str(Location.objects.get(pk=loc_id))
        except Location.DoesNotExist:
            pass
    return result


@login_required
def asset_create(request):
    """Create a new asset."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    if request.method == "POST":
        form = AssetForm(request.POST)
        if form.is_valid():
            asset = form.save(commit=False)
            asset.created_by = request.user
            asset.save()
            form.save_m2m()
            messages.success(request, f"Asset '{asset.name}' created.")
            return redirect("assets:asset_detail", pk=asset.pk)
    else:
        form = AssetForm()

    context = {"form": form}
    context.update(_get_fk_display_names(form))
    return render(request, "assets/asset_form.html", context)


@login_required
def asset_edit(request, pk):
    """Edit an existing asset."""
    asset = get_object_or_404(Asset, pk=pk)
    if not can_edit_asset(request.user, asset):
        raise PermissionDenied
    is_publish = request.method == "POST" and request.POST.get("publish")
    if request.method == "POST":
        post_data = request.POST.copy()
        if is_publish:
            post_data["status"] = "active"
        form = AssetForm(post_data, request.FILES, instance=asset)
        if form.is_valid():
            form.save()
            # Handle uploaded images
            images = request.FILES.getlist("images")
            captions = request.POST.getlist("image_captions")
            for i, img_file in enumerate(images):
                # Convert all images to JPEG for storage (§S2.2.5-05a)
                img_file = _convert_to_jpeg(img_file)
                caption = captions[i] if i < len(captions) else ""
                is_primary = not asset.images.exists() and i == 0
                AssetImage.objects.create(
                    asset=asset,
                    image=img_file,
                    caption=caption,
                    is_primary=is_primary,
                )
            if is_publish:
                messages.success(request, f"Asset '{asset.name}' published.")
            else:
                messages.success(request, f"Asset '{asset.name}' updated.")
            return redirect("assets:asset_detail", pk=asset.pk)
    else:
        form = AssetForm(instance=asset)

    context = {"form": form, "asset": asset}
    context["images"] = asset.images.all().order_by(
        "-is_primary", "uploaded_at"
    )
    context["primary_image"] = asset.primary_image
    context.update(_get_fk_display_names(form))
    return render(request, "assets/asset_form.html", context)


@login_required
def asset_delete(request, pk):
    """Soft-delete (dispose) an asset."""
    asset = get_object_or_404(Asset, pk=pk)
    if not can_delete_asset(request.user, asset):
        raise PermissionDenied
    if asset.is_checked_out:
        messages.error(
            request,
            "Cannot delete an asset that is currently checked out. "
            "Check it in first.",
        )
        return redirect("assets:asset_detail", pk=asset.pk)

    if request.method == "POST":
        asset.status = "disposed"
        asset.save(update_fields=["status"])
        messages.success(request, f"Asset '{asset.name}' has been disposed.")
        return redirect("assets:asset_list")

    return render(
        request, "assets/asset_confirm_delete.html", {"asset": asset}
    )


# --- Quick Capture ---


@login_required
def quick_capture(request):
    """Mobile-first quick capture workflow."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    if request.method == "POST":
        form = QuickCaptureForm(request.POST, request.FILES)
        if form.is_valid():
            name = form.cleaned_data.get("name")
            notes = form.cleaned_data.get("notes", "")
            scanned_code = form.cleaned_data.get("scanned_code", "")
            images = request.FILES.getlist("image")

            # Validate: at least one of name, image, or scanned_code
            if not name and not images and not scanned_code:
                messages.error(
                    request,
                    "Please provide at least a name, photo, or scanned code.",
                )
                return render(
                    request,
                    "assets/quick_capture.html",
                    {"form": form},
                )

            # Auto-generate name if not provided
            if not name:
                now = timezone.localtime()
                name = f"Quick Capture {now.strftime('%b %d %H:%M')}"

            # Handle scanned code conflicts
            barcode_value = ""
            nfc_tag_id = ""

            if scanned_code:
                if BARCODE_PATTERN.match(scanned_code):
                    # It looks like a barcode
                    if Asset.objects.filter(barcode=scanned_code).exists():
                        existing = Asset.objects.get(barcode=scanned_code)
                        messages.error(
                            request,
                            f"Barcode '{scanned_code}' is already "
                            f"assigned to '{existing.name}' "
                            f"({existing.barcode}).",
                        )
                        return render(
                            request,
                            "assets/quick_capture.html",
                            {"form": form},
                        )
                    barcode_value = scanned_code
                else:
                    # Treat as NFC tag ID
                    active_nfc = (
                        NFCTag.objects.filter(
                            tag_id__iexact=scanned_code,
                            removed_at__isnull=True,
                        )
                        .select_related("asset")
                        .first()
                    )
                    if active_nfc:
                        messages.error(
                            request,
                            f"NFC tag '{scanned_code}' is already "
                            f"assigned to '{active_nfc.asset.name}' "
                            f"({active_nfc.asset.barcode}).",
                        )
                        return render(
                            request,
                            "assets/quick_capture.html",
                            {"form": form},
                        )
                    nfc_tag_id = scanned_code

            # Create the draft asset
            asset = Asset(
                name=name,
                notes=notes,
                status="draft",
                created_by=request.user,
            )
            if barcode_value:
                asset.barcode = barcode_value
            asset.save()

            logger.info(
                "Quick capture: user %s created draft asset pk=%d "
                "'%s' (barcode=%s, nfc=%s, images=%d)",
                request.user,
                asset.pk,
                asset.name,
                barcode_value or "none",
                nfc_tag_id or "none",
                len(images),
            )

            # Create NFC tag if applicable
            if nfc_tag_id:
                NFCTag.objects.create(
                    tag_id=nfc_tag_id,
                    asset=asset,
                    assigned_by=request.user,
                    notes="Auto-assigned during quick capture",
                )

            # Handle image uploads (supports multiple)
            if images:
                from props.context_processors import is_ai_analysis_enabled

                ai_enabled = is_ai_analysis_enabled()
                for idx, img_file in enumerate(images):
                    # Convert all images to JPEG for storage (§S2.2.5-05a)
                    img_file = _convert_to_jpeg(img_file)
                    img_obj = AssetImage.objects.create(
                        asset=asset,
                        image=img_file,
                        is_primary=(idx == 0),
                        uploaded_by=request.user,
                    )
                    if ai_enabled:
                        img_obj.ai_processing_status = "pending"
                        img_obj.save(update_fields=["ai_processing_status"])
                        from .tasks import analyse_image

                        analyse_image.delay(img_obj.pk)

            # Return success with capture-another option
            success_context = {
                "asset": asset,
            }
            if nfc_tag_id:
                success_context["nfc_tag_id"] = nfc_tag_id
                success_context["site_url"] = settings.SITE_URL

            if request.htmx:
                return render(
                    request,
                    "assets/partials/capture_success.html",
                    success_context,
                )

            messages.success(
                request,
                f"Draft asset '{asset.name}' created ({asset.barcode}).",
            )
            page_context = {
                "form": QuickCaptureForm(),
                "just_created": asset,
            }
            if nfc_tag_id:
                page_context["nfc_tag_id"] = nfc_tag_id
                page_context["site_url"] = settings.SITE_URL
            return render(
                request,
                "assets/quick_capture.html",
                page_context,
            )
        else:
            logger.warning(
                "Quick capture form validation failed for user %s: %s",
                request.user,
                form.errors.as_json(),
            )
            messages.error(
                request,
                "Please fix the errors below and try again.",
            )
    else:
        form = QuickCaptureForm()
        # Pre-populate scanned code from URL parameter
        scanned_code = request.GET.get("code", "")
        if scanned_code:
            form = QuickCaptureForm(initial={"scanned_code": scanned_code})

    return render(request, "assets/quick_capture.html", {"form": form})


@login_required
def drafts_queue(request):
    """Display the drafts queue - all draft assets."""
    queryset = (
        Asset.objects.filter(status="draft")
        .select_related("category", "created_by")
        .prefetch_related("images")
        .order_by("-created_at")
    )

    paginator = Paginator(queryset, 25)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "assets/drafts_queue.html",
        {"page_obj": page_obj},
    )


@login_required
def drafts_bulk_action(request):
    """Handle bulk actions on draft assets (V20)."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    if request.method == "POST":
        action = request.POST.get("action", "")
        selected_ids = request.POST.getlist("selected")
        if not selected_ids:
            messages.warning(request, "No assets selected.")
            return redirect("assets:drafts_queue")

        drafts = Asset.objects.filter(pk__in=selected_ids, status="draft")
        if action == "activate":
            category_id = request.POST.get("category")
            location_id = request.POST.get("location")
            if category_id and location_id:
                count = drafts.count()
                drafts.update(
                    status="active",
                    category_id=category_id,
                    current_location_id=location_id,
                )
                messages.success(
                    request,
                    f"{count} draft(s) activated.",
                )
            else:
                # Activate without category/location changes
                activated = 0
                for draft in drafts:
                    if draft.category and draft.current_location:
                        draft.status = "active"
                        draft.save(update_fields=["status"])
                        activated += 1
                messages.success(request, f"{activated} draft(s) activated.")
        elif action == "delete":
            count = drafts.count()
            drafts.update(status="disposed")
            messages.success(request, f"{count} draft(s) disposed.")

    return redirect("assets:drafts_queue")


# --- Scan & Lookup ---


@login_required
def scan_view(request):
    """Barcode/NFC scanning interface."""
    return render(request, "assets/scan.html")


@login_required
@ratelimit(key="user", rate="60/m", method="GET", block=True)
def scan_lookup(request):
    """Look up an asset by scanned code. Returns JSON."""
    code = request.GET.get("code", "").strip()
    if not code:
        return JsonResponse({"found": False, "code": "", "error": "No code"})

    # 1. Check barcode
    try:
        asset = Asset.objects.get(barcode=code)
        return JsonResponse(
            {
                "found": True,
                "asset_id": asset.pk,
                "asset_name": asset.name,
                "barcode": asset.barcode,
                "location": (
                    str(asset.current_location)
                    if asset.current_location
                    else None
                ),
                "url": asset.get_absolute_url(),
                "is_draft": asset.status == "draft",
            }
        )
    except Asset.DoesNotExist:
        pass

    # 2. Check serial barcode
    try:
        serial = AssetSerial.objects.select_related("asset").get(
            barcode__iexact=code
        )
        parent = serial.asset
        url = f"{parent.get_absolute_url()}?serial={serial.pk}"
        # S7.19.3: Show disposed message for disposed serials
        response_data = {
            "found": True,
            "asset_id": parent.pk,
            "asset_name": parent.name,
            "barcode": parent.barcode,
            "serial_id": serial.pk,
            "serial_number": serial.serial_number,
            "location": (
                str(serial.current_location or parent.current_location)
                if (serial.current_location or parent.current_location)
                else None
            ),
            "url": url,
            "is_draft": parent.status == "draft",
        }
        if serial.status == "disposed":
            response_data["status"] = "disposed"
            response_data["message"] = (
                f"This serial ({serial.serial_number}) of "
                f"{parent.name} has been disposed."
            )
        return JsonResponse(response_data)
    except AssetSerial.DoesNotExist:
        pass

    # 2b. S7.19.3: Check disposed serial via transaction history
    # (barcode is cleared on disposal but stored in transactions)
    disposed_txn = (
        Transaction.objects.filter(
            serial_barcode__iexact=code,
            serial__status="disposed",
        )
        .select_related("asset", "serial")
        .first()
    )
    if disposed_txn and disposed_txn.serial:
        serial = disposed_txn.serial
        parent = disposed_txn.asset
        url = f"{parent.get_absolute_url()}?serial={serial.pk}"
        return JsonResponse(
            {
                "found": True,
                "asset_id": parent.pk,
                "asset_name": parent.name,
                "barcode": parent.barcode,
                "serial_id": serial.pk,
                "serial_number": serial.serial_number,
                "status": "disposed",
                "message": (
                    f"This serial ({serial.serial_number}) of "
                    f"{parent.name} has been disposed."
                ),
                "url": url,
            }
        )

    # 3. Check active NFC tag
    nfc_asset = NFCTag.get_asset_by_tag(code)
    if nfc_asset:
        return JsonResponse(
            {
                "found": True,
                "asset_id": nfc_asset.pk,
                "asset_name": nfc_asset.name,
                "barcode": nfc_asset.barcode,
                "location": (
                    str(nfc_asset.current_location)
                    if nfc_asset.current_location
                    else None
                ),
                "url": nfc_asset.get_absolute_url(),
                "is_draft": nfc_asset.status == "draft",
            }
        )

    # 4. Not found - redirect to quick capture
    from django.urls import reverse

    return JsonResponse(
        {
            "found": False,
            "code": code,
            "quick_capture_url": (
                f"{reverse('assets:quick_capture')}?code={code}"
            ),
        }
    )


@login_required
@ratelimit(key="user", rate="60/m", method="GET", block=True)
def asset_by_identifier(request, identifier):
    """Unified lookup endpoint: /a/{identifier}/."""
    # 1. Barcode match
    try:
        asset = Asset.objects.get(barcode=identifier)
        return redirect("assets:asset_detail", pk=asset.pk)
    except Asset.DoesNotExist:
        pass

    # 2. Serial barcode match
    try:
        serial = AssetSerial.objects.select_related("asset").get(
            barcode__iexact=identifier
        )
        parent = serial.asset
        # S7.19.3: Show disposed message for disposed serials
        if serial.status == "disposed":
            messages.warning(
                request,
                f"This serial ({serial.serial_number}) of "
                f"{parent.name} has been disposed.",
            )
        return redirect(f"{parent.get_absolute_url()}?serial={serial.pk}")
    except AssetSerial.DoesNotExist:
        pass

    # 2b. S7.19.3: Check disposed serial via transaction history
    disposed_txn = (
        Transaction.objects.filter(
            serial_barcode__iexact=identifier,
            serial__status="disposed",
        )
        .select_related("asset", "serial")
        .first()
    )
    if disposed_txn and disposed_txn.serial:
        serial = disposed_txn.serial
        parent = disposed_txn.asset
        messages.warning(
            request,
            f"This serial ({serial.serial_number}) of "
            f"{parent.name} has been disposed.",
        )
        return redirect(f"{parent.get_absolute_url()}?serial={serial.pk}")

    # 3. Active NFC tag match
    nfc_asset = NFCTag.get_asset_by_tag(identifier)
    if nfc_asset:
        return redirect("assets:asset_detail", pk=nfc_asset.pk)

    # 4. Not found - redirect to Quick Capture
    from django.urls import reverse

    return redirect(f"{reverse('assets:quick_capture')}?code={identifier}")


# --- Image Management ---


def _convert_to_jpeg(uploaded_file):
    """Convert any supported image to JPEG (§S2.2.5-05a).

    Handles JPEG, PNG, WebP, HEIC/HEIF, and MPO inputs.
    Preserves EXIF orientation data, converts to RGB, and saves
    at quality 85. Returns an InMemoryUploadedFile with .jpg
    extension and image/jpeg content type.
    """
    from io import BytesIO

    from pi_heif import register_heif_opener
    from PIL import Image as PILImage
    from PIL import ImageOps

    from django.core.files.uploadedfile import InMemoryUploadedFile

    register_heif_opener()

    img = PILImage.open(uploaded_file)

    # Apply EXIF orientation before any conversion
    img = ImageOps.exif_transpose(img)

    # Preserve EXIF data if available
    exif_data = img.info.get("exif")

    # Convert to RGB (drops alpha channel from PNG/WebP)
    img = img.convert("RGB")

    buf = BytesIO()
    save_kwargs = {"format": "JPEG", "quality": 85}
    if exif_data:
        save_kwargs["exif"] = exif_data
    img.save(buf, **save_kwargs)
    buf.seek(0)

    # Always use .jpg extension
    name = uploaded_file.name
    if "." in name:
        name = name.rsplit(".", 1)[0] + ".jpg"
    else:
        name = name + ".jpg"

    return InMemoryUploadedFile(
        file=buf,
        field_name=(
            uploaded_file.field_name
            if hasattr(uploaded_file, "field_name")
            else "image"
        ),
        name=name,
        content_type="image/jpeg",
        size=buf.getbuffer().nbytes,
        charset=None,
    )


@login_required
def image_upload(request, pk):
    """Upload an image to an asset."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    # V899: Configurable via MAX_IMAGE_SIZE_MB env var
    import os

    MAX_IMAGE_SIZE_MB = int(os.environ.get("MAX_IMAGE_SIZE_MB", "25"))
    MAX_IMAGE_SIZE = MAX_IMAGE_SIZE_MB * 1024 * 1024
    ALLOWED_TYPES = {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
        "image/mpo",
    }

    asset = get_object_or_404(Asset, pk=pk)
    if request.method == "POST":
        form = AssetImageForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data["image"]
            # Validate file size (§7.8.1)
            if uploaded_file.size > MAX_IMAGE_SIZE:
                messages.error(
                    request, "Image too large. Maximum size is 25 MB."
                )
                return redirect("assets:asset_detail", pk=pk)
            # Validate MIME type (§7.8.2)
            if uploaded_file.content_type not in ALLOWED_TYPES:
                messages.error(
                    request,
                    "Invalid image type. Only JPEG, PNG, WebP, "
                    "and HEIC are allowed. Detected {}.".format(
                        uploaded_file.content_type
                    ),
                )
                return redirect("assets:asset_detail", pk=pk)
            # Convert all images to JPEG for storage (§S2.2.5-05a)
            uploaded_file = _convert_to_jpeg(uploaded_file)
            form.files["image"] = uploaded_file
            try:
                image = form.save(commit=False)
                image.asset = asset
                image.uploaded_by = request.user
                image.save()
                # Trigger AI analysis if enabled
                from props.context_processors import is_ai_analysis_enabled

                if is_ai_analysis_enabled():
                    image.ai_processing_status = "pending"
                    image.save(update_fields=["ai_processing_status"])
                    from .tasks import analyse_image

                    analyse_image.delay(image.pk)
                messages.success(request, "Image uploaded.")
            except OSError:
                messages.error(
                    request,
                    "Image upload failed: storage is currently "
                    "unavailable. Please try again later.",
                )
    return redirect("assets:asset_detail", pk=pk)


@login_required
def image_delete(request, pk, image_pk):
    """Delete an asset image."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    image = get_object_or_404(AssetImage, pk=image_pk, asset_id=pk)
    if request.method == "POST":
        was_primary = image.is_primary
        image.delete()
        # Promote next image to primary if we deleted the primary
        if was_primary:
            next_image = AssetImage.objects.filter(asset_id=pk).first()
            if next_image:
                next_image.is_primary = True
                next_image.save(update_fields=["is_primary"])
        messages.success(request, "Image deleted.")
    return redirect("assets:asset_detail", pk=pk)


@login_required
def image_set_primary(request, pk, image_pk):
    """Set an image as primary."""
    image = get_object_or_404(AssetImage, pk=image_pk, asset_id=pk)
    if request.method == "POST":
        image.is_primary = True
        image.save()
        messages.success(request, "Primary image updated.")
    return redirect("assets:asset_detail", pk=pk)


# --- Check-out / Check-in / Transfer ---


@login_required
def asset_checkout(request, pk):
    """Check out an asset to a borrower."""
    asset = get_object_or_404(Asset, pk=pk)
    if not can_checkout_asset(request.user, asset):
        raise PermissionDenied

    # V500: For non-serialised, only block if no quantity available
    if not asset.is_serialised and asset.available_count <= 0:
        messages.error(
            request,
            "No units available for checkout.",
        )
        return redirect("assets:asset_detail", pk=pk)

    # For serialised assets, block if no serials are available
    if asset.is_serialised and asset.available_count == 0:
        messages.error(
            request,
            "All serial units are currently checked out.",
        )
        return redirect("assets:asset_detail", pk=pk)

    if asset.status not in ("active", "draft"):
        messages.error(
            request, "Only active or draft assets can be checked out."
        )
        return redirect("assets:asset_detail", pk=pk)

    # Block checkout if asset is on an active hold list
    from assets.services.holdlists import (
        check_asset_held,
        get_held_quantity,
    )

    hold_is_active = check_asset_held(asset)
    has_override = request.user.has_perm("assets.override_hold_checkout")
    # For non-serialised assets, hold blocking is quantity-aware:
    # only block if the requested qty would exceed available.
    # This is checked later in POST for non-serialised assets.
    # For serialised assets, blocking is serial-aware (checked in POST).
    # For the initial page load, we only block if ALL units are held
    # (or override perm is missing).
    if hold_is_active and not has_override:
        if not asset.is_serialised:
            held_qty = get_held_quantity(asset)
            available_after_holds = asset.quantity - held_qty
            if available_after_holds <= 0:
                messages.error(
                    request,
                    "This asset is on an active hold list and "
                    "cannot be checked out. An override permission "
                    "is required.",
                )
                return redirect("assets:asset_detail", pk=pk)
        else:
            # For serialised assets, only block if ALL available
            # serials are held. If some serials are not pinned
            # on a hold list, allow checkout (filtering happens
            # in POST).
            from assets.services.holdlists import (
                get_active_hold_items as _get_ahi,
            )

            held_items = _get_ahi(asset)
            # Check if any hold item pins a specific serial
            has_serial_pins = any(i.serial_id is not None for i in held_items)
            if not has_serial_pins:
                # No specific serials pinned; block all
                messages.error(
                    request,
                    "This asset is on an active hold list and "
                    "cannot be checked out. An override "
                    "permission is required.",
                )
                return redirect("assets:asset_detail", pk=pk)

    from django.contrib.auth import get_user_model

    User = get_user_model()

    if request.method == "POST":
        borrower_id = request.POST.get("borrower")
        notes = request.POST.get("notes", "")
        destination_id = request.POST.get("destination_location", "")

        # Log hold override in transaction notes
        if hold_is_active and has_override:
            from assets.services.holdlists import get_active_hold_items

            active_items = get_active_hold_items(asset)
            hl_names = ", ".join(set(i.hold_list.name for i in active_items))
            override_note = f"Hold override: asset is held on [{hl_names}]"
            if notes:
                notes = f"{notes}\n{override_note}"
            else:
                notes = override_note

        try:
            borrower = User.objects.get(pk=borrower_id)
        except User.DoesNotExist:
            messages.error(request, "Invalid borrower selected.")
            return redirect("assets:asset_checkout", pk=pk)

        destination = None
        if destination_id:
            try:
                destination = Location.objects.get(
                    pk=destination_id, is_active=True
                )
            except Location.DoesNotExist:
                pass

        # Parse optional backdating
        action_date_str = request.POST.get("action_date", "").strip()
        extra_kwargs = {}
        if action_date_str:
            from django.utils.dateparse import parse_datetime

            action_date = parse_datetime(action_date_str)
            if action_date:
                if timezone.is_naive(action_date):
                    action_date = timezone.make_aware(action_date)
                # S7.21.1: Reject future dates
                if action_date > timezone.now():
                    messages.error(
                        request,
                        "Date cannot be in the future.",
                    )
                    return redirect("assets:asset_checkout", pk=pk)
                # S7.21.2: Reject dates before asset creation
                if asset.created_at and action_date < asset.created_at:
                    messages.error(
                        request,
                        "Date cannot be before the asset was " "created.",
                    )
                    return redirect("assets:asset_checkout", pk=pk)
                # S7.21.4: Reject backdated checkout when already
                # checked out at that date
                if asset.is_checked_out:
                    messages.error(
                        request,
                        "Cannot backdate a checkout for an asset "
                        "that was already checked out at that "
                        "date.",
                    )
                    return redirect("assets:asset_checkout", pk=pk)
                extra_kwargs = {
                    "timestamp": action_date,
                    "is_backdated": True,
                }

        # Parse optional due date (L2: S2.3.2-10)
        due_date_str = request.POST.get("due_date", "").strip()
        if due_date_str:
            from django.utils.dateparse import parse_datetime as pd

            due_date = pd(due_date_str)
            if due_date:
                if timezone.is_naive(due_date):
                    due_date = timezone.make_aware(due_date)
                extra_kwargs["due_date"] = due_date

        # Re-fetch with select_for_update to prevent concurrent checkout
        from django.db import transaction as db_transaction

        with db_transaction.atomic():
            locked_asset = Asset.objects.select_for_update().get(pk=pk)

            if not locked_asset.home_location:
                locked_asset.home_location = locked_asset.current_location

            if locked_asset.is_serialised:
                # --- Serialised checkout ---
                serial_ids = request.POST.getlist("serial_ids")
                auto_assign = request.POST.get("auto_assign_count", "").strip()

                if auto_assign and not serial_ids:
                    # V496: Auto-assign mode — system picks serials
                    count = max(1, int(auto_assign))
                    available = AssetSerial.objects.filter(
                        asset=locked_asset,
                        status="active",
                        checked_out_to__isnull=True,
                        is_archived=False,
                    )[:count]
                else:
                    available = AssetSerial.objects.filter(
                        asset=locked_asset,
                        pk__in=serial_ids,
                        status="active",
                        checked_out_to__isnull=True,
                        is_archived=False,
                    )
                # Serial-aware hold blocking: only block
                # serials that are specifically pinned on a
                # hold list, not all serials of the asset
                if hold_is_active and not has_override:
                    from assets.models import HoldListItem

                    held_serial_ids = set(
                        HoldListItem.objects.filter(
                            asset=locked_asset,
                            serial__isnull=False,
                        )
                        .exclude(
                            hold_list__status__is_terminal=True,
                        )
                        .exclude(pull_status="unavailable")
                        .values_list("serial_id", flat=True)
                    )
                    available = available.exclude(pk__in=held_serial_ids)
                if not available.exists():
                    messages.error(
                        request,
                        "No available serials selected.",
                    )
                    return redirect("assets:asset_checkout", pk=pk)
                for serial in available:
                    tx_kwargs = {
                        "asset": locked_asset,
                        "user": request.user,
                        "action": "checkout",
                        "from_location": (
                            serial.current_location
                            or locked_asset.current_location
                        ),
                        "borrower": borrower,
                        "serial": serial,
                        "notes": notes,
                        **extra_kwargs,
                    }
                    if destination:
                        tx_kwargs["to_location"] = destination
                    Transaction.objects.create(**tx_kwargs)
                    serial.checked_out_to = borrower
                    if destination:
                        serial.current_location = destination
                    serial.save(
                        update_fields=[
                            "checked_out_to",
                            "current_location",
                        ]
                    )
                # If all serials are now checked out, mark
                # the parent asset
                all_out = not AssetSerial.objects.filter(
                    asset=locked_asset,
                    status="active",
                    checked_out_to__isnull=True,
                    is_archived=False,
                ).exists()
                if all_out:
                    locked_asset.checked_out_to = borrower
                if destination:
                    locked_asset.current_location = destination
                locked_asset.save(
                    update_fields=[
                        "checked_out_to",
                        "home_location",
                        "current_location",
                    ]
                )
            else:
                # --- Non-serialised checkout (V500: concurrent) ---
                avail = locked_asset.available_count
                if avail <= 0:
                    messages.error(
                        request,
                        "No units available for checkout.",
                    )
                    return redirect("assets:asset_detail", pk=pk)

                quantity = int(request.POST.get("quantity", 1) or 1)
                quantity = max(1, min(quantity, avail))

                tx_kwargs = {
                    "asset": locked_asset,
                    "user": request.user,
                    "action": "checkout",
                    "from_location": (locked_asset.current_location),
                    "borrower": borrower,
                    "quantity": quantity,
                    "notes": notes,
                    **extra_kwargs,
                }
                if destination:
                    tx_kwargs["to_location"] = destination

                Transaction.objects.create(**tx_kwargs)
                locked_asset.checked_out_to = borrower
                if destination:
                    locked_asset.current_location = destination
                locked_asset.save(
                    update_fields=[
                        "checked_out_to",
                        "home_location",
                        "current_location",
                    ]
                )

        messages.success(
            request,
            f"'{asset.name}' checked out to "
            f"{borrower.get_display_name()}.",
        )
        return redirect("assets:asset_detail", pk=pk)

    from django.contrib.auth.models import Group

    # Filter to users with Borrower+ roles
    borrower_roles = [
        "Borrower",
        "Member",
        "Department Manager",
        "System Admin",
    ]
    users = (
        User.objects.filter(
            is_active=True,
            groups__name__in=borrower_roles,
        )
        .distinct()
        .order_by("username")
    )
    # Also include superusers
    from django.db.models import Q

    users = (
        User.objects.filter(
            Q(is_active=True, groups__name__in=borrower_roles)
            | Q(is_superuser=True, is_active=True)
        )
        .distinct()
        .order_by("username")
    )

    borrower_group = Group.objects.filter(name="Borrower").first()
    # V130: Split users into internal staff and external borrowers
    borrower_ids = set()
    if borrower_group:
        borrower_ids = set(
            borrower_group.user_set.values_list("pk", flat=True)
        )
    internal_users = [u for u in users if u.pk not in borrower_ids]
    external_borrowers = [u for u in users if u.pk in borrower_ids]

    locations = Location.objects.filter(is_active=True)
    context = {
        "asset": asset,
        "users": users,
        "internal_users": internal_users,
        "external_borrowers": external_borrowers,
        "locations": locations,
        "borrower_group_id": (borrower_group.pk if borrower_group else None),
    }

    # S7.16.4: Warn if this asset is a kit-only component
    kit_only_links = AssetKit.objects.filter(
        component=asset, is_kit_only=True
    ).select_related("kit")
    if kit_only_links.exists():
        kit_names = ", ".join(link.kit.name for link in kit_only_links)
        context["kit_only_warning"] = (
            f"This asset is normally checked out as part of a "
            f"kit ({kit_names}). You may still check it out "
            f"independently."
        )

    if asset.is_serialised:
        context["available_serials"] = AssetSerial.objects.filter(
            asset=asset,
            status="active",
            checked_out_to__isnull=True,
            is_archived=False,
        )
    else:
        context["show_quantity"] = True
        context["max_quantity"] = asset.quantity

    return render(
        request,
        "assets/asset_checkout.html",
        context,
    )


@login_required
def asset_checkin(request, pk):
    """Check in an asset to a location."""
    asset = get_object_or_404(Asset, pk=pk)
    if not can_checkout_asset(request.user, asset):
        raise PermissionDenied

    if request.method == "POST":
        location_id = request.POST.get("location")
        notes = request.POST.get("notes", "")

        try:
            to_location = Location.objects.get(pk=location_id, is_active=True)
        except Location.DoesNotExist:
            messages.error(request, "Invalid location selected.")
            return redirect("assets:asset_checkin", pk=pk)

        # Parse optional backdating
        action_date_str = request.POST.get("action_date", "").strip()
        extra_kwargs = {}
        if action_date_str:
            from django.utils.dateparse import parse_datetime

            action_date = parse_datetime(action_date_str)
            if action_date:
                if timezone.is_naive(action_date):
                    action_date = timezone.make_aware(action_date)
                # S7.21.1: Reject future dates
                if action_date > timezone.now():
                    messages.error(
                        request,
                        "Date cannot be in the future.",
                    )
                    return redirect("assets:asset_checkin", pk=pk)
                # S7.21.2: Reject dates before asset creation
                if asset.created_at and action_date < asset.created_at:
                    messages.error(
                        request,
                        "Date cannot be before the asset was " "created.",
                    )
                    return redirect("assets:asset_checkin", pk=pk)
                extra_kwargs = {
                    "timestamp": action_date,
                    "is_backdated": True,
                }

        if asset.is_serialised:
            # --- Serialised check-in ---
            serial_ids = request.POST.getlist("serial_ids")
            checked_out = AssetSerial.objects.filter(
                asset=asset,
                pk__in=serial_ids,
                checked_out_to__isnull=False,
                is_archived=False,
            )
            for serial in checked_out:
                Transaction.objects.create(
                    asset=asset,
                    user=request.user,
                    action="checkin",
                    from_location=serial.current_location,
                    to_location=to_location,
                    serial=serial,
                    notes=notes,
                    **extra_kwargs,
                )
                serial.checked_out_to = None
                serial.current_location = to_location
                serial.save(
                    update_fields=[
                        "checked_out_to",
                        "current_location",
                    ]
                )
            # If no serials remain checked out, clear the
            # parent asset
            still_out = AssetSerial.objects.filter(
                asset=asset,
                checked_out_to__isnull=False,
                is_archived=False,
            ).exists()
            if not still_out:
                asset.checked_out_to = None
            asset.current_location = to_location
            serial_update_fields = [
                "checked_out_to",
                "current_location",
            ]
            # L34: Set as home location checkbox
            if request.POST.get("set_home_location") == "1":
                asset.home_location = to_location
                serial_update_fields.append("home_location")
            asset.save(update_fields=serial_update_fields)
        else:
            # --- Non-serialised check-in ---
            Transaction.objects.create(
                asset=asset,
                user=request.user,
                action="checkin",
                from_location=asset.current_location,
                to_location=to_location,
                notes=notes,
                **extra_kwargs,
            )
            asset.checked_out_to = None
            asset.current_location = to_location
            update_fields = [
                "checked_out_to",
                "current_location",
            ]
            # L34: Set as home location checkbox
            if request.POST.get("set_home_location") == "1":
                asset.home_location = to_location
                update_fields.append("home_location")
            asset.save(update_fields=update_fields)

        messages.success(
            request,
            f"'{asset.name}' checked in to {to_location.name}.",
        )
        return redirect("assets:asset_detail", pk=pk)

    locations = Location.objects.filter(is_active=True)
    context = {
        "asset": asset,
        "locations": locations,
        "home_location": asset.home_location,
    }

    if asset.is_serialised:
        context["checked_out_serials"] = AssetSerial.objects.filter(
            asset=asset,
            checked_out_to__isnull=False,
            is_archived=False,
        )

    return render(
        request,
        "assets/asset_checkin.html",
        context,
    )


@login_required
def asset_transfer(request, pk):
    """Transfer an asset to a new location."""
    asset = get_object_or_404(Asset, pk=pk)
    if not can_edit_asset(request.user, asset):
        raise PermissionDenied

    if asset.is_checked_out:
        messages.error(
            request,
            "Cannot transfer a checked-out asset. Check it in first.",
        )
        return redirect("assets:asset_detail", pk=pk)

    if asset.status not in ("active",):
        messages.error(request, "Only active assets can be transferred.")
        return redirect("assets:asset_detail", pk=pk)

    if request.method == "POST":
        location_id = request.POST.get("location")
        notes = request.POST.get("notes", "")

        try:
            to_location = Location.objects.get(pk=location_id, is_active=True)
        except Location.DoesNotExist:
            messages.error(request, "Invalid location selected.")
            return redirect("assets:asset_transfer", pk=pk)

        # Parse optional backdating
        action_date_str = request.POST.get("action_date", "").strip()
        extra_kwargs = {}
        if action_date_str:
            from django.utils.dateparse import parse_datetime

            action_date = parse_datetime(action_date_str)
            if action_date:
                if timezone.is_naive(action_date):
                    action_date = timezone.make_aware(action_date)
                # S7.21.1: Reject future dates
                if action_date > timezone.now():
                    messages.error(
                        request,
                        "Date cannot be in the future.",
                    )
                    return redirect("assets:asset_transfer", pk=pk)
                # S7.21.2: Reject dates before asset creation
                if asset.created_at and action_date < asset.created_at:
                    messages.error(
                        request,
                        "Date cannot be before the asset was " "created.",
                    )
                    return redirect("assets:asset_transfer", pk=pk)
                extra_kwargs = {
                    "timestamp": action_date,
                    "is_backdated": True,
                }

        Transaction.objects.create(
            asset=asset,
            user=request.user,
            action="transfer",
            from_location=asset.current_location,
            to_location=to_location,
            notes=notes,
            **extra_kwargs,
        )
        asset.current_location = to_location
        asset.save(update_fields=["current_location"])

        messages.success(
            request,
            f"'{asset.name}' transferred to {to_location.name}.",
        )
        return redirect("assets:asset_detail", pk=pk)

    locations = Location.objects.filter(is_active=True)
    return render(
        request,
        "assets/asset_transfer.html",
        {"asset": asset, "locations": locations},
    )


@login_required
def asset_relocate(request, pk):
    """Relocate an asset's home location."""
    asset = get_object_or_404(Asset, pk=pk)
    if not can_edit_asset(request.user, asset):
        raise PermissionDenied

    if asset.status not in ("active",):
        messages.error(request, "Only active assets can be relocated.")
        return redirect("assets:asset_detail", pk=pk)

    if request.method == "POST":
        location_id = request.POST.get("location")
        notes = request.POST.get("notes", "")

        try:
            to_location = Location.objects.get(pk=location_id, is_active=True)
        except Location.DoesNotExist:
            # S7.22.2: Check if the location exists but is
            # inactive
            try:
                inactive_loc = Location.objects.get(
                    pk=location_id, is_active=False
                )
                messages.error(
                    request,
                    f"Cannot relocate to '{inactive_loc.name}': "
                    "this location is inactive.",
                )
            except Location.DoesNotExist:
                messages.error(request, "Invalid location selected.")
            return redirect("assets:asset_relocate", pk=pk)

        # S7.22.1: Reject relocate to same location
        if (
            asset.current_location_id
            and to_location.pk == asset.current_location_id
        ):
            messages.error(
                request,
                "Asset is already at this location.",
            )
            return redirect("assets:asset_relocate", pk=pk)

        # Parse optional backdating (same pattern as transfer)
        action_date_str = request.POST.get("action_date", "").strip()
        extra_kwargs = {}
        if action_date_str:
            from django.utils.dateparse import parse_datetime

            action_date = parse_datetime(action_date_str)
            if action_date:
                if timezone.is_naive(action_date):
                    action_date = timezone.make_aware(action_date)
                if action_date <= timezone.now():
                    extra_kwargs = {
                        "timestamp": action_date,
                        "is_backdated": True,
                    }

        old_location = asset.current_location
        Transaction.objects.create(
            asset=asset,
            user=request.user,
            action="relocate",
            from_location=old_location,
            to_location=to_location,
            notes=notes,
            **extra_kwargs,
        )

        # S7.22.3: If checked out, update home_location AND
        # current_location (track where asset is being relocated)
        if asset.is_checked_out:
            asset.home_location = to_location
            asset.current_location = to_location
            asset.save(
                update_fields=[
                    "home_location",
                    "current_location",
                ]
            )
        else:
            asset.current_location = to_location
            asset.save(update_fields=["current_location"])

        # S7.22.5: Cascade relocate to serials at same location
        if asset.is_serialised:
            AssetSerial.objects.filter(
                asset=asset,
                current_location=old_location,
                is_archived=False,
            ).update(current_location=to_location)

        messages.success(
            request,
            f"'{asset.name}' relocated to {to_location.name}.",
        )
        return redirect("assets:asset_detail", pk=pk)

    locations = Location.objects.filter(is_active=True)
    return render(
        request,
        "assets/asset_relocate.html",
        {"asset": asset, "locations": locations},
    )


# --- Transaction History ---


@login_required
def transaction_list(request):
    """Global transaction list."""
    from django.contrib.auth import get_user_model

    User = get_user_model()

    queryset = Transaction.objects.select_related(
        "asset", "user", "borrower", "from_location", "to_location"
    )

    action = request.GET.get("action")
    if action:
        queryset = queryset.filter(action=action)

    # User filter
    user_id = request.GET.get("user")
    if user_id:
        queryset = queryset.filter(user_id=user_id)

    # Date range filters
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    if date_from:
        queryset = queryset.filter(timestamp__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(timestamp__date__lte=date_to)

    # Users who have transactions (for filter dropdown)
    transaction_user_ids = Transaction.objects.values_list(
        "user_id", flat=True
    ).distinct()
    transaction_users = User.objects.filter(
        pk__in=transaction_user_ids
    ).order_by("username")

    paginator = Paginator(queryset, 50)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "assets/transaction_list.html",
        {
            "page_obj": page_obj,
            "actions": Transaction.ACTION_CHOICES,
            "current_action": action,
            "transaction_users": transaction_users,
            "current_user_id": user_id,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    )


# --- Categories, Locations, Tags CRUD ---


@login_required
def category_list(request):
    """List all categories, scoped to managed departments for DMs."""
    categories = (
        Category.objects.select_related("department")
        .annotate(asset_count=Count("assets"))
        .order_by("department__name", "name")
    )
    role = get_user_role(request.user)
    if role == "department_manager":
        managed_ids = request.user.managed_departments.values_list(
            "pk", flat=True
        )
        categories = categories.filter(department_id__in=managed_ids)
    return render(
        request,
        "assets/category_list.html",
        {"categories": categories},
    )


@login_required
def location_list(request):
    """List all locations."""
    locations = Location.objects.filter(parent__isnull=True).prefetch_related(
        "children"
    )
    return render(
        request,
        "assets/location_list.html",
        {"locations": locations},
    )


@login_required
def location_detail(request, pk):
    """Display location detail with assets (including descendants)."""
    location = get_object_or_404(Location, pk=pk)
    descendant_ids = [loc.pk for loc in location.get_descendants()]
    all_location_ids = [location.pk] + descendant_ids
    assets = Asset.objects.filter(
        current_location_id__in=all_location_ids
    ).select_related("category", "checked_out_to")

    paginator = Paginator(assets, 25)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "assets/location_detail.html",
        {"location": location, "page_obj": page_obj},
    )


@login_required
def tag_list(request):
    """List all tags."""
    tags = Tag.objects.annotate(asset_count=Count("assets"))
    return render(request, "assets/tag_list.html", {"tags": tags})


# --- Labels ---


@login_required
def asset_label(request, pk):
    """Render a printable label for an asset."""
    asset = get_object_or_404(Asset, pk=pk)

    # Generate QR code as base64 data URI
    qr_data_uri = ""
    try:
        import base64
        from io import BytesIO

        import qrcode

        qr = qrcode.QRCode(version=1, box_size=4, border=1)
        qr.add_data(request.build_absolute_uri(f"/a/{asset.barcode}/"))
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")

        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)
        encoded = base64.b64encode(buffer.getvalue()).decode()
        qr_data_uri = f"data:image/png;base64,{encoded}"
    except ImportError:
        pass

    return render(
        request,
        "assets/asset_label.html",
        {"asset": asset, "qr_data_uri": qr_data_uri},
    )


@login_required
def asset_label_zpl(request, pk):
    """Send a label to a Zebra network printer via ZPL."""
    asset = get_object_or_404(Asset, pk=pk)

    from .services.zebra import generate_zpl, print_zpl

    category_name = asset.category.name if asset.category else ""
    zpl = generate_zpl(asset.barcode, asset.name, category_name)

    if request.GET.get("raw"):
        return HttpResponse(zpl, content_type="text/plain")

    success = print_zpl(zpl)
    if success:
        messages.success(request, f"Label sent to printer for '{asset.name}'.")
    else:
        messages.error(
            request,
            "Failed to send label to printer. Check printer configuration.",
        )
    return redirect("assets:asset_detail", pk=pk)


def _toast_html(message, level="success"):
    """Return an auto-dismissing toast HTML fragment for HTMX responses."""
    colours = {
        "success": (
            "bg-emerald-500/10 border-emerald-500/20 "
            "text-emerald-600 dark:text-emerald-300"
        ),
        "error": (
            "bg-red-500/10 border-red-500/20 " "text-red-600 dark:text-red-300"
        ),
    }
    css = colours.get(level, colours["success"])
    return (
        f'<div x-data x-init="setTimeout(() => $el.remove(), 4000)"'
        f' class="pointer-events-auto px-4 py-3 rounded-xl border'
        f" backdrop-blur-sm text-sm animate-slide-down {css}"
        f">{message}</div>"
    )


@login_required
def remote_print_submit(request, pk):
    """Submit a remote print request (S2.4.5-09)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    is_htmx = request.headers.get("HX-Request") == "true"

    def _respond(message, success=True):
        if is_htmx:
            level = "success" if success else "error"
            return HttpResponse(_toast_html(message, level))
        return JsonResponse({"success": success, "error": message})

    # V89: Permission check — Members+, deny Viewers/Borrowers
    role = get_user_role(request.user)
    if role in ("viewer", "borrower"):
        raise PermissionDenied

    asset = get_object_or_404(Asset, pk=pk)
    client_pk = request.POST.get("client_pk")
    printer_id = request.POST.get("printer_id", "")
    quantity = int(request.POST.get("quantity", 1))

    # Validate the print client exists and is eligible
    try:
        pc = PrintClient.objects.get(pk=client_pk)
    except (PrintClient.DoesNotExist, ValueError, TypeError):
        return _respond("Print client not found.", success=False)

    if pc.status != "approved" or not pc.is_active:
        return _respond(
            "Print client is not approved or active.",
            success=False,
        )

    # S2.4.5c-01: TOCTOU check — re-validate connectivity
    if not pc.is_connected:
        return _respond("Client is no longer connected.", success=False)

    pr = PrintRequest.objects.create(
        asset=asset,
        print_client=pc,
        printer_id=printer_id,
        quantity=quantity,
        requested_by=request.user,
    )

    from .services.print_dispatch import dispatch_print_job

    base_url = request.build_absolute_uri("/").rstrip("/")
    dispatch_print_job(pr, site_url=base_url)

    # Remember last-used printer in session
    request.session["last_printer"] = f"{client_pk}:{printer_id}"

    return _respond(f"Label sent to {pc.name}.")


@login_required
def clear_barcode(request, pk):
    """Clear an asset's barcode (V163 — S2.4.2-05)."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    asset = get_object_or_404(Asset, pk=pk)
    if request.method == "POST" and asset.barcode:
        old_barcode = asset.barcode
        # Use queryset.update() to bypass save() override which
        # auto-regenerates blank barcodes
        Asset.objects.filter(pk=pk).update(barcode="", barcode_image="")
        Transaction.objects.create(
            asset=asset,
            user=request.user,
            action="audit",
            from_location=asset.current_location,
            to_location=asset.current_location,
            notes=f"Barcode cleared: {old_barcode}",
        )
        messages.success(request, f"Barcode '{old_barcode}' cleared.")
    return redirect("assets:asset_detail", pk=pk)


@login_required
def barcode_pregenerate(request):
    """Pre-generate barcode labels for blank assets or print existing."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied

    # Check if IDs provided (batch print existing assets)
    ids_param = request.GET.get("ids", "")
    if ids_param:
        try:
            asset_ids = [int(pk) for pk in ids_param.split(",") if pk.strip()]
            assets = Asset.objects.filter(pk__in=asset_ids)

            # Generate QR codes for labels
            label_assets = []
            for asset in assets:
                qr_data_uri = ""
                try:
                    import base64
                    from io import BytesIO

                    import qrcode

                    qr = qrcode.QRCode(version=1, box_size=4, border=1)
                    qr.add_data(
                        request.build_absolute_uri(f"/a/{asset.barcode}/")
                    )
                    qr.make(fit=True)
                    qr_img = qr.make_image(
                        fill_color="black", back_color="white"
                    )
                    buffer = BytesIO()
                    qr_img.save(buffer, format="PNG")
                    buffer.seek(0)
                    qr_data_uri = (
                        f"data:image/png;base64,"
                        f"{base64.b64encode(buffer.getvalue()).decode()}"
                    )
                except ImportError:
                    pass
                label_assets.append(
                    {"asset": asset, "qr_data_uri": qr_data_uri}
                )

            return render(
                request,
                "assets/bulk_labels.html",
                {"label_assets": label_assets},
            )
        except (ValueError, TypeError):
            messages.error(request, "Invalid asset IDs provided.")
            return redirect("assets:dashboard")

    if request.method == "POST":
        import uuid

        try:
            quantity = int(request.POST.get("quantity", 10))
        except (ValueError, TypeError):
            quantity = 10
        quantity = min(max(quantity, 1), 100)  # Clamp 1-100

        # V167: Resolve prefix from department or global setting
        dept_id = request.POST.get("department", "")
        prefix = getattr(settings, "BARCODE_PREFIX", "ASSET")
        if dept_id:
            try:
                dept = Department.objects.get(pk=int(dept_id))
                if dept.barcode_prefix:
                    prefix = dept.barcode_prefix
            except (Department.DoesNotExist, ValueError, TypeError):
                pass

        # V166: Generate barcodes in memory — no DB storage
        # V170: Validate uniqueness against Asset + AssetSerial
        generated_barcodes = []
        max_retries = 10
        for _i in range(quantity):
            for _attempt in range(max_retries):
                barcode_str = f"{prefix}-{uuid.uuid4().hex[:8].upper()}"
                # V170: Cross-table uniqueness check
                if (
                    not Asset.objects.filter(barcode=barcode_str).exists()
                    and not AssetSerial.objects.filter(
                        barcode=barcode_str
                    ).exists()
                    and barcode_str not in generated_barcodes
                    and BARCODE_PATTERN.match(barcode_str)
                ):
                    generated_barcodes.append(barcode_str)
                    break

        # Generate QR codes for labels
        label_assets = []
        for bc in generated_barcodes:
            qr_data_uri = ""
            try:
                import base64
                from io import BytesIO

                import qrcode

                qr = qrcode.QRCode(version=1, box_size=4, border=1)
                qr.add_data(request.build_absolute_uri(f"/a/{bc}/"))
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="black", back_color="white")
                buffer = BytesIO()
                qr_img.save(buffer, format="PNG")
                buffer.seek(0)
                qr_data_uri = (
                    f"data:image/png;base64,"
                    f"{base64.b64encode(buffer.getvalue()).decode()}"
                )
            except ImportError:
                pass
            label_assets.append({"barcode": bc, "qr_data_uri": qr_data_uri})

        messages.success(
            request,
            f"{len(generated_barcodes)} barcode labels " f"pre-generated.",
        )
        return render(
            request,
            "assets/virtual_bulk_labels.html",
            {"label_assets": label_assets},
        )

    departments = Department.objects.filter(is_active=True)
    return render(
        request,
        "assets/barcode_pregenerate.html",
        {"departments": departments},
    )


@login_required
def virtual_barcode_list(request):
    """List unassigned virtual barcodes."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    virtual_barcodes = VirtualBarcode.objects.filter(
        assigned_to_asset__isnull=True,
    ).select_related("created_by")
    return render(
        request,
        "assets/virtual_barcode_list.html",
        {"virtual_barcodes": virtual_barcodes},
    )


# --- NFC Tag Management ---


@login_required
def nfc_add(request, pk):
    """Assign an NFC tag to an asset."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    asset = get_object_or_404(Asset, pk=pk)

    if request.method == "POST":
        tag_id = request.POST.get("tag_id", "").strip()
        notes = request.POST.get("notes", "")

        if not tag_id:
            messages.error(request, "Please enter an NFC tag ID.")
            return redirect("assets:asset_detail", pk=pk)

        with transaction.atomic():
            # Lock any existing active tag row to prevent races
            existing = (
                NFCTag.objects.select_for_update()
                .filter(
                    tag_id__iexact=tag_id,
                    removed_at__isnull=True,
                )
                .select_related("asset")
                .first()
            )
            if existing:
                messages.error(
                    request,
                    f"NFC tag '{tag_id}' is already assigned to "
                    f"'{existing.asset.name}' "
                    f"({existing.asset.barcode}).",
                )
                return redirect("assets:asset_detail", pk=pk)

            NFCTag.objects.create(
                tag_id=tag_id,
                asset=asset,
                assigned_by=request.user,
                notes=notes,
            )
        messages.success(request, f"NFC tag '{tag_id}' assigned.")
        return redirect("assets:asset_detail", pk=pk)

    return render(
        request,
        "assets/nfc_add.html",
        {"asset": asset, "site_url": settings.SITE_URL},
    )


@login_required
def nfc_remove(request, pk, nfc_pk):
    """Remove an NFC tag from an asset."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    # Verify the tag exists (raises 404 if not found)
    get_object_or_404(NFCTag, pk=nfc_pk, asset_id=pk, removed_at__isnull=True)

    if request.method == "POST":
        with transaction.atomic():
            # Lock the tag row to prevent concurrent removal
            locked_tag = (
                NFCTag.objects.select_for_update()
                .filter(
                    pk=nfc_pk,
                    asset_id=pk,
                    removed_at__isnull=True,
                )
                .first()
            )
            if locked_tag:
                locked_tag.removed_at = timezone.now()
                locked_tag.removed_by = request.user
                locked_tag.notes = (
                    f"{locked_tag.notes}\nRemoved: "
                    f"{request.POST.get('notes', '')}".strip()
                )
                locked_tag.save()
                messages.success(
                    request,
                    f"NFC tag '{locked_tag.tag_id}' removed.",
                )

    return redirect("assets:asset_detail", pk=pk)


@login_required
def nfc_history(request, tag_uid):
    """Show the full history of an NFC tag across all assets."""
    tags = (
        NFCTag.objects.filter(tag_id__iexact=tag_uid)
        .select_related("asset", "assigned_by", "removed_by")
        .order_by("-assigned_at")
    )
    if not tags.exists():
        from django.http import Http404

        raise Http404("NFC tag not found.")

    return render(
        request,
        "assets/nfc_history.html",
        {
            "tag_uid": tag_uid,
            "tags": tags,
        },
    )


# --- Location CRUD ---


@login_required
def location_create(request):
    """Create a new location."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    if request.method == "POST":
        form = LocationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request, f"Location '{form.instance.name}' created."
            )
            return redirect("assets:location_list")
    else:
        parent_id = request.GET.get("parent")
        initial = {}
        if parent_id:
            initial["parent"] = parent_id
        form = LocationForm(initial=initial)

    return render(request, "assets/location_form.html", {"form": form})


@login_required
def location_edit(request, pk):
    """Edit a location."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    location = get_object_or_404(Location, pk=pk)
    if request.method == "POST":
        form = LocationForm(request.POST, instance=location)
        if form.is_valid():
            form.save()
            messages.success(request, f"Location '{location.name}' updated.")
            return redirect("assets:location_detail", pk=pk)
    else:
        form = LocationForm(instance=location)

    return render(
        request,
        "assets/location_form.html",
        {"form": form, "location": location},
    )


@login_required
def location_deactivate(request, pk):
    """Deactivate a location."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    location = get_object_or_404(Location, pk=pk)
    if request.method == "POST":
        # Check if any active assets at this location or its descendants
        descendant_locations = location.get_descendants()
        all_location_ids = [location.pk] + [
            loc.pk for loc in descendant_locations
        ]
        asset_count = Asset.objects.filter(
            current_location_id__in=all_location_ids, status="active"
        ).count()
        if asset_count > 0:
            messages.error(
                request,
                f"Cannot deactivate '{location.name}' — "
                f"{asset_count} active asset(s) at this location "
                f"or its descendants. Transfer them first.",
            )
            return redirect("assets:location_detail", pk=pk)
        # V747: Log note for assets using this as home_location
        home_assets = Asset.objects.filter(home_location=location)
        for home_asset in home_assets:
            Transaction.objects.create(
                asset=home_asset,
                user=request.user,
                action="note",
                notes=(
                    f"Home location '{location.name}' was "
                    f"deactivated. Home location cleared."
                ),
            )
        # Clear home_location from all affected assets
        home_assets.update(home_location=None)

        location.is_active = False
        location.save(update_fields=["is_active"])
        messages.success(request, f"Location '{location.name}' deactivated.")
        return redirect("assets:location_list")
    return redirect("assets:location_detail", pk=pk)


# --- Category CRUD ---


@login_required
def category_create(request):
    """Create a new category."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            # Dept managers can only create in their departments
            if role == "department_manager":
                dept = form.cleaned_data.get("department")
                managed = request.user.managed_departments.all()
                if dept not in managed:
                    raise PermissionDenied
            form.save()
            messages.success(
                request, f"Category '{form.instance.name}' created."
            )
            return redirect("assets:category_list")
    else:
        form = CategoryForm()

    return render(request, "assets/category_form.html", {"form": form})


@login_required
def category_edit(request, pk):
    """Edit a category."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    category = get_object_or_404(Category, pk=pk)
    # Dept managers can only edit categories in their departments
    if role == "department_manager":
        managed = request.user.managed_departments.all()
        if category.department not in managed:
            raise PermissionDenied
    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, f"Category '{category.name}' updated.")
            return redirect("assets:category_list")
    else:
        form = CategoryForm(instance=category)

    return render(
        request,
        "assets/category_form.html",
        {"form": form, "category": category},
    )


# --- Tag CRUD ---


@login_required
def tag_create(request):
    """Create a new tag."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    if request.method == "POST":
        form = TagForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"Tag '{form.instance.name}' created.")
            return redirect("assets:tag_list")
    else:
        form = TagForm()

    return render(request, "assets/tag_form.html", {"form": form})


@login_required
def tag_edit(request, pk):
    """Edit a tag."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    tag = get_object_or_404(Tag, pk=pk)
    if request.method == "POST":
        form = TagForm(request.POST, instance=tag)
        if form.is_valid():
            form.save()
            messages.success(request, f"Tag '{tag.name}' updated.")
            return redirect("assets:tag_list")
    else:
        form = TagForm(instance=tag)

    return render(request, "assets/tag_form.html", {"form": form, "tag": tag})


@login_required
def tag_search(request):
    """Search tags by name. Returns JSON list for autocomplete."""
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse([], safe=False)
    tags = Tag.objects.filter(name__icontains=q).values("id", "name")[:20]
    return JsonResponse(list(tags), safe=False)


@login_required
def tag_create_inline(request):
    """Create a tag inline via AJAX POST. Returns JSON."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    role = get_user_role(request.user)
    if role == "viewer":
        return JsonResponse({"error": "Permission denied"}, status=403)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    name = data.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Name is required"}, status=400)
    if len(name) > 50:
        return JsonResponse(
            {"error": "Name must be 50 characters or less"}, status=400
        )
    tag, created = Tag.objects.get_or_create(
        name__iexact=name, defaults={"name": name}
    )
    return JsonResponse({"id": tag.id, "name": tag.name, "created": created})


@login_required
def category_search(request):
    """Search categories by name. Returns JSON list for autocomplete."""
    q = request.GET.get("q", "").strip()
    qs = Category.objects.select_related("department")
    if q:
        qs = qs.filter(name__icontains=q)
    cats = qs.values("id", "name", "department__name")[:30]
    results = [
        {"id": c["id"], "name": c["name"], "department": c["department__name"]}
        for c in cats
    ]
    return JsonResponse(results, safe=False)


@login_required
def location_search(request):
    """Search locations by name. Returns JSON list for autocomplete."""
    q = request.GET.get("q", "").strip()
    qs = Location.objects.filter(is_active=True)
    if q:
        qs = qs.filter(name__icontains=q)
    locs = qs[:30]
    results = [{"id": loc.id, "name": str(loc)} for loc in locs]
    return JsonResponse(results, safe=False)


@login_required
def department_list_json(request):
    """Return all departments as JSON for modal select population."""
    depts = Department.objects.filter(is_active=True).values("id", "name")
    return JsonResponse(list(depts), safe=False)


@login_required
def department_create_inline(request):
    """Create a department inline via AJAX POST. Returns JSON."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        return JsonResponse({"error": "Permission denied"}, status=403)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    name = data.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Name is required"}, status=400)
    if len(name) > 100:
        return JsonResponse(
            {"error": "Name must be 100 characters or less"}, status=400
        )
    if Department.objects.filter(name__iexact=name).exists():
        dept = Department.objects.get(name__iexact=name)
        return JsonResponse(
            {"id": dept.id, "name": dept.name, "created": False}
        )
    dept = Department.objects.create(name=name)
    return JsonResponse({"id": dept.id, "name": dept.name, "created": True})


@login_required
def category_create_inline(request):
    """Create a category inline via AJAX POST. Returns JSON."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    name = data.get("name", "").strip()
    department_id = data.get("department_id")
    if not name:
        return JsonResponse({"error": "Name is required"}, status=400)
    if not department_id:
        return JsonResponse({"error": "Department is required"}, status=400)
    try:
        department = Department.objects.get(pk=department_id)
    except Department.DoesNotExist:
        return JsonResponse({"error": "Invalid department"}, status=400)
    if Category.objects.filter(
        name__iexact=name, department=department
    ).exists():
        cat = Category.objects.get(name__iexact=name, department=department)
        return JsonResponse({"id": cat.id, "name": cat.name, "created": False})
    cat = Category.objects.create(name=name, department=department)
    return JsonResponse({"id": cat.id, "name": cat.name, "created": True})


@login_required
def location_create_inline(request):
    """Create a location inline via AJAX POST. Returns JSON."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    name = data.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Name is required"}, status=400)
    if len(name) > 100:
        return JsonResponse(
            {"error": "Name must be 100 characters or less"}, status=400
        )
    parent_id = data.get("parent_id")
    parent = None
    if parent_id:
        try:
            parent = Location.objects.get(pk=parent_id)
        except Location.DoesNotExist:
            return JsonResponse(
                {"error": "Invalid parent location"}, status=400
            )
    loc = Location.objects.create(name=name, parent=parent)
    return JsonResponse({"id": loc.id, "name": str(loc), "created": True})


# --- Stocktake ---


@login_required
def stocktake_list(request):
    """List all stocktake sessions."""
    sessions = StocktakeSession.objects.select_related(
        "location", "started_by"
    )
    return render(
        request,
        "assets/stocktake_list.html",
        {"sessions": sessions},
    )


@login_required
def stocktake_start(request):
    """Start a new stocktake session."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    if request.method == "POST":
        location_id = request.POST.get("location")
        try:
            location = Location.objects.get(pk=location_id, is_active=True)
        except Location.DoesNotExist:
            messages.error(request, "Invalid location selected.")
            return redirect("assets:stocktake_list")

        # Check for existing in-progress session
        existing = StocktakeSession.objects.filter(
            location=location, status="in_progress"
        ).first()
        if existing:
            messages.info(
                request,
                f"There is already a stocktake in progress for "
                f"'{location.name}'. Resuming it.",
            )
            return redirect("assets:stocktake_detail", pk=existing.pk)

        session = StocktakeSession.objects.create(
            location=location,
            started_by=request.user,
        )
        # M6: Snapshot expected assets at start time
        # V236/V737: Include checked-out assets whose home_location
        # matches so they appear flagged rather than missing.
        expected = Asset.objects.filter(
            Q(current_location=location)
            | Q(
                home_location=location,
                checked_out_to__isnull=False,
            ),
            status__in=["active", "missing"],
        ).distinct()
        StocktakeItem.objects.bulk_create(
            [
                StocktakeItem(session=session, asset=a, status="expected")
                for a in expected
            ]
        )
        messages.success(
            request,
            f"Stocktake started for '{location.name}'.",
        )
        return redirect("assets:stocktake_detail", pk=session.pk)

    locations = Location.objects.filter(is_active=True)
    return render(
        request,
        "assets/stocktake_start.html",
        {"locations": locations},
    )


@login_required
def stocktake_detail(request, pk):
    """View and interact with a stocktake session."""
    session = get_object_or_404(
        StocktakeSession.objects.select_related("location", "started_by"),
        pk=pk,
    )
    expected = session.expected_assets.select_related(
        "category", "checked_out_to"
    ).prefetch_related("images")
    confirmed_ids = set(session.confirmed_assets.values_list("pk", flat=True))

    # Paginate expected assets (S7.9.4)
    paginator = Paginator(expected, 25)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "assets/stocktake_detail.html",
        {
            "session": session,
            "expected": page_obj,
            "confirmed_ids": confirmed_ids,
            "page_obj": page_obj,
        },
    )


@login_required
def stocktake_confirm(request, pk):
    """Confirm an asset in a stocktake session (HTMX or POST)."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    session = get_object_or_404(StocktakeSession, pk=pk, status="in_progress")

    if request.method == "POST":
        # Handle transfer confirmation (V31)
        transfer_id = request.POST.get("transfer_asset_id")
        if transfer_id:
            try:
                transfer_asset = Asset.objects.get(pk=transfer_id)
                old_loc = transfer_asset.current_location
                transfer_asset.current_location = session.location
                transfer_asset.save(update_fields=["current_location"])
                Transaction.objects.create(
                    asset=transfer_asset,
                    user=request.user,
                    action="relocate",
                    from_location=old_loc,
                    to_location=session.location,
                    notes=f"Transferred during stocktake #{session.pk}",
                )
                messages.success(
                    request,
                    f"'{transfer_asset.name}' transferred to "
                    f"'{session.location.name}'.",
                )
            except Asset.DoesNotExist:
                pass
            return redirect("assets:stocktake_detail", pk=pk)

        # Handle dismiss discrepancy (V31)
        dismiss_id = request.POST.get("dismiss_asset_id")
        if dismiss_id:
            messages.info(
                request, "Location discrepancy noted but not transferred."
            )
            return redirect("assets:stocktake_detail", pk=pk)

        asset_id = request.POST.get("asset_id")
        if asset_id:
            try:
                asset = Asset.objects.get(pk=asset_id)
                session.confirmed_assets.add(asset)
                Transaction.objects.create(
                    asset=asset,
                    user=request.user,
                    action="audit",
                    from_location=asset.current_location,
                    to_location=session.location,
                    notes=f"Confirmed during stocktake #{session.pk}",
                )
                # G9: Update or create StocktakeItem
                updated = StocktakeItem.objects.filter(
                    session=session, asset=asset
                ).update(
                    status="confirmed",
                    scanned_by=request.user,
                    scanned_at=timezone.now(),
                )
                if not updated:
                    StocktakeItem.objects.create(
                        session=session,
                        asset=asset,
                        status="unexpected",
                        scanned_by=request.user,
                        scanned_at=timezone.now(),
                    )
                # V31: Show confirmation prompt instead of auto-transfer
                if asset.current_location != session.location:
                    old_location_name = (
                        asset.current_location.name
                        if asset.current_location
                        else "unknown"
                    )
                    # Show confirmation instead of auto-transfer
                    if request.headers.get("HX-Request"):
                        return render(
                            request,
                            "assets/partials/stocktake_transfer_confirm.html",
                            {
                                "asset": asset,
                                "session": session,
                                "old_location": old_location_name,
                                "new_location": session.location.name,
                            },
                        )
                    else:
                        messages.warning(
                            request,
                            f"'{asset.name}' is registered at "
                            f"'{old_location_name}' but scanned here at "
                            f"'{session.location.name}'. Use the transfer "
                            f"option to update its location.",
                        )
            except Asset.DoesNotExist:
                pass

        # Handle scanned code
        code = request.POST.get("code", "").strip()
        if code:
            found_asset = None
            try:
                found_asset = Asset.objects.get(barcode=code)
            except Asset.DoesNotExist:
                found_asset = NFCTag.get_asset_by_tag(code)

            if found_asset:
                session.confirmed_assets.add(found_asset)
                Transaction.objects.create(
                    asset=found_asset,
                    user=request.user,
                    action="audit",
                    from_location=found_asset.current_location,
                    to_location=session.location,
                    notes=f"Confirmed during stocktake #{session.pk}",
                )
                # G9: Update or create StocktakeItem
                updated = StocktakeItem.objects.filter(
                    session=session, asset=found_asset
                ).update(
                    status="confirmed",
                    scanned_by=request.user,
                    scanned_at=timezone.now(),
                )
                if not updated:
                    StocktakeItem.objects.create(
                        session=session,
                        asset=found_asset,
                        status="unexpected",
                        scanned_by=request.user,
                        scanned_at=timezone.now(),
                    )
                # V31: Show confirmation prompt instead of auto-transfer
                if found_asset.current_location != session.location:
                    old_location_name = (
                        found_asset.current_location.name
                        if found_asset.current_location
                        else "unknown"
                    )
                    # Show confirmation instead of auto-transfer
                    if request.headers.get("HX-Request"):
                        return render(
                            request,
                            "assets/partials/stocktake_transfer_confirm.html",
                            {
                                "asset": found_asset,
                                "session": session,
                                "old_location": old_location_name,
                                "new_location": session.location.name,
                            },
                        )
                    else:
                        messages.warning(
                            request,
                            f"'{found_asset.name}' is registered at "
                            f"'{old_location_name}' but scanned here at "
                            f"'{session.location.name}'. Use the transfer "
                            f"option to update its location.",
                        )
                        messages.success(
                            request, f"Confirmed: {found_asset.name}"
                        )
                else:
                    messages.success(request, f"Confirmed: {found_asset.name}")
            else:
                # V8: Unknown code — link to Quick Capture with code
                from urllib.parse import urlencode

                from django.urls import reverse

                qc_url = (
                    reverse("assets:quick_capture")
                    + "?"
                    + urlencode(
                        {
                            "code": code,
                            "location": session.location.pk,
                        }
                    )
                )
                messages.warning(
                    request,
                    f"Code '{code}' not found in the system. "
                    f'<a href="{qc_url}" class="underline">'
                    f"Quick Capture this item</a>.",
                    extra_tags="safe",
                )

    return redirect("assets:stocktake_detail", pk=pk)


@login_required
def stocktake_complete(request, pk):
    """Complete or abandon a stocktake session."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    session = get_object_or_404(StocktakeSession, pk=pk, status="in_progress")

    if request.method == "POST":
        action = request.POST.get("action", "complete")
        notes = request.POST.get("notes", "")

        session.ended_at = timezone.now()
        session.notes = notes

        if action == "abandon":
            session.status = "abandoned"
            messages.info(request, "Stocktake abandoned.")
        else:
            session.status = "completed"
            # Mark unconfirmed assets as missing
            mark_missing = request.POST.get("mark_missing") == "1"
            if mark_missing:
                missing = session.missing_assets.filter(
                    checked_out_to__isnull=True
                )
                missing_count = missing.update(status="missing")
                # M7: Update StocktakeItems and create Transactions
                missing_items = session.items.filter(status="expected")
                for item in missing_items.select_related("asset"):
                    Transaction.objects.create(
                        asset=item.asset,
                        user=request.user,
                        action="audit",
                        from_location=session.location,
                        to_location=session.location,
                        notes=(
                            f"Marked missing during "
                            f"stocktake #{session.pk}"
                        ),
                    )
                missing_items.update(status="missing")
                messages.success(
                    request,
                    f"Stocktake completed. {missing_count} asset(s) "
                    f"marked as missing.",
                )
            else:
                messages.success(request, "Stocktake completed.")

        session.save()
        return redirect("assets:stocktake_summary", pk=pk)

    return redirect("assets:stocktake_detail", pk=pk)


@login_required
def stocktake_summary(request, pk):
    """Display stocktake completion summary."""
    session = get_object_or_404(
        StocktakeSession.objects.select_related("location", "started_by"),
        pk=pk,
    )
    # Use StocktakeItem data when available, fall back to M2M
    items = session.items.all()
    if items.exists():
        total_expected = items.exclude(status="unexpected").count()
        confirmed_count = items.filter(status="confirmed").count()
        missing_qs = items.filter(status="missing").select_related("asset")
        missing_assets = Asset.objects.filter(
            pk__in=missing_qs.values_list("asset_id", flat=True)
        )
        missing_count = missing_qs.count()
        unexpected_qs = items.filter(status="unexpected").select_related(
            "asset"
        )
        unexpected_assets = Asset.objects.filter(
            pk__in=unexpected_qs.values_list("asset_id", flat=True)
        )
        unexpected_count = unexpected_qs.count()
    else:
        # Backwards compatibility: use M2M and dynamic property
        expected = session.expected_assets
        confirmed_ids = set(
            session.confirmed_assets.values_list("pk", flat=True)
        )
        missing_assets = expected.exclude(pk__in=confirmed_ids)
        unexpected_assets = session.unexpected_assets
        total_expected = expected.count()
        confirmed_count = len(confirmed_ids)
        missing_count = missing_assets.count()
        unexpected_count = unexpected_assets.count()

    return render(
        request,
        "assets/stocktake_summary.html",
        {
            "session": session,
            "total_expected": total_expected,
            "confirmed_count": confirmed_count,
            "missing_assets": missing_assets,
            "missing_count": missing_count,
            "unexpected_assets": unexpected_assets,
            "unexpected_count": unexpected_count,
        },
    )


# --- Lost & Stolen Report (V467) ---


@login_required
def lost_stolen_report(request):
    """V467: Dedicated report view for lost and stolen assets."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager", "member"):
        raise PermissionDenied

    assets = (
        Asset.objects.filter(status__in=["lost", "stolen"])
        .select_related(
            "category",
            "category__department",
            "current_location",
            "checked_out_to",
        )
        .order_by("status", "-updated_at")
    )
    return render(
        request,
        "assets/lost_stolen_report.html",
        {"assets": assets, "total": assets.count()},
    )


# --- Export ---


@login_required
def export_assets(request):
    """Export assets to Excel."""
    if not request.user.has_perm("assets.can_export_assets"):
        role = get_user_role(request.user)
        if role not in ("system_admin", "department_manager", "member"):
            return HttpResponseForbidden("Permission denied")
    from .services.export import export_assets_xlsx

    queryset = Asset.objects.with_related().select_related("created_by")

    # Exclude disposed by default unless explicitly included
    include_disposed = request.GET.get("include_disposed")
    if not include_disposed:
        queryset = queryset.exclude(status="disposed")

    # Apply same filters as asset_list
    status = request.GET.get("status")
    if status:
        queryset = queryset.filter(status=status)

    department = request.GET.get("department")
    if department:
        queryset = queryset.filter(category__department_id=department)

    category = request.GET.get("category")
    if category:
        queryset = queryset.filter(category_id=category)

    location = request.GET.get("location")
    if location:
        if location == "checked_out":
            queryset = queryset.filter(checked_out_to__isnull=False)
        else:
            queryset = queryset.filter(current_location_id=location)

    tag = request.GET.get("tag")
    if tag:
        queryset = queryset.filter(tags__id=tag)

    condition = request.GET.get("condition")
    if condition:
        queryset = queryset.filter(condition=condition)

    q = request.GET.get("q", "")
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q)
            | Q(description__icontains=q)
            | Q(barcode__icontains=q)
            | Q(tags__name__icontains=q)
        ).distinct()

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


# --- Bulk Operations ---


@login_required
def bulk_actions(request):
    """Handle bulk actions on selected assets."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    if request.method != "POST":
        return redirect("assets:asset_list")

    select_all_matching = request.POST.get("select_all_matching") == "1"

    if select_all_matching:
        # V271: Use shared builder instead of duplicating filter logic
        from .services.bulk import (
            build_asset_filter_queryset,
            validate_filter_params,
        )

        raw_filters = {
            "status": request.POST.get("filter_status", ""),
            "q": request.POST.get("filter_q", ""),
            "department": request.POST.get("filter_department", ""),
            "category": request.POST.get("filter_category", ""),
            "location": request.POST.get("filter_location", ""),
            "tag": request.POST.get("filter_tag", ""),
            "condition": request.POST.get("filter_condition", ""),
            "is_kit": request.POST.get("filter_is_kit", ""),
        }
        filters = validate_filter_params(raw_filters)
        queryset = build_asset_filter_queryset(filters)
        asset_ids = list(queryset.values_list("pk", flat=True))
        asset_ids = [str(i) for i in asset_ids]
    else:
        asset_ids = request.POST.getlist("asset_ids")

    if not asset_ids:
        messages.warning(request, "No assets selected.")
        return redirect("assets:asset_list")

    action = request.POST.get("bulk_action")

    if action == "transfer":
        location_id = request.POST.get("location")
        if not location_id:
            messages.error(request, "Please select a location for transfer.")
            return redirect("assets:asset_list")
        from .services.bulk import bulk_transfer

        result = bulk_transfer(
            [int(i) for i in asset_ids], int(location_id), request.user
        )
        count = result["transferred"]
        skipped = result["skipped"]
        messages.success(request, f"{count} asset(s) transferred.")
        if skipped:
            skipped_list = ", ".join(skipped)
            messages.warning(
                request,
                f"{len(skipped)} checked-out asset(s) were skipped: "
                f"{skipped_list}. Check them in before transferring.",
            )

    elif action == "status_change":
        new_status = request.POST.get("new_status")
        if not new_status:
            messages.error(request, "Please select a status.")
            return redirect("assets:asset_list")
        from .services.bulk import bulk_status_change

        count, failures = bulk_status_change(
            [int(i) for i in asset_ids], new_status, request.user
        )
        messages.success(request, f"{count} asset(s) updated.")
        if failures:
            fail_summary = "; ".join(failures[:5])
            if len(failures) > 5:
                fail_summary += f" and {len(failures) - 5} more"
            messages.warning(
                request,
                f"{len(failures)} asset(s) could not be updated: "
                f"{fail_summary}",
            )

    elif action == "bulk_edit":
        edit_category = request.POST.get("edit_category")
        edit_location = request.POST.get("edit_location")
        if not edit_category and not edit_location:
            messages.error(
                request,
                "Please select a category or location to assign.",
            )
            return redirect("assets:asset_list")
        from .services.bulk import bulk_edit

        count = bulk_edit(
            [int(i) for i in asset_ids],
            category_id=int(edit_category) if edit_category else None,
            location_id=int(edit_location) if edit_location else None,
        )
        messages.success(request, f"{count} asset(s) updated.")

    elif action == "bulk_checkout":
        borrower_id = request.POST.get("bulk_borrower")
        if not borrower_id:
            messages.error(request, "Please select a borrower for checkout.")
            return redirect("assets:asset_list")
        # Parse optional backdating
        action_date_str = request.POST.get("bulk_action_date", "").strip()
        bulk_timestamp = None
        if action_date_str:
            from django.utils.dateparse import parse_datetime

            action_date = parse_datetime(action_date_str)
            if action_date:
                if timezone.is_naive(action_date):
                    action_date = timezone.make_aware(action_date)
                if action_date <= timezone.now():
                    bulk_timestamp = action_date
        from .services.bulk import bulk_checkout

        result = bulk_checkout(
            [int(i) for i in asset_ids],
            int(borrower_id),
            request.user,
            notes="Bulk checkout",
            timestamp=bulk_timestamp,
        )
        messages.success(
            request, f"{result['checked_out']} asset(s) checked out."
        )
        if result["skipped"]:
            messages.warning(
                request,
                f"{len(result['skipped'])} asset(s) skipped "
                f"(already checked out): {', '.join(result['skipped'][:5])}",
            )

    elif action == "bulk_checkin":
        checkin_location_id = request.POST.get("bulk_checkin_location")
        if not checkin_location_id:
            messages.error(request, "Please select a location for check-in.")
            return redirect("assets:asset_list")
        action_date_str = request.POST.get("bulk_action_date", "").strip()
        bulk_timestamp = None
        if action_date_str:
            from django.utils.dateparse import parse_datetime

            action_date = parse_datetime(action_date_str)
            if action_date:
                if timezone.is_naive(action_date):
                    action_date = timezone.make_aware(action_date)
                if action_date <= timezone.now():
                    bulk_timestamp = action_date
        from .services.bulk import bulk_checkin

        result = bulk_checkin(
            [int(i) for i in asset_ids],
            int(checkin_location_id),
            request.user,
            notes="Bulk check-in",
            timestamp=bulk_timestamp,
        )
        messages.success(
            request, f"{result['checked_in']} asset(s) checked in."
        )
        if result["skipped"]:
            messages.warning(
                request,
                f"{len(result['skipped'])} asset(s) skipped "
                f"(not checked out): {', '.join(result['skipped'][:5])}",
            )

    elif action == "print_labels":
        assets = Asset.objects.filter(pk__in=asset_ids)
        # Generate QR codes for each asset
        label_assets = []
        for asset in assets:
            qr_data_uri = ""
            try:
                import base64
                from io import BytesIO

                import qrcode

                qr = qrcode.QRCode(version=1, box_size=4, border=1)
                qr.add_data(request.build_absolute_uri(f"/a/{asset.barcode}/"))
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="black", back_color="white")
                buffer = BytesIO()
                qr_img.save(buffer, format="PNG")
                buffer.seek(0)
                qr_data_uri = (
                    f"data:image/png;base64,"
                    f"{base64.b64encode(buffer.getvalue()).decode()}"
                )
            except ImportError:
                pass
            label_assets.append({"asset": asset, "qr_data_uri": qr_data_uri})
        return render(
            request,
            "assets/bulk_labels.html",
            {"label_assets": label_assets},
        )

    elif action == "print_labels_zpl":
        # V257: Zebra ZPL bulk label printing
        assets = list(Asset.objects.filter(pk__in=asset_ids))
        from .services.zebra import print_batch_labels

        success, count = print_batch_labels(assets)
        if success:
            messages.success(
                request,
                f"{count} label(s) sent to Zebra printer.",
            )
        else:
            messages.error(
                request,
                "Failed to send labels to Zebra printer. "
                "Check printer configuration.",
            )

    elif action == "remote_print":
        # Bulk remote print via connected print client
        remote_printer = request.POST.get("remote_printer", "")
        if ":" not in remote_printer:
            messages.error(request, "Please select a printer.")
            return redirect("assets:asset_list")

        client_pk, printer_id = remote_printer.split(":", 1)
        try:
            pc = PrintClient.objects.get(pk=client_pk)
        except (PrintClient.DoesNotExist, ValueError, TypeError):
            messages.error(request, "Print client not found.")
            return redirect("assets:asset_list")

        if pc.status != "approved" or not pc.is_active:
            messages.error(request, "Print client is not approved or active.")
            return redirect("assets:asset_list")

        if not pc.is_connected:
            messages.error(request, "Print client is no longer connected.")
            return redirect("assets:asset_list")

        # Validate printer_id exists on client
        printer_ids = {
            p.get("id") for p in (pc.printers or []) if isinstance(p, dict)
        }
        if printer_id not in printer_ids:
            messages.error(request, "Selected printer not found on client.")
            return redirect("assets:asset_list")

        assets = Asset.objects.filter(pk__in=asset_ids).select_related(
            "category__department"
        )
        base_url = request.build_absolute_uri("/").rstrip("/")
        channel_layer = get_channel_layer()
        group_name = f"print_client_active_{pc.pk}"

        sent = 0
        failed = 0
        for asset in assets:
            pr = PrintRequest.objects.create(
                asset=asset,
                print_client=pc,
                printer_id=printer_id,
                quantity=1,
                requested_by=request.user,
            )
            asset_name = (asset.name or "")[:30]
            barcode_val = asset.barcode or ""
            category_name = ""
            department_name = ""
            if asset.category:
                category_name = asset.category.name or ""
                if asset.category.department:
                    department_name = asset.category.department.name or ""
            qr_content = f"{base_url}/a/{barcode_val}/" if base_url else ""

            message = {
                "type": "print.job",
                "job_id": str(pr.job_id),
                "printer_id": printer_id,
                "barcode": barcode_val,
                "asset_name": asset_name,
                "category_name": category_name,
                "department_name": department_name,
                "qr_content": qr_content,
                "quantity": 1,
            }
            try:
                async_to_sync(channel_layer.group_send)(group_name, message)
                sent += 1
            except Exception:
                pr.transition_to("failed", error_message="Send failed")
                failed += 1

        # Remember last-used printer in session
        request.session["last_printer"] = remote_printer

        if sent:
            messages.success(
                request,
                f"{sent} label(s) sent to {pc.name}.",
            )
        if failed:
            messages.warning(
                request,
                f"{failed} label(s) failed to send.",
            )

    else:
        messages.error(request, "Unknown bulk action.")

    return redirect("assets:asset_list")


# --- Print All Filtered Labels ---


@login_required
def print_all_filtered_labels(request):
    """Generate printable labels for ALL assets matching current filters.

    Accepts the same filter parameters as asset_list (via GET).
    """
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied

    queryset = Asset.objects.with_related()

    # Apply the same filters as asset_list
    status = request.GET.get("status", "")
    if status:
        queryset = queryset.filter(status=status)

    q = request.GET.get("q", "")
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q)
            | Q(description__icontains=q)
            | Q(barcode__icontains=q)
            | Q(tags__name__icontains=q)
            | Q(
                nfc_tags__tag_id__icontains=q,
                nfc_tags__removed_at__isnull=True,
            )
        ).distinct()

    department = request.GET.get("department")
    if department:
        queryset = queryset.filter(category__department_id=department)

    category = request.GET.get("category")
    if category:
        queryset = queryset.filter(category_id=category)

    location = request.GET.get("location")
    if location:
        if location == "checked_out":
            queryset = queryset.filter(checked_out_to__isnull=False)
        else:
            queryset = queryset.filter(current_location_id=location)

    tag = request.GET.get("tag")
    if tag:
        queryset = queryset.filter(tags__id=tag)

    condition = request.GET.get("condition")
    if condition:
        queryset = queryset.filter(condition=condition)

    assets = queryset.order_by("name")

    # Generate QR codes for each asset
    label_assets = []
    for asset in assets:
        qr_data_uri = ""
        try:
            import base64
            from io import BytesIO

            import qrcode

            qr = qrcode.QRCode(version=1, box_size=4, border=1)
            qr.add_data(request.build_absolute_uri(f"/a/{asset.barcode}/"))
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            qr_img.save(buffer, format="PNG")
            buffer.seek(0)
            qr_data_uri = (
                f"data:image/png;base64,"
                f"{base64.b64encode(buffer.getvalue()).decode()}"
            )
        except ImportError:
            pass
        label_assets.append({"asset": asset, "qr_data_uri": qr_data_uri})

    return render(
        request,
        "assets/bulk_labels.html",
        {"label_assets": label_assets},
    )


# --- Asset Merge ---


@login_required
def asset_merge_select(request):
    """Select assets to merge."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    if request.method == "POST":
        asset_ids = request.POST.getlist("asset_ids")
        if len(asset_ids) < 2:
            messages.error(request, "Select at least 2 assets to merge.")
            return redirect("assets:asset_list")

        assets = Asset.objects.filter(pk__in=asset_ids)
        return render(
            request,
            "assets/asset_merge.html",
            {"assets": assets, "asset_ids": ",".join(asset_ids)},
        )

    return redirect("assets:asset_list")


@login_required
def asset_merge_execute(request):
    """Execute the merge."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    if request.method != "POST":
        return redirect("assets:asset_list")

    primary_id = request.POST.get("primary_id")
    asset_ids = request.POST.get("asset_ids", "").split(",")

    if not primary_id or len(asset_ids) < 2:
        messages.error(request, "Invalid merge request.")
        return redirect("assets:asset_list")

    try:
        primary = Asset.objects.get(pk=primary_id)
        duplicates = Asset.objects.filter(pk__in=asset_ids).exclude(
            pk=primary_id
        )
    except Asset.DoesNotExist:
        messages.error(request, "Primary asset not found.")
        return redirect("assets:asset_list")

    from .services.merge import merge_assets

    try:
        merge_assets(primary, list(duplicates), request.user)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("assets:asset_list")

    messages.success(
        request,
        f"Merged {duplicates.count()} asset(s) into "
        f"'{primary.name}' ({primary.barcode}).",
    )
    return redirect("assets:asset_detail", pk=primary.pk)


# --- AI Analysis ---


@login_required
def ai_analyse(request, pk, image_pk):
    """Trigger AI analysis for an asset image."""
    image = get_object_or_404(AssetImage, pk=image_pk, asset_id=pk)

    from props.context_processors import is_ai_analysis_enabled

    if not is_ai_analysis_enabled():
        messages.error(
            request, "AI analysis is not configured on this server."
        )
        return redirect("assets:asset_detail", pk=pk)

    from .tasks import analyse_image

    analyse_image.delay(image.pk)
    messages.info(
        request,
        "AI analysis queued for image. Results will appear shortly.",
    )
    return redirect("assets:asset_detail", pk=pk)


@login_required
def ai_apply_suggestions(request, pk, image_pk):
    """Apply AI suggestions to the asset."""
    image = get_object_or_404(AssetImage, pk=image_pk, asset_id=pk)
    asset = image.asset

    if request.method == "POST":
        # Apply selected suggestions
        if request.POST.get("apply_name") and image.ai_name_suggestion:
            asset.name = image.ai_name_suggestion

        if request.POST.get("apply_description") and image.ai_description:
            asset.description = image.ai_description

        if request.POST.get("append_description") and image.ai_description:
            if asset.description:
                asset.description = (
                    f"{asset.description}\n\n{image.ai_description}"
                )
            else:
                asset.description = image.ai_description

        if (
            request.POST.get("apply_condition")
            and image.ai_condition_suggestion
        ):
            if image.ai_condition_suggestion in dict(Asset.CONDITION_CHOICES):
                asset.condition = image.ai_condition_suggestion

        # Resolve department before category (needed for new category creation)
        resolved_dept = None
        if (
            request.POST.get("apply_department")
            and image.ai_department_suggestion
        ):
            try:
                resolved_dept = Department.objects.get(
                    name__iexact=image.ai_department_suggestion
                )
            except Department.DoesNotExist:
                pass

        if (
            request.POST.get("create_apply_department")
            and image.ai_department_suggestion
        ):
            resolved_dept, created = Department.objects.get_or_create(
                name__iexact=image.ai_department_suggestion,
                defaults={"name": image.ai_department_suggestion},
            )
            if created:
                messages.info(
                    request,
                    f'New department "{resolved_dept.name}" created '
                    f"from AI suggestion.",
                )

        if request.POST.get("apply_category") and image.ai_category_suggestion:
            try:
                cat = Category.objects.get(
                    name__iexact=image.ai_category_suggestion
                )
                asset.category = cat
            except Category.DoesNotExist:
                messages.warning(
                    request,
                    f"Category '{image.ai_category_suggestion}' "
                    f"not found. Could not apply category suggestion.",
                )

        if (
            request.POST.get("create_apply_category")
            and image.ai_category_suggestion
        ):
            # Use resolved department, fall back to asset's current department
            dept = resolved_dept or asset.department
            if dept is None:
                messages.warning(
                    request,
                    "Cannot create category: no department selected. "
                    "Apply a department first.",
                )
            else:
                cat, created = Category.objects.get_or_create(
                    name__iexact=image.ai_category_suggestion,
                    defaults={
                        "name": image.ai_category_suggestion,
                        "department": dept,
                    },
                )
                asset.category = cat
                if created:
                    messages.info(
                        request,
                        f'New category "{cat.name}" created'
                        f" from AI suggestion.",
                    )

        if request.POST.get("apply_tags") and image.ai_tag_suggestions:
            for tag_name in image.ai_tag_suggestions:
                tag, _created = Tag.objects.get_or_create(
                    name__iexact=tag_name.strip(),
                    defaults={"name": tag_name.strip()},
                )
                asset.tags.add(tag)

        if request.POST.get("copy_ocr_to_notes") and image.ai_ocr_text:
            if asset.notes:
                asset.notes = f"{asset.notes}\n\n{image.ai_ocr_text}"
            else:
                asset.notes = image.ai_ocr_text

        asset.save()

        # Track that suggestions were applied (L36)
        image.ai_suggestions_applied = True
        image.save(update_fields=["ai_suggestions_applied"])

        messages.success(request, "AI suggestions applied.")

    return redirect("assets:asset_detail", pk=pk)


@login_required
def ai_status(request, pk, image_pk):
    """Return AI analysis status for HTMX polling."""
    image = get_object_or_404(AssetImage, pk=image_pk, asset_id=pk)
    if image.ai_processing_status in ("completed", "failed"):
        # Return full result panel - redirect to asset detail
        return redirect("assets:asset_detail", pk=pk)
    # Still processing - return polling div
    return HttpResponse(
        f'<div hx-get="{request.path}" hx-trigger="every 5s" '
        f'hx-swap="outerHTML" class="bg-purple-500/5 rounded-xl '
        f'border border-purple-500/20 p-4 flex items-center gap-3">'
        f'<div class="w-5 h-5 border-2 border-purple-500/30 '
        f'border-t-purple-500 rounded-full animate-spin"></div>'
        f'<span class="text-purple-300 text-sm">'
        f"AI analysis in progress...</span>"
        f"</div>"
    )


@login_required
def ai_reanalyse(request, pk, image_pk):
    """Retry AI analysis for a failed image."""
    image = get_object_or_404(AssetImage, pk=image_pk, asset_id=pk)

    from props.context_processors import is_ai_analysis_enabled

    if not is_ai_analysis_enabled():
        messages.error(
            request, "AI analysis is not configured on this server."
        )
        return redirect("assets:asset_detail", pk=pk)

    from .tasks import reanalyse_image

    reanalyse_image.delay(image.pk)
    messages.info(request, "AI re-analysis queued.")
    return redirect("assets:asset_detail", pk=pk)


# --- Handover ---


@login_required
def asset_handover(request, pk):
    """Hand over a checked-out asset to a new borrower."""
    asset = get_object_or_404(Asset, pk=pk)
    if not can_handover_asset(request.user, asset):
        raise PermissionDenied

    if not asset.is_checked_out:
        messages.error(
            request,
            "This asset is not checked out. Handover requires a checked-out "
            "asset.",
        )
        return redirect("assets:asset_detail", pk=pk)

    # S7.20.4: Block handover on lost/stolen assets
    if asset.status in ("lost", "stolen"):
        messages.error(
            request,
            f"Cannot hand over a {asset.status} asset. "
            "Recover the asset first.",
        )
        return redirect("assets:asset_detail", pk=pk)

    from django.contrib.auth import get_user_model

    User = get_user_model()

    if request.method == "POST":
        borrower_id = request.POST.get("borrower")
        notes = request.POST.get("notes", "")
        location_id = request.POST.get("location", "").strip()

        try:
            new_borrower = User.objects.get(pk=borrower_id)
        except User.DoesNotExist:
            messages.error(request, "Invalid borrower selected.")
            return redirect("assets:asset_handover", pk=pk)

        # S7.20.2: Reject handover to the same borrower
        if (
            asset.checked_out_to_id
            and asset.checked_out_to_id == new_borrower.pk
        ):
            messages.error(
                request,
                "Asset is already checked out to this person.",
            )
            return redirect("assets:asset_detail", pk=pk)

        to_location = None
        if location_id:
            try:
                to_location = Location.objects.get(
                    pk=location_id, is_active=True
                )
            except Location.DoesNotExist:
                pass

        # Parse optional backdating
        action_date_str = request.POST.get("action_date", "").strip()
        timestamp = None
        if action_date_str:
            from django.utils.dateparse import parse_datetime

            action_date = parse_datetime(action_date_str)
            if action_date:
                if timezone.is_naive(action_date):
                    action_date = timezone.make_aware(action_date)
                # S7.21.1: Reject future dates
                if action_date > timezone.now():
                    messages.error(
                        request,
                        "Date cannot be in the future.",
                    )
                    return redirect("assets:asset_handover", pk=pk)
                timestamp = action_date

        from .services.transactions import create_handover

        try:
            create_handover(
                asset=asset,
                new_borrower=new_borrower,
                performed_by=request.user,
                to_location=to_location,
                notes=notes,
                timestamp=timestamp,
            )
        except (ValueError, ValidationError) as exc:
            messages.error(request, str(exc))
            return redirect("assets:asset_detail", pk=pk)

        messages.success(
            request,
            f"'{asset.name}' handed over to "
            f"{new_borrower.get_display_name()}.",
        )
        return redirect("assets:asset_detail", pk=pk)

    from django.contrib.auth.models import Group
    from django.db.models import Q as HQ

    borrower_roles = [
        "Borrower",
        "Member",
        "Department Manager",
        "System Admin",
    ]
    borrower_group = Group.objects.filter(name="Borrower").first()
    users = (
        User.objects.filter(
            HQ(is_active=True, groups__name__in=borrower_roles)
            | HQ(is_superuser=True, is_active=True)
        )
        .distinct()
        .order_by("username")
    )
    locations = Location.objects.filter(is_active=True)
    return render(
        request,
        "assets/asset_handover.html",
        {
            "asset": asset,
            "users": users,
            "locations": locations,
            "borrower_group_id": borrower_group.pk if borrower_group else None,
        },
    )


# --- Serialisation Conversion ---


@login_required
def asset_convert_serialisation(request, pk):
    """Show conversion impact and handle confirmation."""
    asset = get_object_or_404(Asset, pk=pk)
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied

    if request.method == "POST":
        confirm = request.POST.get("confirm")
        if not confirm:
            messages.error(request, "You must confirm the conversion.")
            return redirect("assets:asset_detail", pk=pk)

        if asset.is_serialised:
            override = request.POST.get("override_checkout") == "1"
            adjusted_qty = request.POST.get("adjusted_quantity")
            qty = int(adjusted_qty) if adjusted_qty else None
            try:
                from assets.services.serial import (
                    apply_convert_to_non_serialised,
                )

                apply_convert_to_non_serialised(
                    asset,
                    request.user,
                    adjusted_quantity=qty,
                    override_checkout=override,
                )
                messages.success(
                    request,
                    f"'{asset.name}' converted to non-serialised. "
                    f"Serials archived.",
                )
            except ValidationError as e:
                messages.error(request, str(e.message))
        else:
            try:
                from assets.services.serial import (
                    apply_convert_to_serialised,
                    restore_archived_serials,
                )

                apply_convert_to_serialised(asset, request.user)

                restore = request.POST.get("restore_serials") == "1"
                if restore:
                    result = restore_archived_serials(asset, request.user)
                    if result["conflicts"]:
                        messages.warning(
                            request,
                            f"Restored {result['restored']} serial(s)"
                            f". {len(result['conflicts'])} barcode "
                            f"conflict(s) were cleared.",
                        )
                    elif result["restored"]:
                        messages.success(
                            request,
                            f"Restored {result['restored']} archived"
                            f" serial(s).",
                        )

                messages.success(
                    request,
                    f"'{asset.name}' converted to serialised.",
                )
            except ValidationError as e:
                messages.error(request, str(e.message))

        return redirect("assets:asset_detail", pk=pk)

    # GET: show impact summary
    if asset.is_serialised:
        from assets.services.serial import (
            convert_to_non_serialised,
        )

        impact = convert_to_non_serialised(asset, request.user)
        converting_to = "non_serialised"
    else:
        from assets.services.serial import (
            convert_to_serialised,
            get_archived_serials,
        )

        impact = convert_to_serialised(asset, request.user)
        archived = get_archived_serials(asset)
        impact["archived_serials"] = archived.count()
        converting_to = "serialised"

    return render(
        request,
        "assets/convert_serialisation.html",
        {
            "asset": asset,
            "impact": impact,
            "converting_to": converting_to,
        },
    )


# --- Hold Lists ---


@login_required
def holdlist_list(request):
    """List all hold lists."""
    from assets.models import HoldList, HoldListStatus

    status_filter = request.GET.get("status")
    project_filter = request.GET.get("project")
    qs = HoldList.objects.select_related(
        "project", "department", "status", "created_by"
    )
    if status_filter:
        qs = qs.filter(status__name=status_filter)
    if project_filter:
        qs = qs.filter(project_id=project_filter)
    statuses = HoldListStatus.objects.all()

    # L24: Pagination
    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "assets/holdlist_list.html",
        {
            "hold_lists": page_obj,
            "page_obj": page_obj,
            "statuses": statuses,
            "current_status": status_filter,
        },
    )


@login_required
def holdlist_detail(request, pk):
    """Show hold list with items."""
    from collections import OrderedDict

    from assets.models import HoldList

    hold_list = get_object_or_404(
        HoldList.objects.select_related("project", "department", "status"),
        pk=pk,
    )
    items = hold_list.items.select_related(
        "asset", "asset__current_location", "serial", "pulled_by"
    )
    from assets.services.holdlists import detect_overlaps, get_effective_dates

    overlaps = detect_overlaps(hold_list)
    effective_start, effective_end = get_effective_dates(hold_list)

    # Group items by location for pull view
    items_by_location = OrderedDict()
    for item in items:
        loc_name = (
            item.asset.current_location.name
            if item.asset.current_location
            else "Unknown Location"
        )
        if loc_name not in items_by_location:
            items_by_location[loc_name] = []
        items_by_location[loc_name].append(item)

    return render(
        request,
        "assets/holdlist_detail.html",
        {
            "hold_list": hold_list,
            "items": items,
            "overlaps": overlaps,
            "effective_start": effective_start,
            "effective_end": effective_end,
            "items_by_location": items_by_location,
        },
    )


@login_required
def holdlist_create(request):
    """Create a new hold list."""
    from assets.models import Department, HoldListStatus, Project

    if request.method == "POST":
        from assets.services.holdlists import create_hold_list

        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Name is required.")
            return redirect("assets:holdlist_create")
        kwargs = {}
        project_id = request.POST.get("project")
        if project_id:
            kwargs["project_id"] = project_id
        dept_id = request.POST.get("department")
        if dept_id:
            kwargs["department_id"] = dept_id
        status_id = request.POST.get("status")
        if status_id:
            kwargs["status"] = HoldListStatus.objects.get(pk=status_id)
        start = request.POST.get("start_date")
        end = request.POST.get("end_date")
        if start:
            kwargs["start_date"] = start
        if end:
            kwargs["end_date"] = end
        kwargs["notes"] = request.POST.get("notes", "")
        try:
            hold_list = create_hold_list(name, request.user, **kwargs)
            messages.success(request, f"Hold list '{name}' created.")
            return redirect("assets:holdlist_detail", pk=hold_list.pk)
        except Exception as e:
            messages.error(request, str(e))

    projects = Project.objects.filter(is_active=True)
    departments = Department.objects.filter(is_active=True)
    statuses = HoldListStatus.objects.all()
    return render(
        request,
        "assets/holdlist_form.html",
        {
            "projects": projects,
            "departments": departments,
            "statuses": statuses,
            "editing": False,
        },
    )


@login_required
def holdlist_edit(request, pk):
    """Edit an existing hold list."""
    from assets.models import Department, HoldList, HoldListStatus, Project

    hold_list = get_object_or_404(HoldList, pk=pk)
    role = get_user_role(request.user)
    if hold_list.created_by != request.user and role not in (
        "system_admin",
        "department_manager",
    ):
        raise PermissionDenied

    # Locked hold lists can only be edited by managers/admins
    if hold_list.is_locked and role not in (
        "system_admin",
        "department_manager",
    ):
        messages.error(
            request,
            "This hold list is locked and cannot be edited.",
        )
        if request.method == "POST":
            return redirect("assets:holdlist_detail", pk=pk)
        return redirect("assets:holdlist_detail", pk=pk)

    if request.method == "POST":
        hold_list.name = request.POST.get("name", hold_list.name)
        project_id = request.POST.get("project")
        hold_list.project_id = project_id if project_id else None
        dept_id = request.POST.get("department")
        hold_list.department_id = dept_id if dept_id else None
        status_id = request.POST.get("status")
        if status_id:
            hold_list.status_id = status_id
        hold_list.start_date = request.POST.get("start_date") or None
        hold_list.end_date = request.POST.get("end_date") or None
        hold_list.notes = request.POST.get("notes", "")
        try:
            hold_list.full_clean()
            hold_list.save()
            messages.success(request, "Hold list updated.")
            return redirect("assets:holdlist_detail", pk=pk)
        except Exception as e:
            messages.error(request, str(e))

    projects = Project.objects.filter(is_active=True)
    departments = Department.objects.filter(is_active=True)
    statuses = HoldListStatus.objects.all()
    return render(
        request,
        "assets/holdlist_form.html",
        {
            "hold_list": hold_list,
            "projects": projects,
            "departments": departments,
            "statuses": statuses,
            "editing": True,
        },
    )


@login_required
def holdlist_add_item(request, pk):
    """Add an item to a hold list."""
    from assets.models import HoldList, HoldListItem

    hold_list = get_object_or_404(HoldList, pk=pk)
    if request.method == "POST":
        asset_id = request.POST.get("asset_id")
        if asset_id:
            from assets.services.holdlists import add_item

            asset = get_object_or_404(Asset, pk=asset_id)
            qty = int(request.POST.get("quantity", 1))
            notes = request.POST.get("notes", "")
            override_overlap = request.POST.get("override_overlap")
            try:
                add_item(
                    hold_list,
                    asset,
                    request.user,
                    quantity=qty,
                    notes=notes,
                )
                messages.success(
                    request, f"Added '{asset.name}' to hold list."
                )
                # Check for overlapping holds and warn
                if hold_list.start_date and hold_list.end_date:
                    overlapping = (
                        HoldListItem.objects.filter(
                            asset=asset,
                            hold_list__start_date__lte=(hold_list.end_date),
                            hold_list__end_date__gte=(hold_list.start_date),
                        )
                        .exclude(hold_list=hold_list)
                        .exclude(hold_list__status__is_terminal=True)
                        .select_related("hold_list")
                    )
                    if overlapping.exists():
                        if override_overlap:
                            messages.warning(
                                request,
                                f"Overlap override acknowledged: "
                                f"'{asset.name}' is also on other "
                                f"hold lists.",
                            )
                        else:
                            for oi in overlapping:
                                hl = oi.hold_list
                                messages.warning(
                                    request,
                                    f"Overlap: '{asset.name}' is "
                                    f'also on "{hl.name}" '
                                    f"({hl.start_date} - "
                                    f"{hl.end_date}).",
                                )
            except Exception as e:
                messages.error(request, str(e))
    return redirect("assets:holdlist_detail", pk=pk)


@login_required
def holdlist_remove_item(request, pk, item_pk):
    """Remove an item from a hold list."""
    from assets.models import HoldList

    hold_list = get_object_or_404(HoldList, pk=pk)
    if request.method == "POST":
        from assets.services.holdlists import remove_item

        try:
            remove_item(hold_list, item_pk, request.user)
            messages.success(request, "Item removed.")
        except Exception as e:
            messages.error(request, str(e))
    return redirect("assets:holdlist_detail", pk=pk)


@login_required
def holdlist_edit_item(request, pk, item_pk):
    """V459: Edit an existing hold list item's quantity and notes."""
    from assets.models import HoldList, HoldListItem

    hold_list = get_object_or_404(HoldList, pk=pk)
    item = get_object_or_404(HoldListItem, pk=item_pk, hold_list=hold_list)
    if request.method == "POST":
        quantity = request.POST.get("quantity")
        notes = request.POST.get("notes", "")
        if quantity:
            try:
                item.quantity = max(1, int(quantity))
            except (ValueError, TypeError):
                pass
        item.notes = notes
        item.save()
        messages.success(request, "Item updated.")
    return redirect("assets:holdlist_detail", pk=pk)


@login_required
def holdlist_pick_sheet(request, pk):
    """Download pick sheet PDF for a hold list."""
    from assets.models import HoldList
    from assets.services.pdf import generate_pick_sheet_pdf

    hold_list = get_object_or_404(
        HoldList.objects.select_related("project", "department", "status"),
        pk=pk,
    )
    items = hold_list.items.select_related(
        "asset", "asset__category", "asset__current_location"
    )
    pdf_bytes = generate_pick_sheet_pdf(
        hold_list, items, generated_by=request.user
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="pick-sheet-{hold_list.pk}.pdf"'
    )
    return response


@login_required
def holdlist_update_pull_status(request, pk, item_pk):
    """Update pull status of a hold list item."""
    from assets.models import HoldList, HoldListItem

    hold_list = get_object_or_404(HoldList, pk=pk)
    item = get_object_or_404(HoldListItem, pk=item_pk, hold_list=hold_list)
    if request.method == "POST":
        new_status = request.POST.get("pull_status", "")
        if new_status in ("pending", "pulled", "unavailable"):
            if new_status == "pulled":
                from assets.services.holdlists import fulfil_item

                fulfil_item(item, request.user)
            else:
                from assets.services.holdlists import (
                    update_pull_status,
                )

                update_pull_status(item, new_status, request.user)
            messages.success(
                request,
                f"Item '{item.asset.name}' marked as {new_status}.",
            )
        else:
            messages.error(request, "Invalid pull status.")
    return redirect("assets:holdlist_detail", pk=pk)


@login_required
def project_list(request):
    """List all projects."""
    from assets.models import Project

    projects = Project.objects.select_related("created_by").all()
    return render(request, "assets/project_list.html", {"projects": projects})


@login_required
def project_create(request):
    """Create a project."""
    from assets.models import Project

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Name is required.")
            return redirect("assets:project_create")
        Project.objects.create(
            name=name,
            description=request.POST.get("description", ""),
            created_by=request.user,
        )
        messages.success(request, f"Project '{name}' created.")
        return redirect("assets:project_list")
    return render(request, "assets/project_form.html", {"editing": False})


@login_required
def project_edit(request, pk):
    """Edit a project."""
    from assets.models import Project

    project = get_object_or_404(Project, pk=pk)
    if request.method == "POST":
        project.name = request.POST.get("name", project.name)
        project.description = request.POST.get("description", "")
        project.is_active = request.POST.get("is_active") == "1"
        project.save()
        messages.success(request, "Project updated.")
        return redirect("assets:project_list")
    return render(
        request,
        "assets/project_form.html",
        {
            "project": project,
            "editing": True,
        },
    )


@login_required
def project_detail(request, pk):
    """Show project detail with associated hold lists."""
    from assets.models import HoldList, Project

    project = get_object_or_404(Project, pk=pk)
    hold_lists = HoldList.objects.filter(project=project).select_related(
        "department", "status"
    )
    return render(
        request,
        "assets/project_detail.html",
        {
            "project": project,
            "hold_lists": hold_lists,
        },
    )


@login_required
def project_delete(request, pk):
    """Delete a project. Only creator, dept manager, or admin."""
    from assets.models import Project

    project = get_object_or_404(Project, pk=pk)
    role = get_user_role(request.user)
    if project.created_by != request.user and role not in (
        "system_admin",
        "department_manager",
    ):
        raise PermissionDenied

    if request.method == "POST":
        project.delete()
        messages.success(request, f"Project '{project.name}' deleted.")
        return redirect("assets:project_list")
    return redirect("assets:project_list")


@login_required
def holdlist_delete(request, pk):
    """Delete a hold list."""
    from assets.models import HoldList

    hold_list = get_object_or_404(HoldList, pk=pk)
    role = get_user_role(request.user)
    if hold_list.created_by != request.user and role not in (
        "system_admin",
        "department_manager",
    ):
        raise PermissionDenied

    if request.method == "POST":
        hold_list.delete()
        messages.success(request, f"Hold list '{hold_list.name}' deleted.")
        return redirect("assets:holdlist_list")
    return redirect("assets:holdlist_list")


@login_required
def holdlist_lock(request, pk):
    """Lock a hold list."""
    from assets.models import HoldList
    from assets.services.holdlists import lock_hold_list

    hold_list = get_object_or_404(HoldList, pk=pk)
    if request.method == "POST":
        lock_hold_list(hold_list, request.user)
        messages.success(request, "Hold list locked.")
    return redirect("assets:holdlist_detail", pk=pk)


@login_required
def holdlist_unlock(request, pk):
    """Unlock a hold list."""
    from assets.models import HoldList
    from assets.services.holdlists import unlock_hold_list

    hold_list = get_object_or_404(HoldList, pk=pk)
    if request.method == "POST":
        unlock_hold_list(hold_list, request.user)
        messages.success(request, "Hold list unlocked.")
    return redirect("assets:holdlist_detail", pk=pk)


@login_required
def holdlist_fulfil(request, pk):
    """Fulfil/bulk checkout a hold list's items."""
    from django.contrib.auth import get_user_model

    from assets.models import HoldList
    from assets.services.transactions import create_checkout

    User = get_user_model()

    hold_list = get_object_or_404(
        HoldList.objects.select_related("project", "department", "status"),
        pk=pk,
    )
    items = hold_list.items.select_related(
        "asset", "asset__current_location", "serial", "pulled_by"
    )

    # V449: POST handler for bulk fulfil/checkout
    if request.method == "POST":
        borrower_id = request.POST.get("borrower")
        try:
            borrower = User.objects.get(pk=borrower_id)
        except (User.DoesNotExist, ValueError, TypeError):
            messages.error(request, "Invalid borrower selected.")
            return redirect("assets:holdlist_fulfil", pk=pk)

        fulfilled = 0
        for item in items:
            asset = item.asset
            if asset.available_count > 0:
                try:
                    create_checkout(
                        asset=asset,
                        borrower=borrower,
                        performed_by=request.user,
                        notes=f"Fulfilled from hold list: {hold_list.name}",
                    )
                    item.pull_status = "pulled"
                    item.pulled_by = request.user
                    item.pulled_at = timezone.now()
                    item.save()
                    fulfilled += 1
                except Exception:
                    pass
        messages.success(
            request,
            f"Fulfilled {fulfilled} item(s) to {borrower.get_display_name}.",
        )
        return redirect("assets:holdlist_detail", pk=pk)

    return render(
        request,
        "assets/holdlist_fulfil.html",
        {
            "hold_list": hold_list,
            "items": items,
        },
    )


# --- Kit Management ---


@login_required
def kit_contents(request, pk):
    """View kit contents (components)."""
    asset = get_object_or_404(Asset, pk=pk)
    if not asset.is_kit:
        messages.error(request, "This asset is not a kit.")
        return redirect("assets:asset_detail", pk=pk)
    components = AssetKit.objects.filter(kit=asset).select_related(
        "component", "serial"
    )
    role = get_user_role(request.user)
    can_manage = role in ("system_admin", "department_manager")
    return render(
        request,
        "assets/kit_contents.html",
        {
            "asset": asset,
            "components": components,
            "can_manage": can_manage,
        },
    )


@login_required
def kit_add_component(request, pk):
    """Add a component to a kit."""
    asset = get_object_or_404(Asset, pk=pk)
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    if not asset.is_kit:
        messages.error(request, "This asset is not a kit.")
        return redirect("assets:asset_detail", pk=pk)

    if request.method == "POST":
        component_id = request.POST.get("component_id")
        if component_id:
            component = get_object_or_404(Asset, pk=component_id)
            is_required = request.POST.get("is_required") == "1"
            qty = int(request.POST.get("quantity", 1))
            try:
                ak = AssetKit(
                    kit=asset,
                    component=component,
                    is_required=is_required,
                    quantity=qty,
                )
                ak.full_clean()
                ak.save()
                messages.success(
                    request,
                    f"Added '{component.name}' to kit.",
                )
            except Exception as e:
                messages.error(request, str(e))
    return redirect("assets:kit_contents", pk=pk)


@login_required
def kit_remove_component(request, pk, component_pk):
    """Remove a component from a kit."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied
    if request.method == "POST":
        AssetKit.objects.filter(kit_id=pk, pk=component_pk).delete()
        messages.success(request, "Component removed from kit.")
    return redirect("assets:kit_contents", pk=pk)


# --- Print Job Status & History ---


@login_required
def print_job_status(request, pk, job_id):
    """V92: HTMX polling endpoint for print job status."""
    pr = get_object_or_404(PrintRequest, asset_id=pk, job_id=job_id)
    data = {
        "status": pr.status,
        "error": pr.error_message or "",
    }
    return JsonResponse(data)


@login_required
def print_history(request, pk):
    """V96: Print history for an asset."""
    asset = get_object_or_404(Asset, pk=pk)
    requests = PrintRequest.objects.filter(asset=asset).select_related(
        "print_client", "requested_by"
    )[:50]
    return render(
        request,
        "assets/print_history.html",
        {"asset": asset, "print_requests": requests},
    )

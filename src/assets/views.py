"""Views for the assets app."""

import json
import re

from django_ratelimit.decorators import ratelimit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
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
    AssetSerial,
    Category,
    Department,
    Location,
    NFCTag,
    StocktakeSession,
    Tag,
    Transaction,
)
from .services.permissions import (
    can_checkout_asset,
    can_delete_asset,
    can_edit_asset,
    can_handover_asset,
    get_user_role,
)

BARCODE_PATTERN = re.compile(r"^[A-Z]+-[A-Z0-9]+$")


# --- Dashboard ---


@login_required
def dashboard(request):
    """Display the dashboard with summary metrics."""
    total_active = Asset.objects.filter(status="active").count()
    total_draft = Asset.objects.filter(status="draft").count()
    total_checked_out = Asset.objects.filter(
        checked_out_to__isnull=False
    ).count()
    total_missing = Asset.objects.filter(status="missing").count()

    recent_transactions = Transaction.objects.select_related(
        "asset", "user", "borrower", "from_location", "to_location"
    )[:10]

    recent_drafts = Asset.objects.filter(status="draft").select_related(
        "category", "created_by"
    )[:5]

    checked_out_assets = (
        Asset.objects.filter(checked_out_to__isnull=False)
        .select_related("checked_out_to", "category", "current_location")
        .prefetch_related("transactions")[:10]
    )

    # Per-department counts
    dept_counts = (
        Department.objects.filter(is_active=True)
        .annotate(
            asset_count=Count(
                "categories__assets",
                filter=Q(categories__assets__status="active"),
            )
        )
        .order_by("-asset_count")[:10]
    )

    # Per-category counts
    cat_counts = Category.objects.annotate(
        asset_count=Count("assets", filter=Q(assets__status="active"))
    ).order_by("-asset_count")[:10]

    # Per-location counts
    loc_counts = (
        Location.objects.filter(is_active=True)
        .annotate(
            asset_count=Count("assets", filter=Q(assets__status="active"))
        )
        .order_by("-asset_count")[:10]
    )

    # Top 10 tags
    top_tags = Tag.objects.annotate(asset_count=Count("assets")).order_by(
        "-asset_count"
    )[:10]

    # Pending approvals count for admins (S2.15.4-09)
    pending_approvals_count = 0
    if get_user_role(request.user) == "system_admin":
        from accounts.models import CustomUser as User

        pending_approvals_count = User.objects.filter(
            is_active=False,
            email_verified=True,
            rejection_reason="",
        ).count()

    return render(
        request,
        "assets/dashboard.html",
        {
            "total_active": total_active,
            "total_draft": total_draft,
            "total_checked_out": total_checked_out,
            "total_missing": total_missing,
            "recent_transactions": recent_transactions,
            "recent_drafts": recent_drafts,
            "checked_out_assets": checked_out_assets,
            "dept_counts": dept_counts,
            "cat_counts": cat_counts,
            "loc_counts": loc_counts,
            "top_tags": top_tags,
            "pending_approvals_count": pending_approvals_count,
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
    }

    response = render(request, "assets/asset_list.html", context)
    if view_mode in ("list", "grid"):
        response.set_cookie("view_mode", view_mode, max_age=365 * 24 * 3600)
    return response


# --- Asset Detail ---


@login_required
def asset_detail(request, pk):
    """Display asset detail view."""
    asset = get_object_or_404(
        Asset.objects.with_related().select_related("created_by"),
        pk=pk,
    )
    transactions = asset.transactions.select_related(
        "user", "borrower", "from_location", "to_location"
    )
    images = asset.images.all()
    active_nfc = asset.nfc_tags.filter(removed_at__isnull=True)
    removed_nfc = asset.nfc_tags.filter(removed_at__isnull=False)

    can_edit = can_edit_asset(request.user, asset)
    can_delete = can_delete_asset(request.user, asset)
    can_checkout = can_checkout_asset(request.user, asset)
    can_handover = can_handover_asset(request.user, asset)

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
    if request.method == "POST":
        form = AssetForm(request.POST, request.FILES, instance=asset)
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
            if request.htmx:
                return render(
                    request,
                    "assets/partials/capture_success.html",
                    {"asset": asset},
                )

            messages.success(
                request,
                f"Draft asset '{asset.name}' created ({asset.barcode}).",
            )
            return render(
                request,
                "assets/quick_capture.html",
                {
                    "form": QuickCaptureForm(),
                    "just_created": asset,
                },
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


# --- Scan & Lookup ---


@login_required
def scan_view(request):
    """Barcode/NFC scanning interface."""
    return render(request, "assets/scan.html")


@login_required
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
        return JsonResponse(
            {
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
        )
    except AssetSerial.DoesNotExist:
        pass

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
        return redirect(f"{parent.get_absolute_url()}?serial={serial.pk}")
    except AssetSerial.DoesNotExist:
        pass

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
    MAX_IMAGE_SIZE = 25 * 1024 * 1024  # 25 MB (§S7.8.1)
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

    if asset.is_checked_out:
        messages.error(
            request,
            f"This asset is already checked out to "
            f"{asset.checked_out_to.get_display_name()}.",
        )
        return redirect("assets:asset_detail", pk=pk)

    if asset.status not in ("active", "draft"):
        messages.error(
            request, "Only active or draft assets can be checked out."
        )
        return redirect("assets:asset_detail", pk=pk)

    from django.contrib.auth import get_user_model

    User = get_user_model()

    if request.method == "POST":
        borrower_id = request.POST.get("borrower")
        notes = request.POST.get("notes", "")
        destination_id = request.POST.get("destination_location", "")

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
                if action_date <= timezone.now():
                    extra_kwargs = {
                        "timestamp": action_date,
                        "is_backdated": True,
                    }

        # Re-fetch with select_for_update to prevent concurrent checkout
        from django.db import transaction as db_transaction

        with db_transaction.atomic():
            locked_asset = Asset.objects.select_for_update().get(pk=pk)
            if locked_asset.is_checked_out:
                messages.error(
                    request,
                    "This asset was just checked out by another user.",
                )
                return redirect("assets:asset_detail", pk=pk)

            if not locked_asset.home_location:
                locked_asset.home_location = locked_asset.current_location

            tx_kwargs = {
                "asset": locked_asset,
                "user": request.user,
                "action": "checkout",
                "from_location": locked_asset.current_location,
                "borrower": borrower,
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

    borrower_group = Group.objects.filter(name="Borrower").first()
    users = User.objects.filter(is_active=True).order_by("username")
    return render(
        request,
        "assets/asset_checkout.html",
        {
            "asset": asset,
            "users": users,
            "borrower_group_id": borrower_group.pk if borrower_group else None,
        },
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
                if action_date <= timezone.now():
                    extra_kwargs = {
                        "timestamp": action_date,
                        "is_backdated": True,
                    }

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
        asset.save(update_fields=["checked_out_to", "current_location"])

        messages.success(
            request,
            f"'{asset.name}' checked in to {to_location.name}.",
        )
        return redirect("assets:asset_detail", pk=pk)

    locations = Location.objects.filter(is_active=True)
    return render(
        request,
        "assets/asset_checkin.html",
        {
            "asset": asset,
            "locations": locations,
            "home_location": asset.home_location,
        },
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
                if action_date <= timezone.now():
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
            messages.error(request, "Invalid location selected.")
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
        asset.current_location = to_location
        asset.save(update_fields=["current_location"])

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
    """List all categories."""
    categories = (
        Category.objects.select_related("department")
        .annotate(asset_count=Count("assets"))
        .order_by("department__name", "name")
    )
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
    """Display location detail with assets."""
    location = get_object_or_404(Location, pk=pk)
    assets = Asset.objects.filter(current_location=location).select_related(
        "category", "checked_out_to"
    )

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


@login_required
def barcode_pregenerate(request):
    """Pre-generate a batch of barcode labels for blank assets."""
    role = get_user_role(request.user)
    if role not in ("system_admin", "department_manager"):
        raise PermissionDenied

    if request.method == "POST":
        try:
            quantity = int(request.POST.get("quantity", 10))
        except (ValueError, TypeError):
            quantity = 10
        quantity = min(max(quantity, 1), 100)  # Clamp 1-100

        # Generate blank draft assets with barcodes
        created_assets = []
        for i in range(quantity):
            asset = Asset(
                name=f"Pre-generated #{i + 1}",
                status="draft",
                created_by=request.user,
            )
            asset.save()
            created_assets.append(asset)

        # Generate QR codes for labels
        label_assets = []
        for asset in created_assets:
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

        messages.success(request, f"{quantity} barcode labels pre-generated.")
        return render(
            request,
            "assets/bulk_labels.html",
            {"label_assets": label_assets},
        )

    return render(request, "assets/barcode_pregenerate.html")


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

        # Check for conflicts
        existing = (
            NFCTag.objects.filter(
                tag_id__iexact=tag_id, removed_at__isnull=True
            )
            .select_related("asset")
            .first()
        )
        if existing:
            messages.error(
                request,
                f"NFC tag '{tag_id}' is already assigned to "
                f"'{existing.asset.name}' ({existing.asset.barcode}).",
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

    return render(request, "assets/nfc_add.html", {"asset": asset})


@login_required
def nfc_remove(request, pk, nfc_pk):
    """Remove an NFC tag from an asset."""
    role = get_user_role(request.user)
    if role == "viewer":
        raise PermissionDenied
    nfc_tag = get_object_or_404(
        NFCTag, pk=nfc_pk, asset_id=pk, removed_at__isnull=True
    )

    if request.method == "POST":
        nfc_tag.removed_at = timezone.now()
        nfc_tag.removed_by = request.user
        nfc_tag.notes = (
            f"{nfc_tag.notes}\nRemoved: "
            f"{request.POST.get('notes', '')}".strip()
        )
        nfc_tag.save()
        messages.success(request, f"NFC tag '{nfc_tag.tag_id}' removed.")

    return redirect("assets:asset_detail", pk=pk)


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
    expected = session.expected_assets.select_related("category")
    confirmed_ids = set(session.confirmed_assets.values_list("pk", flat=True))

    return render(
        request,
        "assets/stocktake_detail.html",
        {
            "session": session,
            "expected": expected,
            "confirmed_ids": confirmed_ids,
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
                # Update current_location if asset is at a different location
                if asset.current_location != session.location:
                    asset.current_location = session.location
                    asset.save(update_fields=["current_location"])
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
                # Update current_location if asset is at a different location
                if found_asset.current_location != session.location:
                    old_location_name = (
                        found_asset.current_location.name
                        if found_asset.current_location
                        else "unknown"
                    )
                    found_asset.current_location = session.location
                    found_asset.save(update_fields=["current_location"])
                    messages.info(
                        request,
                        f"'{found_asset.name}' location updated from "
                        f"'{old_location_name}' to "
                        f"'{session.location.name}'.",
                    )
                    messages.success(request, f"Confirmed: {found_asset.name}")
                else:
                    messages.success(request, f"Confirmed: {found_asset.name}")
            else:
                # V8: Unknown code — link to Quick Capture with code
                from urllib.parse import urlencode

                from django.urls import reverse

                qc_url = (
                    reverse("assets:quick_capture")
                    + "?"
                    + urlencode({"code": code})
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
                missing = session.missing_assets
                missing_count = missing.update(status="missing")
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
    expected = session.expected_assets
    confirmed_ids = set(session.confirmed_assets.values_list("pk", flat=True))
    missing = expected.exclude(pk__in=confirmed_ids)
    unexpected = session.unexpected_assets

    return render(
        request,
        "assets/stocktake_summary.html",
        {
            "session": session,
            "total_expected": expected.count(),
            "confirmed_count": len(confirmed_ids),
            "missing_assets": missing,
            "missing_count": missing.count(),
            "unexpected_assets": unexpected,
            "unexpected_count": unexpected.count(),
        },
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
        # Re-run the filter query to get ALL matching asset IDs
        queryset = Asset.objects.all()
        filter_status = request.POST.get("filter_status", "")
        if filter_status:
            queryset = queryset.filter(status=filter_status)
        filter_q = request.POST.get("filter_q", "")
        if filter_q:
            queryset = queryset.filter(
                Q(name__icontains=filter_q)
                | Q(description__icontains=filter_q)
                | Q(barcode__icontains=filter_q)
                | Q(tags__name__icontains=filter_q)
            ).distinct()
        filter_department = request.POST.get("filter_department", "")
        if filter_department:
            queryset = queryset.filter(
                category__department_id=filter_department
            )
        filter_category = request.POST.get("filter_category", "")
        if filter_category:
            queryset = queryset.filter(category_id=filter_category)
        filter_location = request.POST.get("filter_location", "")
        if filter_location:
            if filter_location == "checked_out":
                queryset = queryset.filter(checked_out_to__isnull=False)
            else:
                queryset = queryset.filter(current_location_id=filter_location)
        filter_tag = request.POST.get("filter_tag", "")
        if filter_tag:
            queryset = queryset.filter(tags__id=filter_tag)
        filter_condition = request.POST.get("filter_condition", "")
        if filter_condition:
            queryset = queryset.filter(condition=filter_condition)
        asset_ids = list(queryset.values_list("pk", flat=True).distinct())
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

    else:
        messages.error(request, "Unknown bulk action.")

    return redirect("assets:asset_list")


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
            if not asset.description:
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
                pass

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

        asset.save()
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
                if action_date <= timezone.now():
                    timestamp = action_date

        from .services.transactions import create_handover

        create_handover(
            asset=asset,
            new_borrower=new_borrower,
            performed_by=request.user,
            to_location=to_location,
            notes=notes,
            timestamp=timestamp,
        )

        messages.success(
            request,
            f"'{asset.name}' handed over to "
            f"{new_borrower.get_display_name()}.",
        )
        return redirect("assets:asset_detail", pk=pk)

    from django.contrib.auth.models import Group

    borrower_group = Group.objects.filter(name="Borrower").first()
    users = User.objects.filter(is_active=True).order_by("username")
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

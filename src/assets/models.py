"""Models for PROPS asset management."""

import uuid
from io import BytesIO

import barcode
from barcode.writer import ImageWriter

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models
from django.urls import reverse
from django.utils import timezone


class Department(models.Model):
    """Organisational team or domain within the society."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    barcode_prefix = models.CharField(
        max_length=20,
        blank=True,
        help_text="Barcode prefix for assets in this department",
    )
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="managed_departments",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Tag(models.Model):
    """Flexible tags for assets."""

    name = models.CharField(max_length=50, unique=True)
    color = models.CharField(
        max_length=20,
        default="gray",
        help_text="Tailwind color name",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    """Asset type classification. Each category belongs to one department."""

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    icon = models.CharField(
        max_length=50, blank=True, help_text="Icon class name"
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="categories",
    )

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["department", "name"],
                name="unique_category_per_department",
            ),
        ]

    def __str__(self):
        return self.name


class Location(models.Model):
    """Physical place where assets can be stored."""

    name = models.CharField(max_length=100)
    address = models.TextField(blank=True)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    is_active = models.BooleanField(default=True)
    is_checkable = models.BooleanField(
        default=False,
        help_text=(
            "If True, this location can be checked out as a unit "
            "(all assets checked out to a single borrower)."
        ),
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "parent"],
                name="unique_location_name_per_parent",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(parent__isnull=True),
                name="unique_top_level_location_name",
            ),
        ]

    def __str__(self):
        return self.full_path

    @property
    def full_path(self):
        """Return the full hierarchical path."""
        parts = [self.name]
        current = self.parent
        while current:
            parts.insert(0, current.name)
            current = current.parent
        return " > ".join(parts)

    def get_absolute_url(self):
        return reverse("assets:location_detail", kwargs={"pk": self.pk})

    def clean(self):
        super().clean()
        if self.parent:
            # Prevent circular references
            current = self.parent
            depth = 1
            while current:
                if current.pk == self.pk:
                    raise ValidationError(
                        "A location cannot be its own ancestor."
                    )
                current = current.parent
                depth += 1
            # Max 4 levels of nesting
            if depth > 3:
                raise ValidationError(
                    "Maximum nesting depth of 4 levels exceeded."
                )

    def get_descendants(self):
        """Return all descendant locations using iterative batch queries."""
        descendants = []
        current_level = list(self.children.all())
        while current_level:
            descendants.extend(current_level)
            current_ids = [loc.pk for loc in current_level]
            current_level = list(
                Location.objects.filter(parent_id__in=current_ids)
            )
        return descendants


class AssetManager(models.Manager):
    """Custom manager with shared queryset builder for Asset."""

    def with_related(self):
        """Apply the standard select_related and prefetch_related calls.

        Includes annotations so that ``is_checked_out`` can avoid
        per-row queries for both serialised and non-serialised assets.
        """
        from django.db.models import Q, Sum
        from django.db.models.functions import Coalesce

        return (
            self.select_related(
                "category",
                "category__department",
                "current_location",
                "checked_out_to",
            )
            .prefetch_related(
                "tags",
                models.Prefetch(
                    "images",
                    queryset=AssetImage.objects.filter(is_primary=True),
                    to_attr="primary_images",
                ),
            )
            .annotate(
                _has_checked_out_serial=models.Exists(
                    AssetSerial.objects.filter(
                        asset=models.OuterRef("pk"),
                        checked_out_to__isnull=False,
                        is_archived=False,
                    )
                ),
                _outstanding_checkout_qty=models.Subquery(
                    Transaction.objects.filter(
                        asset=models.OuterRef("pk"),
                    )
                    .values("asset")
                    .annotate(
                        outstanding=Coalesce(
                            Sum(
                                "quantity",
                                filter=Q(action="checkout"),
                            ),
                            0,
                        )
                        - Coalesce(
                            Sum(
                                "quantity",
                                filter=Q(action="checkin"),
                            ),
                            0,
                        ),
                    )
                    .values("outstanding")[:1],
                    output_field=models.IntegerField(),
                ),
            )
        )


class Asset(models.Model):
    """Individual trackable asset."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("active", "Active"),
        ("retired", "Retired"),
        ("disposed", "Disposed"),
        ("missing", "Missing"),
        ("lost", "Lost"),
        ("stolen", "Stolen"),
    ]

    CONDITION_CHOICES = [
        ("excellent", "Excellent"),
        ("good", "Good"),
        ("fair", "Fair"),
        ("poor", "Poor"),
        ("damaged", "Damaged"),
    ]

    # Valid state transitions: from_status -> [to_statuses]
    VALID_TRANSITIONS = {
        "draft": ["active", "disposed"],
        "active": ["retired", "missing", "lost", "stolen", "disposed"],
        "retired": ["active", "disposed"],
        "missing": ["active", "lost", "stolen", "disposed"],
        "lost": ["active", "disposed"],
        "stolen": ["active", "disposed"],
        "disposed": [],
    }

    is_serialised = models.BooleanField(
        default=False,
        help_text="Track individual serial units for this asset",
    )
    is_kit = models.BooleanField(
        default=False,
        help_text="This asset is a kit containing other assets",
    )

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="assets",
        null=True,
        blank=True,
        help_text="Required for non-draft assets",
    )
    current_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="assets",
        null=True,
        blank=True,
        help_text="Required for non-draft assets",
    )
    home_location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        related_name="home_assets",
        null=True,
        blank=True,
        help_text="Where this asset lives when not checked out",
    )
    barcode = models.CharField(max_length=50, unique=True, blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="assets")
    quantity = models.PositiveIntegerField(default=1)
    condition = models.CharField(
        max_length=20, choices=CONDITION_CHOICES, default="good"
    )
    notes = models.TextField(blank=True)
    lost_stolen_notes = models.TextField(
        blank=True,
        help_text="Details about loss or theft circumstances",
    )
    is_public = models.BooleanField(
        default=False,
        help_text="Whether this asset is visible in public listings",
    )
    public_description = models.TextField(
        blank=True,
        null=True,
        help_text="Description shown in public listings",
    )
    barcode_image = models.ImageField(
        upload_to="barcodes/", blank=True, null=True
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="draft"
    )
    purchase_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    estimated_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    checked_out_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="borrowed_assets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_assets",
    )

    objects = AssetManager()

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["status"], name="idx_asset_status"),
            models.Index(fields=["created_at"], name="idx_asset_created_at"),
            models.Index(fields=["condition"], name="idx_asset_condition"),
            models.Index(
                fields=["is_public"],
                condition=models.Q(is_public=True),
                name="idx_asset_is_public",
            ),
            models.Index(fields=["is_kit"], name="idx_asset_is_kit"),
            models.Index(
                fields=["is_serialised"],
                name="idx_asset_is_serialised",
            ),
        ]
        permissions = [
            ("can_checkout_asset", "Can check out assets"),
            ("can_checkin_asset", "Can check in assets"),
            ("can_print_labels", "Can print asset labels"),
            ("can_merge_assets", "Can merge duplicate assets"),
            ("can_export_assets", "Can export asset data"),
            (
                "can_handover_asset",
                "Can hand over assets between borrowers",
            ),
            (
                "override_hold_checkout",
                "Can override hold list checkout block",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.barcode})"

    def get_absolute_url(self):
        return reverse("assets:asset_detail", kwargs={"pk": self.pk})

    def save(self, *args, **kwargs):
        is_new = self._state.adding

        # S2.2.3-11: When marking a checked-out asset as lost/stolen,
        # set current_location to the checkout destination (last known
        # location).
        if (
            not is_new
            and self.status in ("lost", "stolen")
            and self.checked_out_to_id
        ):
            last_checkout = (
                Transaction.objects.filter(asset=self, action="checkout")
                .order_by("-timestamp")
                .first()
            )
            if last_checkout and last_checkout.to_location:
                self.current_location = last_checkout.to_location

        if not self.barcode:
            self.barcode = self._generate_barcode()
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                super().save(*args, **kwargs)
                break
            except IntegrityError:
                if attempt >= max_attempts - 1:
                    raise
                # Barcode collision — regenerate and retry
                self.barcode = self._generate_barcode()
        if not self.barcode_image:
            self._generate_barcode_image()
        # Link any matching VirtualBarcode when a new asset is created
        if is_new and self.barcode:
            VirtualBarcode.objects.filter(
                barcode=self.barcode,
                assigned_to_asset__isnull=True,
            ).update(
                assigned_to_asset=self,
                assigned_at=timezone.now(),
            )

        # S7.17.2: When asset marked lost/stolen, update hold list
        # items to unavailable
        if self.status in ("lost", "stolen") and self.pk:
            from django.apps import apps

            HLItem = apps.get_model("assets", "HoldListItem")
            HLItem.objects.filter(
                asset=self,
            ).exclude(
                pull_status="unavailable",
            ).update(pull_status="unavailable")

    def clean(self):
        super().clean()
        # Non-draft assets must have category and location
        if self.status != "draft":
            if not self.category:
                raise ValidationError(
                    {"category": "Category is required for non-draft assets."}
                )
            if not self.current_location:
                raise ValidationError(
                    {
                        "current_location": "Location is required for "
                        "non-draft assets."
                    }
                )

    def can_transition_to(self, new_status):
        """Check if the status transition is valid."""
        return new_status in self.VALID_TRANSITIONS.get(self.status, [])

    def _generate_barcode(self):
        """Generate a unique barcode string.

        Uses department barcode_prefix if set, otherwise falls back
        to global BARCODE_PREFIX setting.
        """
        prefix = None
        if self.category_id and self.category.department:
            dept_prefix = self.category.department.barcode_prefix
            if dept_prefix:
                prefix = dept_prefix
        if not prefix:
            prefix = getattr(settings, "BARCODE_PREFIX", "ASSET")
        return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"

    def _generate_barcode_image(self):
        """Generate barcode image using Code128."""
        code128 = barcode.get_barcode_class("code128")
        rv = BytesIO()
        code = code128(self.barcode, writer=ImageWriter())
        code.write(
            rv,
            options={
                "module_width": 0.4,
                "module_height": 15,
                "font_size": 10,
                "text_distance": 5,
                "quiet_zone": 6.5,
            },
        )
        self.barcode_image.save(
            f"{self.barcode}.png",
            ContentFile(rv.getvalue()),
            save=True,
        )

    @property
    def primary_image(self):
        """Get the primary image for this asset.

        Uses the prefetched ``primary_images`` attribute when available
        (set by ``AssetManager.with_related()``) to avoid N+1 queries.
        Falls back to a database query when the prefetch is absent.
        """
        # Use prefetched primary_images from with_related() if available
        if hasattr(self, "primary_images"):
            if self.primary_images:
                return self.primary_images[0]
            return None
        # Fallback for assets not loaded via with_related()
        return (
            self.images.filter(is_primary=True).first() or self.images.first()
        )

    @property
    def active_nfc_tags(self):
        """Get all currently active NFC tags for this asset."""
        return self.nfc_tags.filter(removed_at__isnull=True)

    @property
    def is_checked_out(self):
        """Return whether the asset has any units checked out.

        Uses the ``_has_checked_out_serial`` annotation from
        ``with_related()`` when available to avoid N+1 queries.

        V500: For non-serialised assets, True when checked_out_to
        is set OR when transaction-tracked quantity is outstanding.
        """
        if self.is_serialised:
            # Use annotation from with_related() if available
            if hasattr(self, "_has_checked_out_serial"):
                return self._has_checked_out_serial
            return self.serials.filter(
                checked_out_to__isnull=False,
                is_archived=False,
            ).exists()
        # Use annotation from with_related() if available
        if hasattr(self, "_outstanding_checkout_qty"):
            outstanding = self._outstanding_checkout_qty or 0
            if outstanding > 0:
                return True
            return self.checked_out_to is not None
        if self.checked_out_to is not None:
            return True
        return self.available_count < self.quantity

    @property
    def effective_quantity(self):
        """Count of trackable units."""
        if self.is_serialised:
            return (
                self.serials.filter(
                    is_archived=False,
                )
                .exclude(status="disposed")
                .count()
            )
        return self.quantity

    @property
    def derived_status(self):
        """Aggregate status from serials, or own status."""
        if not self.is_serialised:
            return self.status
        statuses = set(
            self.serials.filter(is_archived=False).values_list(
                "status", flat=True
            )
        )
        if not statuses:
            return self.status
        # Priority order
        for s in [
            "active",
            "missing",
            "lost",
            "stolen",
            "retired",
            "disposed",
        ]:
            if s in statuses:
                return s
        return self.status

    @property
    def condition_summary(self):
        """Summary of conditions across serials, or own condition."""
        if not self.is_serialised:
            return self.condition
        from django.db.models import Count

        return dict(
            self.serials.filter(is_archived=False)
            .values_list("condition")
            .annotate(count=Count("id"))
        )

    @property
    def available_count(self):
        """Number of units available for checkout.

        V500: For non-serialised assets, tracks outstanding quantity
        via checkout/checkin transaction sums. Falls back to
        checked_out_to when no transactions exist (backward compat).
        """
        if self.is_serialised:
            return self.serials.filter(
                status="active",
                checked_out_to__isnull=True,
                is_archived=False,
            ).count()
        from django.db.models import Sum

        checkout_sum = (
            self.transactions.filter(action="checkout").aggregate(
                total=Sum("quantity")
            )["total"]
            or 0
        )
        checkin_sum = (
            self.transactions.filter(action="checkin").aggregate(
                total=Sum("quantity")
            )["total"]
            or 0
        )
        outstanding = checkout_sum - checkin_sum
        if outstanding > 0:
            return max(0, self.quantity - outstanding)
        # No transaction-tracked checkouts; fall back to FK
        if self.checked_out_to is not None:
            return max(0, self.quantity - 1)
        return self.quantity

    @property
    def checked_out_at(self):
        """Return the timestamp of the most recent checkout transaction."""
        if not self.is_checked_out:
            return None
        tx = (
            self.transactions.filter(action="checkout")
            .order_by("-timestamp")
            .first()
        )
        return tx.timestamp if tx else None

    @property
    def department(self):
        """Return the department via category, or None."""
        if self.category:
            return self.category.department
        return None


class AssetImage(models.Model):
    """Photographic records attached to assets."""

    AI_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    ]

    asset = models.ForeignKey(
        Asset, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to="assets/")
    thumbnail = models.ImageField(
        upload_to="thumbnails/", blank=True, null=True
    )
    detail_thumbnail = models.ImageField(
        upload_to="detail_thumbnails/", blank=True, null=True
    )
    caption = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
    )

    # AI analysis fields
    ai_description = models.TextField(blank=True, default="")
    ai_category_suggestion = models.CharField(
        max_length=100, blank=True, default=""
    )
    ai_tag_suggestions = models.JSONField(default=list, blank=True)
    ai_condition_suggestion = models.CharField(
        max_length=20, blank=True, default=""
    )
    ai_ocr_text = models.TextField(blank=True, default="")
    ai_name_suggestion = models.CharField(
        max_length=200, blank=True, default=""
    )
    ai_processed_at = models.DateTimeField(null=True, blank=True)
    ai_processing_status = models.CharField(
        max_length=20,
        choices=AI_STATUS_CHOICES,
        default="skipped",
    )
    ai_error_message = models.TextField(blank=True, default="")
    ai_prompt_tokens = models.PositiveIntegerField(default=0)
    ai_completion_tokens = models.PositiveIntegerField(default=0)
    ai_category_is_new = models.BooleanField(
        default=False,
        help_text="True when AI suggests a category not found in the database",
    )
    ai_department_suggestion = models.CharField(
        max_length=100, blank=True, default=""
    )
    ai_department_is_new = models.BooleanField(
        default=False,
        help_text="True when AI suggests a department"
        " not in the provided list",
    )
    ai_suggestions_applied = models.BooleanField(
        default=False,
        help_text="True when AI suggestions have been applied" " to the asset",
    )

    class Meta:
        ordering = ["-is_primary", "-uploaded_at"]
        indexes = [
            models.Index(
                fields=["asset", "is_primary"],
                name="idx_assetimage_asset_primary",
            ),
        ]

    def __str__(self):
        return f"Image for {self.asset.name}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if self.is_primary:
            AssetImage.objects.filter(
                asset=self.asset, is_primary=True
            ).exclude(pk=self.pk).update(is_primary=False)
        elif (
            not self.pk
            and not AssetImage.objects.filter(asset=self.asset).exists()
        ):
            self.is_primary = True
        super().save(*args, **kwargs)
        if is_new and self.image and not self.thumbnail:
            self._generate_thumbnail()
        if is_new and self.image:
            try:
                from .tasks import generate_detail_thumbnail

                generate_detail_thumbnail.delay(self.pk)
            except Exception:
                pass  # Celery/Redis unavailable; task skipped

    def _generate_thumbnail(self):
        """Generate a 300x300 max thumbnail and cap original at 3264px."""
        try:
            from io import BytesIO

            from PIL import Image

            from django.core.files.base import ContentFile

            img = Image.open(self.image)

            # Cap original at 3264px longest edge
            longest = max(img.size)
            if longest > 3264:
                scale = 3264 / longest
                new_size = (
                    int(img.size[0] * scale),
                    int(img.size[1] * scale),
                )
                img = img.resize(new_size, Image.LANCZOS)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=90)
                buf.seek(0)
                self.image.save(
                    self.image.name.split("/")[-1],
                    ContentFile(buf.getvalue()),
                    save=False,
                )

            # Generate 300px grid thumbnail
            grid_img = Image.open(self.image)
            grid_img.thumbnail((300, 300), Image.LANCZOS)

            if grid_img.mode in ("RGBA", "P"):
                grid_img = grid_img.convert("RGB")

            thumb_io = BytesIO()
            grid_img.save(thumb_io, format="JPEG", quality=80)
            thumb_io.seek(0)

            thumb_name = (
                f"thumb_{self.image.name.split('/')[-1].rsplit('.', 1)[0]}.jpg"
            )
            self.thumbnail.save(
                thumb_name, ContentFile(thumb_io.getvalue()), save=True
            )
        except (ImportError, Exception):
            pass


class NFCTag(models.Model):
    """Tracks NFC tags assigned to assets, with history."""

    tag_id = models.CharField(
        max_length=100,
        help_text="NFC tag identifier (serial number or custom ID)",
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.CASCADE, related_name="nfc_tags"
    )
    serial = models.ForeignKey(
        "AssetSerial",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nfc_tags",
        help_text="Specific serial unit (V479: COULD)",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="assigned_nfc_tags",
    )
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="removed_nfc_tags",
    )
    notes = models.TextField(
        blank=True, help_text="Reason for assignment or removal"
    )

    class Meta:
        ordering = ["-assigned_at"]
        verbose_name = "NFC Tag"
        verbose_name_plural = "NFC Tags"
        constraints = [
            models.UniqueConstraint(
                fields=["tag_id"],
                condition=models.Q(removed_at__isnull=True),
                name="unique_active_nfc_tag",
            ),
        ]

    def __str__(self):
        status = "active" if not self.removed_at else "removed"
        return f"{self.tag_id} ({status}) - {self.asset.name}"

    @property
    def is_active(self):
        return self.removed_at is None

    @classmethod
    def get_asset_by_tag(cls, tag_id):
        """Find the asset currently associated with an NFC tag."""
        active_tag = (
            cls.objects.filter(
                tag_id__iexact=tag_id,
                removed_at__isnull=True,
            )
            .select_related("asset")
            .first()
        )
        return active_tag.asset if active_tag else None


class Transaction(models.Model):
    """Immutable audit log of all asset movements and state changes."""

    ACTION_CHOICES = [
        ("checkout", "Check Out"),
        ("checkin", "Check In"),
        ("transfer", "Transfer"),
        ("relocate", "Relocate"),
        ("audit", "Audit"),
        ("handover", "Handover"),
        ("kit_return", "Kit Return"),
        ("note", "Note"),
    ]

    asset = models.ForeignKey(
        Asset, on_delete=models.CASCADE, related_name="transactions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="transactions",
        help_text="The user who performed the action",
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    from_location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions_from",
    )
    to_location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions_to",
    )
    borrower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="borrower_transactions",
        help_text="The person the asset is checked out to",
    )
    due_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Expected return date for checkouts",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        help_text="Number of units in this transaction",
    )
    serial = models.ForeignKey(
        "AssetSerial",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        help_text="Specific serial unit for serialised assets",
    )
    serial_barcode = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Denormalised barcode snapshot of the serial",
    )
    notes = models.TextField(blank=True)
    timestamp = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    is_backdated = models.BooleanField(default=False)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(
                fields=["timestamp"], name="idx_transaction_timestamp"
            ),
            models.Index(fields=["action"], name="idx_transaction_action"),
            models.Index(
                fields=["is_backdated"], name="idx_transaction_is_backdated"
            ),
        ]

    def __str__(self):
        return (
            f"{self.asset.name} - {self.get_action_display()} by {self.user}"
        )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "Transactions are immutable and cannot be modified."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Transactions are immutable and cannot be deleted."
        )


class AssetSerial(models.Model):
    """Individual serialised unit of a parent asset."""

    STATUS_CHOICES = [
        ("active", "Active"),
        ("retired", "Retired"),
        ("missing", "Missing"),
        ("lost", "Lost"),
        ("stolen", "Stolen"),
        ("disposed", "Disposed"),
    ]

    CONDITION_CHOICES = Asset.CONDITION_CHOICES

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="serials",
    )
    serial_number = models.CharField(max_length=100)
    barcode = models.CharField(
        max_length=50, unique=True, null=True, blank=True
    )
    barcode_image = models.ImageField(
        upload_to="barcodes/serial/", blank=True, null=True
    )
    condition = models.CharField(
        max_length=20, choices=CONDITION_CHOICES, default="good"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="active"
    )
    notes = models.TextField(blank=True)
    checked_out_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="borrowed_serials",
    )
    current_location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="serials",
    )
    lost_stolen_notes = models.TextField(
        blank=True,
        help_text="Details about loss or theft circumstances",
    )
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["serial_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "serial_number"],
                name="unique_serial_per_asset",
            ),
        ]
        indexes = [
            models.Index(
                fields=["asset", "status"],
                name="idx_serial_asset_status",
            ),
        ]

    def __str__(self):
        return f"{self.asset.name} #{self.serial_number}"

    def clean(self):
        super().clean()
        # Parent must be serialised
        if self.asset_id and not self.asset.is_serialised:
            raise ValidationError(
                "Cannot create serial units for a " "non-serialised asset."
            )
        # Status must not be draft
        if self.status == "draft":
            raise ValidationError(
                {"status": "'draft' is not a valid serial status."}
            )
        # Cross-table barcode uniqueness
        if self.barcode:
            if (
                Asset.objects.filter(barcode=self.barcode)
                .exclude(pk=None)
                .exists()
            ):
                raise ValidationError(
                    {
                        "barcode": "This barcode is already in use "
                        "by an asset."
                    }
                )

    def save(self, *args, **kwargs):
        # Capture barcode before save for disposal tracking
        _barcode_before = self.barcode
        super().save(*args, **kwargs)
        # S7.10.5: Clear barcode on disposal to free for reuse
        # S7.16.9: Auto-unpin from kit components on disposal
        if self.status == "disposed":
            if _barcode_before:
                # S7.19.3: Create a disposal note transaction with
                # serial_barcode snapshot so scan lookup can still
                # find disposed serials by their old barcode
                if not Transaction.objects.filter(
                    serial=self,
                    serial_barcode=_barcode_before,
                ).exists():
                    Transaction.objects.create(
                        asset=self.asset,
                        serial=self,
                        user_id=self.asset.created_by_id,
                        action="note",
                        serial_barcode=_barcode_before,
                        notes=(f"Serial #{self.serial_number} " f"disposed."),
                    )
                AssetSerial.objects.filter(pk=self.pk).update(barcode=None)
                # Keep in-memory barcode for scan lookup to show
                # disposed message (S7.19.3)
            AssetKit.objects.filter(serial=self).update(serial=None)

        # S7.19.2: When all serials are disposed, auto-update
        # parent asset status to disposed
        if self.asset_id and self.status == "disposed":
            parent = self.asset
            non_disposed = (
                parent.serials.filter(
                    is_archived=False,
                )
                .exclude(status="disposed")
                .exists()
            )
            if not non_disposed:
                # All active serials are disposed — update parent
                has_active_serials = parent.serials.filter(
                    is_archived=False,
                ).exists()
                if has_active_serials:
                    Asset.objects.filter(pk=parent.pk).update(
                        status="disposed"
                    )


class VirtualBarcode(models.Model):
    """Tracks pre-printed barcodes not yet assigned to assets."""

    barcode = models.CharField(max_length=50, unique=True)
    barcode_image = models.ImageField(
        upload_to="barcodes/virtual/", blank=True, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="virtual_barcodes",
    )
    assigned_to_asset = models.OneToOneField(
        Asset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="virtual_barcode_source",
    )
    assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "assigned" if self.assigned_to_asset else "unassigned"
        return f"{self.barcode} ({status})"


class AssetKit(models.Model):
    """Links component assets into a kit."""

    kit = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="kit_components",
    )
    component = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="member_of_kits",
    )
    quantity = models.PositiveIntegerField(default=1)
    is_required = models.BooleanField(default=True)
    is_kit_only = models.BooleanField(
        default=False,
        help_text="Component can only be checked out as part of this kit",
    )
    serial = models.ForeignKey(
        AssetSerial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kit_memberships",
        help_text="Specific serial unit if component is serialised",
    )
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kit", "component"],
                name="unique_kit_component",
            ),
        ]

    def __str__(self):
        return f"{self.kit.name} -> {self.component.name}"

    def clean(self):
        super().clean()
        # Kit asset must have is_kit=True
        if self.kit_id and not self.kit.is_kit:
            raise ValidationError("The kit asset must have is_kit=True.")
        # No self-reference
        if self.kit_id and self.component_id:
            if self.kit_id == self.component_id:
                raise ValidationError(
                    "An asset cannot be a component of itself."
                )
            # Circular reference detection
            self._check_circular(self.component_id, {self.kit_id})

        # If serial is set, it must belong to the component
        if self.serial_id and self.component_id:
            if self.serial.asset_id != self.component_id:
                raise ValidationError(
                    {"serial": "Serial must belong to the " "component asset."}
                )

    def _check_circular(self, component_id, visited):
        """Recursively check for circular kit references."""
        sub_kits = AssetKit.objects.filter(kit_id=component_id).values_list(
            "component_id", flat=True
        )
        for sub_id in sub_kits:
            if sub_id in visited:
                raise ValidationError("Circular kit reference detected.")
            visited.add(sub_id)
            self._check_circular(sub_id, visited)


class StocktakeSession(models.Model):
    """Tracks an audit pass of a specific location."""

    STATUS_CHOICES = [
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("abandoned", "Abandoned"),
    ]

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="stocktake_sessions",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stocktake_sessions",
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="stocktake_sessions",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="in_progress"
    )
    notes = models.TextField(blank=True)
    confirmed_assets = models.ManyToManyField(
        Asset,
        blank=True,
        related_name="stocktake_confirmations",
        help_text="Assets confirmed present during this stocktake",
    )

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["location"],
                condition=models.Q(status="in_progress"),
                name="unique_in_progress_stocktake_per_location",
            ),
        ]

    def __str__(self):
        return (
            f"Stocktake at {self.location.name} "
            f"({self.get_status_display()})"
        )

    @property
    def expected_assets(self):
        """Assets expected at this location (active and missing per spec).

        V236/V737: Also includes checked-out assets whose home_location
        matches the stocktake location, so they appear in the expected
        list flagged as "checked out" rather than being counted as
        missing.
        """
        from django.db.models import Q

        return Asset.objects.filter(
            Q(current_location=self.location)
            | Q(
                home_location=self.location,
                checked_out_to__isnull=False,
            ),
            status__in=["active", "missing"],
        ).distinct()

    @property
    def missing_assets(self):
        """Assets expected but not confirmed."""
        return self.expected_assets.exclude(
            pk__in=self.confirmed_assets.values_list("pk", flat=True)
        )

    @property
    def unexpected_assets(self):
        """Assets confirmed but not expected at this location."""
        return self.confirmed_assets.exclude(current_location=self.location)


class StocktakeItem(models.Model):
    """Per-item record within a stocktake session (G9 — S3.1.9)."""

    STATUS_CHOICES = [
        ("expected", "Expected"),
        ("confirmed", "Confirmed"),
        ("missing", "Missing"),
        ("unexpected", "Unexpected"),
    ]

    session = models.ForeignKey(
        StocktakeSession,
        on_delete=models.CASCADE,
        related_name="items",
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="stocktake_items",
    )
    serial = models.ForeignKey(
        "AssetSerial",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stocktake_items",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="expected"
    )
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    scanned_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "asset", "serial"],
                name="unique_stocktake_item",
            ),
        ]
        ordering = ["asset__name"]

    def __str__(self):
        return (
            f"{self.asset.name} — "
            f"{self.get_status_display()} "
            f"(session #{self.session_id})"
        )


def validate_logo_file_size(value):
    """Enforce a maximum file size of 500 KB for logo uploads."""
    if value.size > 500 * 1024:
        raise ValidationError("File size must be at most 500 KB.")


def validate_favicon_file_size(value):
    """Enforce a maximum file size of 100 KB for favicon uploads."""
    if value.size > 100 * 1024:
        raise ValidationError("File size must be at most 100 KB.")


class SiteBranding(models.Model):
    """Singleton model for site logo and favicon configuration."""

    logo_light = models.ImageField(
        upload_to="branding/",
        blank=True,
        null=True,
        help_text="Logo for light backgrounds (SVG or PNG, max 500 KB)",
        validators=[validate_logo_file_size],
    )
    logo_dark = models.ImageField(
        upload_to="branding/",
        blank=True,
        null=True,
        help_text="Logo for dark backgrounds (SVG or PNG, max 500 KB)",
        validators=[validate_logo_file_size],
    )
    favicon = models.ImageField(
        upload_to="branding/",
        blank=True,
        null=True,
        help_text="Favicon (PNG or ICO, max 100 KB)",
        validators=[validate_favicon_file_size],
    )
    primary_color = models.CharField(
        max_length=7,
        blank=True,
        help_text="Primary brand colour (hex, e.g. #4F46E5)",
    )
    secondary_color = models.CharField(
        max_length=7,
        blank=True,
        help_text="Secondary brand colour (hex)",
    )
    accent_color = models.CharField(
        max_length=7,
        blank=True,
        help_text="Accent brand colour (hex)",
    )
    color_mode = models.CharField(
        max_length=10,
        choices=[
            ("light", "Light"),
            ("dark", "Dark"),
            ("system", "System"),
        ],
        default="system",
        help_text="Default colour mode for the site",
    )

    class Meta:
        verbose_name = "Site Branding"
        verbose_name_plural = "Site Branding"

    def __str__(self):
        return "Site Branding"

    def clean(self):
        super().clean()
        if self.logo_light:
            ext = self.logo_light.name.rsplit(".", 1)[-1].lower()
            if ext not in ("svg", "png"):
                raise ValidationError(
                    {"logo_light": "Only SVG and PNG files are allowed."}
                )
        if self.logo_dark:
            ext = self.logo_dark.name.rsplit(".", 1)[-1].lower()
            if ext not in ("svg", "png"):
                raise ValidationError(
                    {"logo_dark": "Only SVG and PNG files are allowed."}
                )
        if self.favicon:
            ext = self.favicon.name.rsplit(".", 1)[-1].lower()
            if ext not in ("png", "ico"):
                raise ValidationError(
                    {"favicon": "Only PNG and ICO files are allowed."}
                )

    def save(self, *args, **kwargs):
        # Enforce singleton: always reuse the first row's pk
        if not self.pk:
            existing = SiteBranding.objects.first()
            if existing:
                self.pk = existing.pk
        super().save(*args, **kwargs)
        cache.delete("site_branding")

    @classmethod
    def get_cached(cls):
        """Return the cached SiteBranding instance, or None."""
        instance = cache.get("site_branding")
        if instance is None:
            instance = cls.objects.first()
            cache.set("site_branding", instance, timeout=None)
        return instance


class Project(models.Model):
    """A project or event that may have hold lists."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_projects",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class ProjectDateRange(models.Model):
    """A date range within a project (e.g. rehearsal week, show week)."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="date_ranges",
    )
    label = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="project_date_ranges",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="project_date_ranges",
    )

    class Meta:
        ordering = ["start_date"]

    def __str__(self):
        return f"{self.project.name}: {self.label}"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValidationError("End date must be after start date.")


class HoldListStatus(models.Model):
    """Status for hold lists (Draft, Confirmed, In Progress, etc.)."""

    name = models.CharField(max_length=50, unique=True)
    is_default = models.BooleanField(default=False)
    is_terminal = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    color = models.CharField(max_length=20, blank=True, default="gray")

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "Hold list statuses"

    def __str__(self):
        return self.name


class HoldList(models.Model):
    """A list of assets to be held/reserved for a project."""

    name = models.CharField(max_length=200)
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hold_lists",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="hold_lists",
    )
    status = models.ForeignKey(
        HoldListStatus,
        on_delete=models.PROTECT,
        related_name="hold_lists",
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_hold_lists",
    )
    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["project", "status"],
                name="idx_holdlist_project_status",
            ),
            models.Index(
                fields=["department", "status"],
                name="idx_holdlist_dept_status",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not self.project and not (self.start_date and self.end_date):
            raise ValidationError("Dates are required when no project is set.")
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValidationError("End date must be after start date.")


class HoldListItem(models.Model):
    """An item on a hold list."""

    PULL_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("pulled", "Pulled"),
        ("unavailable", "Unavailable"),
    ]

    hold_list = models.ForeignKey(
        HoldList,
        on_delete=models.CASCADE,
        related_name="items",
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="hold_list_items",
    )
    serial = models.ForeignKey(
        AssetSerial,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="hold_list_items",
    )
    quantity = models.PositiveIntegerField(default=1)
    pull_status = models.CharField(
        max_length=15,
        choices=PULL_STATUS_CHOICES,
        default="pending",
    )
    pulled_at = models.DateTimeField(null=True, blank=True)
    pulled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pulled_items",
    )
    notes = models.TextField(blank=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="added_hold_items",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hold_list", "asset", "serial"],
                name="unique_holdlist_asset_serial",
            ),
            models.UniqueConstraint(
                fields=["hold_list", "asset"],
                condition=models.Q(serial__isnull=True),
                name="unique_holdlist_asset_no_serial",
            ),
        ]

    def __str__(self):
        return f"{self.hold_list.name}: {self.asset.name}"

    def clean(self):
        super().clean()
        if self.serial and self.quantity != 1:
            raise ValidationError(
                "Quantity must be 1 when a specific serial is set."
            )


class PrintClient(models.Model):
    """Remote print station paired via props-label-manager (S3.1.20)."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
    ]

    name = models.CharField(max_length=200)
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    is_active = models.BooleanField(default=True)
    is_connected = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    printers = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_print_clients",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    protocol_version = models.CharField(max_length=10, default="1")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "is_connected"],
                name="idx_printclient_status_conn",
            ),
            models.Index(
                fields=["is_active"],
                name="idx_printclient_is_active",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class PrintRequest(models.Model):
    """Print job sent to a remote print client (S3.1.21)."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("acked", "Acknowledged"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    VALID_TRANSITIONS = {
        "pending": ["sent", "failed"],
        "sent": ["acked", "failed"],
        "acked": ["completed", "failed"],
        "completed": [],
        "failed": [],
    }

    job_id = models.UUIDField(default=uuid.uuid4, unique=True)
    print_client = models.ForeignKey(
        PrintClient,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="print_requests",
    )
    LABEL_TYPE_CHOICES = [
        ("asset", "Asset"),
        ("location", "Location"),
    ]

    asset = models.ForeignKey(
        Asset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="print_requests",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="print_requests",
    )
    label_type = models.CharField(
        max_length=20,
        choices=LABEL_TYPE_CHOICES,
        default="asset",
    )
    printer_id = models.CharField(max_length=50)
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    error_message = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    acked_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="print_requests",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["print_client", "status"],
                name="idx_printreq_client_status",
            ),
            models.Index(
                fields=["asset"],
                name="idx_printreq_asset",
            ),
            models.Index(
                fields=["status", "created_at"],
                name="idx_printreq_status_created",
            ),
        ]

    def __str__(self):
        return f"PrintRequest {self.job_id} ({self.get_status_display()})"

    def transition_to(self, new_status, error_message=""):
        """Transition to a new status, enforcing the state machine.

        Valid transitions:
          pending -> sent, pending -> failed
          sent -> acked, sent -> failed
          acked -> completed, acked -> failed

        Raises ValidationError for invalid transitions.
        """
        valid_targets = self.VALID_TRANSITIONS.get(self.status, [])
        if new_status not in valid_targets:
            raise ValidationError(
                f"Cannot transition from '{self.status}' "
                f"to '{new_status}'."
            )

        now = timezone.now()
        self.status = new_status

        if new_status == "sent":
            self.sent_at = now
        elif new_status == "acked":
            self.acked_at = now
        elif new_status == "completed":
            self.completed_at = now
        elif new_status == "failed":
            self.completed_at = now
            if error_message:
                self.error_message = error_message

        self.save()

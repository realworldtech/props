"""Models for PROPS asset management."""

import uuid
from io import BytesIO

import barcode
from barcode.writer import ImageWriter

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models
from django.urls import reverse
from django.utils import timezone


class Department(models.Model):
    """Organisational team or domain within the society."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
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

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "parent"],
                name="unique_location_name_per_parent",
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
        """Return all descendant locations."""
        descendants = []
        children = list(self.children.all())
        for child in children:
            descendants.append(child)
            descendants.extend(child.get_descendants())
        return descendants


class AssetManager(models.Manager):
    """Custom manager with shared queryset builder for Asset."""

    def with_related(self):
        """Apply the standard select_related and prefetch_related calls."""
        return self.select_related(
            "category",
            "category__department",
            "current_location",
            "checked_out_to",
        ).prefetch_related("tags")


class Asset(models.Model):
    """Individual trackable asset."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("active", "Active"),
        ("retired", "Retired"),
        ("disposed", "Disposed"),
        ("missing", "Missing"),
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
        "active": ["retired", "missing", "disposed"],
        "retired": ["active", "disposed"],
        "missing": ["active", "disposed"],
        "disposed": [],
    }

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
        ]
        permissions = [
            ("can_checkout_asset", "Can check out assets"),
            ("can_checkin_asset", "Can check in assets"),
            ("can_print_labels", "Can print asset labels"),
            ("can_merge_assets", "Can merge duplicate assets"),
            ("can_export_assets", "Can export asset data"),
            ("can_handover_asset", "Can hand over assets between borrowers"),
        ]

    def __str__(self):
        return f"{self.name} ({self.barcode})"

    def get_absolute_url(self):
        return reverse("assets:asset_detail", kwargs={"pk": self.pk})

    def save(self, *args, **kwargs):
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
        """Generate a unique barcode string."""
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
        """Get the primary image for this asset."""
        return (
            self.images.filter(is_primary=True).first() or self.images.first()
        )

    @property
    def active_nfc_tags(self):
        """Get all currently active NFC tags for this asset."""
        return self.nfc_tags.filter(removed_at__isnull=True)

    @property
    def is_checked_out(self):
        """Return whether the asset is currently checked out."""
        return self.checked_out_to is not None

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

    def _generate_thumbnail(self):
        """Generate a 300x300 max thumbnail."""
        try:
            from io import BytesIO

            from PIL import Image

            from django.core.files.base import ContentFile

            img = Image.open(self.image)
            img.thumbnail((300, 300), Image.LANCZOS)

            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            thumb_io = BytesIO()
            img.save(thumb_io, format="JPEG", quality=80)
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
        ("audit", "Audit"),
        ("handover", "Handover"),
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
        """Assets expected at this location (active and missing per spec)."""
        return Asset.objects.filter(
            current_location=self.location,
            status__in=["active", "missing"],
        )

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
            cache.set("site_branding", instance, timeout=3600)
        return instance

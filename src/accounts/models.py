"""Custom user model for PROPS."""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver


class CustomUser(AbstractUser):
    """Extended user with display name, phone, and required email."""

    display_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Human-readable name displayed in check-out records",
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="Contact number for following up on overdue check-outs",
    )
    organisation = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="External organisation (e.g., 'NUCMS')",
    )
    email = models.EmailField("email address", blank=False, unique=True)
    email_verified = models.BooleanField(
        default=False,
        help_text="Set to True when user clicks email verification link",
    )
    requested_department = models.ForeignKey(
        "assets.Department",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_by_users",
        help_text="Department selected during registration",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_users",
        help_text="Admin who approved or rejected this account",
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the account was approved or rejected",
    )
    rejection_reason = models.TextField(
        blank=True,
        default="",
        help_text="Reason for rejection, if applicable",
    )

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        permissions = [
            ("can_approve_users", "Can approve user registrations"),
        ]

    @property
    def account_state(self):
        """Return the account state: active, pending, unverified."""
        if self.is_active:
            return "active"
        if self.email_verified and self.rejection_reason:
            return "rejected"
        if self.email_verified:
            return "pending_approval"
        return "unverified"

    def get_display_name(self):
        """Return display_name if set, otherwise full name or username."""
        if self.display_name:
            return self.display_name
        full = self.get_full_name()
        return full if full else self.username

    def __str__(self):
        return self.get_display_name()


@receiver(pre_delete, sender=CustomUser)
def create_orphan_checkout_transactions(sender, instance, **kwargs):
    """Create transaction notes for assets checked out to a user
    being deleted, preserving the audit trail (S7.10.1)."""
    from assets.models import Asset, Transaction

    checked_out = Asset.objects.filter(checked_out_to=instance)
    display_name = instance.get_display_name()
    for asset in checked_out:
        Transaction.objects.create(
            asset=asset,
            user=instance,
            action="audit",
            notes=(
                f"Borrower '{display_name}' (user #{instance.pk}) "
                f"deleted. Asset was checked out to this borrower."
            ),
        )

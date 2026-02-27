"""Shared borrower list helpers for checkout/handover views."""

from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()


def get_borrower_lists():
    """Return (internal_users, external_borrowers) querysets.

    Uses permission-based checks instead of hardcoded group names:
    - Internal: active users who can checkout assets or are superusers
    - External: active users who can only be borrowers (no checkout perm)
    """
    # All active users who have either checkout or borrower permission
    all_eligible = (
        User.objects.filter(is_active=True)
        .filter(
            Q(is_superuser=True)
            | Q(groups__permissions__codename="can_checkout_asset")
            | Q(groups__permissions__codename="can_be_borrower")
        )
        .distinct()
    )

    # External borrowers: have can_be_borrower but NOT can_checkout_asset
    # and are not superusers
    external_borrowers = (
        User.objects.filter(
            is_active=True,
            groups__permissions__codename="can_be_borrower",
        )
        .exclude(
            Q(is_superuser=True)
            | Q(groups__permissions__codename="can_checkout_asset")
        )
        .distinct()
        .order_by("first_name", "last_name", "username")
    )

    external_ids = set(external_borrowers.values_list("pk", flat=True))

    # Internal: everyone eligible except external borrowers
    internal_users = all_eligible.exclude(pk__in=external_ids).order_by(
        "first_name", "last_name", "username"
    )

    return internal_users, external_borrowers

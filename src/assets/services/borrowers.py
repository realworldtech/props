"""Shared borrower list helpers for checkout/handover views."""

from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()


def get_borrower_lists():
    """Return (internal_users, external_borrowers) querysets.

    Uses permission-based checks instead of hardcoded group names:
    - Internal: active users who can checkout assets or are superusers
    - External: active users who can only be borrowers (no checkout perm)

    Checks both group-level and user-level permissions to stay aligned
    with has_perm() semantics used elsewhere.
    """
    # Permission queries covering both group and direct user assignment
    can_checkout_q = Q(groups__permissions__codename="can_checkout_asset") | Q(
        user_permissions__codename="can_checkout_asset"
    )
    can_be_borrower_q = Q(groups__permissions__codename="can_be_borrower") | Q(
        user_permissions__codename="can_be_borrower"
    )

    # All active users who have either checkout or borrower permission
    all_eligible = (
        User.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | can_checkout_q | can_be_borrower_q)
        .distinct()
    )

    # External borrowers: have can_be_borrower but NOT can_checkout_asset
    # and are not superusers
    external_borrowers = (
        User.objects.filter(is_active=True)
        .filter(can_be_borrower_q)
        .exclude(Q(is_superuser=True) | can_checkout_q)
        .distinct()
        .order_by("first_name", "last_name", "username")
    )

    # Internal: everyone eligible except external borrowers (subquery)
    internal_users = all_eligible.exclude(
        pk__in=external_borrowers.values("pk")
    ).order_by("first_name", "last_name", "username")

    return internal_users, external_borrowers

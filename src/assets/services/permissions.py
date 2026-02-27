"""Permission checking for department-based access control."""

from django.contrib.auth import get_user_model

from ..models import Asset, Department

User = get_user_model()


def get_user_role(user: User, department: Department = None) -> str:
    """Determine the user's highest role.

    Returns one of: 'system_admin', 'department_manager', 'member',
    'viewer', or 'borrower'.

    Uses permission-based checks rather than group names, so
    deployments that rename groups (e.g. "Member" → "Team Member")
    continue to work correctly.
    """
    if user.is_superuser:
        return "system_admin"

    if user.has_perm("accounts.can_approve_users"):
        return "system_admin"

    if department and department.managers.filter(pk=user.pk).exists():
        return "department_manager"

    if user.has_perm("assets.can_merge_assets"):
        return "department_manager"

    if user.has_perm("assets.can_checkout_asset"):
        return "member"

    if user.has_perm("assets.can_be_borrower"):
        return "borrower"

    return "viewer"


def can_edit_asset(user: User, asset: Asset) -> bool:
    """Check if the user can edit the given asset."""
    role = get_user_role(user, asset.department)

    if role == "system_admin":
        return True

    if role == "department_manager":
        # Block write access if the asset's department is inactive
        dept = asset.department
        if dept and not dept.is_active:
            if not user.is_superuser and not user.has_perm(
                "accounts.can_approve_users"
            ):
                return False
        return True

    if role == "member":
        # Members can edit their own drafts
        if asset.status == "draft" and asset.created_by == user:
            return True

    return False


def can_delete_asset(user: User, asset: Asset) -> bool:
    """Check if the user can delete (dispose) the given asset."""
    role = get_user_role(user, asset.department)
    return role in ("system_admin", "department_manager")


def can_checkout_asset(user: User, asset: Asset) -> bool:
    """Check if the user can check out the given asset."""
    role = get_user_role(user, asset.department)
    return role in ("system_admin", "department_manager", "member")


def can_handover_asset(user: User, asset: Asset) -> bool:
    """Check if the user can hand over a checked-out asset."""
    role = get_user_role(user, asset.department)
    return role in ("system_admin", "department_manager")

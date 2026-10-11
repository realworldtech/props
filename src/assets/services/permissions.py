"""Permission checking for department-based access control."""

from django.contrib.auth import get_user_model

from ..models import Asset, Department

User = get_user_model()


def get_user_role(user: User, department: Department = None) -> str:
    """Return the user's highest role.

    Returns one of: 'system_admin', 'department_manager', 'member',
    'viewer', or 'borrower'.

    With a department, the manager role applies only if the user
    manages that department (S2.8.3-05). Without a department, any
    user with the manager permissions has the manager role.

    The checks use permissions, not group names, so deployments can
    rename the groups (e.g. "Member" → "Team Member").
    """
    if user.is_superuser:
        return "system_admin"

    if user.has_perm("accounts.can_approve_users"):
        return "system_admin"

    if department is not None:
        if department.managers.filter(pk=user.pk).exists():
            return "department_manager"
    elif user.has_perm("assets.can_merge_assets"):
        return "department_manager"

    if user.has_perm("assets.can_checkout_asset"):
        return "member"

    if user.has_perm("assets.can_be_borrower"):
        return "borrower"

    return "viewer"


def can_edit_asset(user: User, asset: Asset) -> bool:
    """Check if the user can edit the given asset."""
    # A disposed asset is a fixed historical record (S2.2.3-05).
    if asset.status == "disposed":
        return False
    role = get_user_role(user, asset.department)

    if role == "system_admin":
        return True

    # S7.7.2: a deactivated department is read-only for non-admins.
    dept = asset.department
    if dept and not dept.is_active:
        return False

    # S2.10.3-04: drafts are shared work in progress.
    if asset.status == "draft" and role in ("department_manager", "member"):
        return True

    if role == "department_manager":
        return True

    return False


def can_manage_asset(user: User, asset: Asset) -> bool:
    """Return True for System Admins and the asset's Department Managers.

    S2.10.3-01: kits, barcodes and serialisation are not for Members.
    """
    role = get_user_role(user, asset.department)
    return role in ("system_admin", "department_manager") and can_edit_asset(
        user, asset
    )


def can_change_status(user: User, asset: Asset) -> bool:
    """Check if the user can mark the asset missing, lost, stolen or found.

    S2.2.3-03: only Department Managers and System Admins.
    """
    return can_manage_asset(user, asset)


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


def can_assign_category(user: User, category) -> bool:
    """Return False for a non-admin and a category of an inactive
    department (S7.7.2: a deactivated department is read-only)."""
    dept = category.department
    if dept is None or dept.is_active:
        return True
    return get_user_role(user, dept) == "system_admin"

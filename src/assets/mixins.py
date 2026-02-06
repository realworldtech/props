"""Permission mixins for the assets app."""

from django.core.exceptions import PermissionDenied


class DepartmentPermissionMixin:
    """Mixin to check department-level permissions on views."""

    def check_department_permission(self, user, asset):
        """Check if user has permission to modify the asset.

        Returns True if:
        - User is superuser
        - User is in System Admin group
        - User is Department Manager for the asset's department
        - User is a Member who created this draft asset
        """
        if user.is_superuser:
            return True

        groups = set(user.groups.values_list("name", flat=True))

        if "System Admin" in groups:
            return True

        if "Department Manager" in groups:
            if (
                asset.department
                and asset.department.managers.filter(pk=user.pk).exists()
            ):
                return True

        if "Member" in groups:
            if asset.status == "draft" and asset.created_by == user:
                return True

        return False

    def require_department_permission(self, user, asset):
        """Raise PermissionDenied if user lacks permission."""
        if not self.check_department_permission(user, asset):
            raise PermissionDenied

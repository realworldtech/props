"""Permission mixins for the assets app."""

from django.core.exceptions import PermissionDenied

from assets.services.permissions import can_edit_asset


class DepartmentPermissionMixin:
    """Mixin to check department-level permissions on views."""

    def check_department_permission(self, user, asset):
        """Check if user has permission to modify the asset.

        Delegates to the can_edit_asset service function which uses
        permission-based role resolution (not hardcoded group names).
        """
        return can_edit_asset(user, asset)

    def require_department_permission(self, user, asset):
        """Raise PermissionDenied if user lacks permission."""
        if not self.check_department_permission(user, asset):
            raise PermissionDenied

"""Management command to create permission groups per §4.8.4."""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from assets.models import Asset, Category, Department, Location


class Command(BaseCommand):
    help = "Create the four permission groups with appropriate permissions"

    def handle(self, *args, **options):
        # Get content types
        asset_ct = ContentType.objects.get_for_model(Asset)
        category_ct = ContentType.objects.get_for_model(Category)
        location_ct = ContentType.objects.get_for_model(Location)
        department_ct = ContentType.objects.get_for_model(Department)

        # Get or create standard permissions
        def get_perm(codename, ct=None):
            if ct:
                return Permission.objects.get(
                    codename=codename, content_type=ct
                )
            return Permission.objects.get(codename=codename)

        # Asset permissions
        view_asset = get_perm("view_asset", asset_ct)
        add_asset = get_perm("add_asset", asset_ct)
        change_asset = get_perm("change_asset", asset_ct)
        delete_asset = get_perm("delete_asset", asset_ct)

        # Custom asset permissions
        checkout_asset = get_perm("can_checkout_asset", asset_ct)
        checkin_asset = get_perm("can_checkin_asset", asset_ct)
        print_labels = get_perm("can_print_labels", asset_ct)
        merge_assets = get_perm("can_merge_assets", asset_ct)
        export_assets = get_perm("can_export_assets", asset_ct)
        handover_asset = get_perm("can_handover_asset", asset_ct)

        # Category permissions
        add_category = get_perm("add_category", category_ct)
        change_category = get_perm("change_category", category_ct)
        delete_category = get_perm("delete_category", category_ct)
        view_category = get_perm("view_category", category_ct)

        # Location permissions
        add_location = get_perm("add_location", location_ct)
        change_location = get_perm("change_location", location_ct)
        delete_location = get_perm("delete_location", location_ct)
        view_location = get_perm("view_location", location_ct)

        # Department permissions
        add_department = get_perm("add_department", department_ct)
        change_department = get_perm("change_department", department_ct)
        delete_department = get_perm("delete_department", department_ct)
        view_department = get_perm("view_department", department_ct)

        # System Admin group
        system_admin, _ = Group.objects.get_or_create(name="System Admin")
        system_admin.permissions.set(
            [
                view_asset,
                add_asset,
                change_asset,
                delete_asset,
                checkout_asset,
                checkin_asset,
                print_labels,
                merge_assets,
                export_assets,
                handover_asset,
                add_category,
                change_category,
                delete_category,
                view_category,
                add_location,
                change_location,
                delete_location,
                view_location,
                add_department,
                change_department,
                delete_department,
                view_department,
            ]
        )
        self.stdout.write(
            self.style.SUCCESS("Created/updated 'System Admin' group")
        )

        # Department Manager group
        dept_manager, _ = Group.objects.get_or_create(
            name="Department Manager"
        )
        dept_manager.permissions.set(
            [
                view_asset,
                add_asset,
                change_asset,
                delete_asset,
                checkout_asset,
                checkin_asset,
                print_labels,
                merge_assets,
                export_assets,
                handover_asset,
                add_category,
                change_category,
                delete_category,
                view_category,
                view_location,
                view_department,
            ]
        )
        self.stdout.write(
            self.style.SUCCESS("Created/updated 'Department Manager' group")
        )

        # Member group
        member, _ = Group.objects.get_or_create(name="Member")
        member.permissions.set(
            [
                view_asset,
                add_asset,
                change_asset,
                checkout_asset,
                checkin_asset,
                print_labels,
                export_assets,
                view_category,
                view_location,
                view_department,
            ]
        )
        self.stdout.write(self.style.SUCCESS("Created/updated 'Member' group"))

        # Viewer group
        viewer, _ = Group.objects.get_or_create(name="Viewer")
        viewer.permissions.set(
            [
                view_asset,
                export_assets,
                view_category,
                view_location,
                view_department,
            ]
        )
        self.stdout.write(self.style.SUCCESS("Created/updated 'Viewer' group"))

        # Borrower group (no permissions — external loan recipients)
        borrower, _ = Group.objects.get_or_create(name="Borrower")
        borrower.permissions.clear()
        self.stdout.write(
            self.style.SUCCESS("Created/updated 'Borrower' group")
        )

        self.stdout.write(
            self.style.SUCCESS("All permission groups configured.")
        )

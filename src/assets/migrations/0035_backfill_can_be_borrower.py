"""Data migration: backfill can_be_borrower permission to Borrower groups.

Finds groups with zero permissions (catches renamed Borrower groups) and
any group named "Borrower" as a safety net, then adds can_be_borrower.

KNOWN LIMITATION: The zero-permissions heuristic will add can_be_borrower
to ALL groups that have no permissions assigned, not just Borrower groups.
This is intentional for the initial deployment (where the only empty group
is the Borrower group or its renamed variants). Future deployments are
protected by setup_groups which assigns can_be_borrower explicitly.
If your deployment has other empty groups that should NOT receive this
permission, run setup_groups after this migration to correct them.
"""

from django.db import migrations


def backfill_can_be_borrower(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    # Get the can_be_borrower permission
    try:
        asset_ct = ContentType.objects.get(app_label="assets", model="asset")
        borrower_perm = Permission.objects.get(
            codename="can_be_borrower", content_type=asset_ct
        )
    except (ContentType.DoesNotExist, Permission.DoesNotExist):
        return

    # Find groups with zero permissions (likely renamed Borrower groups).
    # NOTE: This intentionally adds can_be_borrower to ALL empty groups.
    # See module docstring for rationale and limitations.
    empty_groups = Group.objects.filter(permissions__isnull=True)
    for group in empty_groups:
        group.permissions.add(borrower_perm)

    # Also find any group explicitly named "Borrower" as safety net
    for group in Group.objects.filter(name="Borrower"):
        group.permissions.add(borrower_perm)


def reverse_backfill(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    try:
        asset_ct = ContentType.objects.get(app_label="assets", model="asset")
        borrower_perm = Permission.objects.get(
            codename="can_be_borrower", content_type=asset_ct
        )
    except (ContentType.DoesNotExist, Permission.DoesNotExist):
        return

    # Remove from groups that only have this one permission
    for group in Group.objects.filter(permissions=borrower_perm):
        if group.permissions.count() == 1:
            group.permissions.remove(borrower_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0034_add_can_be_borrower_permission"),
    ]

    operations = [
        migrations.RunPython(
            backfill_can_be_borrower,
            reverse_backfill,
        ),
    ]

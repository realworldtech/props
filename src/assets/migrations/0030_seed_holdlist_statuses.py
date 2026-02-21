"""Seed default hold list statuses per spec S3.4.20."""

from django.db import migrations


def seed_statuses(apps, schema_editor):
    HoldListStatus = apps.get_model("assets", "HoldListStatus")
    statuses = [
        {
            "name": "Draft",
            "is_default": True,
            "is_terminal": False,
            "sort_order": 10,
            "color": "gray",
        },
        {
            "name": "Confirmed",
            "is_default": False,
            "is_terminal": False,
            "sort_order": 20,
            "color": "blue",
        },
        {
            "name": "In Progress",
            "is_default": False,
            "is_terminal": False,
            "sort_order": 30,
            "color": "yellow",
        },
        {
            "name": "Fulfilled",
            "is_default": False,
            "is_terminal": True,
            "sort_order": 40,
            "color": "green",
        },
        {
            "name": "Cancelled",
            "is_default": False,
            "is_terminal": True,
            "sort_order": 50,
            "color": "red",
        },
    ]
    for s in statuses:
        HoldListStatus.objects.update_or_create(
            name=s.pop("name"),
            defaults=s,
        )


def reverse_statuses(apps, schema_editor):
    HoldListStatus = apps.get_model("assets", "HoldListStatus")
    HoldListStatus.objects.filter(
        name__in=[
            "Draft",
            "Confirmed",
            "In Progress",
            "Fulfilled",
            "Cancelled",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0029_alter_printrequest_quantity"),
    ]

    operations = [
        migrations.RunPython(seed_statuses, reverse_statuses),
    ]

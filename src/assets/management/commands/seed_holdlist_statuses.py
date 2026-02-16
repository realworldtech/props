"""Seed default hold list statuses."""

from django.core.management.base import BaseCommand

from assets.models import HoldListStatus


class Command(BaseCommand):
    help = "Seed default hold list statuses"

    def handle(self, *args, **options):
        statuses = [
            {
                "name": "Draft",
                "is_default": True,
                "sort_order": 0,
                "color": "gray",
            },
            {
                "name": "Confirmed",
                "sort_order": 10,
                "color": "blue",
            },
            {
                "name": "In Progress",
                "sort_order": 20,
                "color": "yellow",
            },
            {
                "name": "Fulfilled",
                "is_terminal": True,
                "sort_order": 30,
                "color": "green",
            },
            {
                "name": "Cancelled",
                "is_terminal": True,
                "sort_order": 40,
                "color": "red",
            },
        ]
        for s in statuses:
            obj, created = HoldListStatus.objects.update_or_create(
                name=s["name"],
                defaults=s,
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"{action}: {obj.name}")

"""Management command to migrate legacy nfc_tag_id fields to NFCTag model.

Per S4.4.2.3, this command migrates any legacy nfc_tag_id values from the
Asset model into the NFCTag model. Since the nfc_tag_id field has already
been removed from Asset, this command now serves as a no-op reference for
documentation and audit purposes.
"""

from django.core.management.base import BaseCommand

from assets.models import NFCTag


class Command(BaseCommand):
    help = (
        "Migrate legacy nfc_tag_id fields from Asset to NFCTag model. "
        "No-op if migration has already been completed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be migrated without making changes.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)

        if dry_run:
            self.stdout.write(
                self.style.NOTICE(
                    "Dry run: no legacy nfc_tag_id field exists on "
                    "Asset model. Migration already completed."
                )
            )
            return

        # The nfc_tag_id field has been removed from the Asset model.
        # All NFC tags are now tracked via the NFCTag model.
        tag_count = NFCTag.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"No legacy NFC tag fields to migrate. "
                f"Migration already completed. "
                f"{tag_count} NFC tag(s) in NFCTag model."
            )
        )

"""Data migration: Set email_verified=True for all existing users (S2.15.3-03)."""

from django.db import migrations


def set_existing_users_verified(apps, schema_editor):
    CustomUser = apps.get_model("accounts", "CustomUser")
    CustomUser.objects.filter(email_verified=False).update(email_verified=True)


def reverse_set_existing_users_verified(apps, schema_editor):
    pass  # No safe reverse — cannot distinguish old vs new users


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_registration_fields"),
    ]

    operations = [
        migrations.RunPython(
            set_existing_users_verified,
            reverse_set_existing_users_verified,
        ),
    ]

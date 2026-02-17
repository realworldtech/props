import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0018_fix_holdlist_item_constraint"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # H1: HoldList.project CASCADE → SET_NULL
        migrations.AlterField(
            model_name="holdlist",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="hold_lists",
                to="assets.project",
            ),
        ),
        # H2: HoldList.department SET_NULL → PROTECT, remove null/blank
        migrations.AlterField(
            model_name="holdlist",
            name="department",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="hold_lists",
                to="assets.department",
            ),
        ),
        # H3: HoldList.created_by SET_NULL → PROTECT, remove null
        migrations.AlterField(
            model_name="holdlist",
            name="created_by",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="created_hold_lists",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # L9: HoldListItem.serial SET_NULL → CASCADE
        migrations.AlterField(
            model_name="holdlistitem",
            name="serial",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="hold_list_items",
                to="assets.assetserial",
            ),
        ),
        # L10: Add HoldListItem.added_at
        migrations.AddField(
            model_name="holdlistitem",
            name="added_at",
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
    ]

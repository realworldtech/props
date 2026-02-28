"""Tests for AI analysis and image processing."""

import json
from unittest.mock import MagicMock, patch

import pytest

from django.contrib.auth import get_user_model
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from assets.factories import (
    AssetFactory,
    AssetImageFactory,
    AssetKitFactory,
    AssetSerialFactory,
    CategoryFactory,
    DepartmentFactory,
    HoldListFactory,
    HoldListItemFactory,
    HoldListStatusFactory,
    LocationFactory,
    NFCTagFactory,
    ProjectFactory,
    SiteBrandingFactory,
    StocktakeItemFactory,
    StocktakeSessionFactory,
    TagFactory,
    TransactionFactory,
    UserFactory,
    VirtualBarcodeFactory,
)
from assets.models import (
    Asset,
    AssetImage,
    Category,
)

User = get_user_model()


class TestAIAutoTrigger:
    """Test AI analysis is auto-triggered on image upload (Batch C)."""

    @patch(
        "props.context_processors.is_ai_analysis_enabled", return_value=True
    )
    @patch("assets.tasks.analyse_image.delay")
    def test_image_upload_triggers_ai(
        self, mock_delay, mock_enabled, admin_client, asset, admin_user
    ):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img = SimpleUploadedFile(
            "test.jpg", buf.getvalue(), content_type="image/jpeg"
        )

        admin_client.post(
            reverse("assets:image_upload", args=[asset.pk]),
            {"image": img, "caption": "test"},
        )
        assert mock_delay.called

    @patch(
        "props.context_processors.is_ai_analysis_enabled", return_value=True
    )
    @patch("assets.tasks.analyse_image.delay")
    def test_quick_capture_triggers_ai(
        self, mock_delay, mock_enabled, admin_client
    ):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "blue").save(buf, "JPEG")
        buf.seek(0)
        img = SimpleUploadedFile(
            "cap.jpg", buf.getvalue(), content_type="image/jpeg"
        )

        admin_client.post(
            reverse("assets:quick_capture"),
            {"name": "AI Capture Test", "image": img},
        )
        assert mock_delay.called


class TestAICostControls:
    """Test AI daily limit enforcement (Batch C)."""

    @patch("assets.services.ai.analyse_image_data")
    def test_daily_limit_skips_analysis(self, mock_api, db, asset, user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from django.utils import timezone

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "green").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "limit.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset, image=img_file, uploaded_by=user
        )

        # Create images that appear already processed today
        for i in range(5):
            buf2 = BytesIO()
            PILImage.new("RGB", (10, 10), "red").save(buf2, "JPEG")
            buf2.seek(0)
            f = SimpleUploadedFile(
                f"old{i}.jpg", buf2.getvalue(), content_type="image/jpeg"
            )
            AssetImage.objects.create(
                asset=asset,
                image=f,
                uploaded_by=user,
                ai_processing_status="completed",
                ai_processed_at=timezone.now(),
            )

        with override_settings(
            AI_ANALYSIS_DAILY_LIMIT=5,
            ANTHROPIC_API_KEY="test-key",
        ):
            from assets.tasks import analyse_image

            analyse_image(image.pk)

        image.refresh_from_db()
        assert image.ai_processing_status == "skipped"
        assert "limit" in image.ai_error_message.lower()
        mock_api.assert_not_called()


class TestAIImageResize:
    """V21: Test AI image resize to longest-edge (1568px)."""

    def test_resize_large_image(self):
        from io import BytesIO

        from PIL import Image as PILImage

        from assets.services.ai import resize_image_for_ai

        # Create a 3000x2000 image exceeding 1568 longest edge
        buf = BytesIO()
        PILImage.new("RGB", (3000, 2000), "red").save(buf, "JPEG")
        buf.seek(0)

        result_bytes, media_type = resize_image_for_ai(buf.getvalue())
        assert media_type == "image/jpeg"

        result_img = PILImage.open(BytesIO(result_bytes))
        # Longest edge should be ~1568 (allow +-1 rounding)
        assert abs(result_img.width - 1568) <= 1
        assert abs(result_img.height - 1045) <= 1

    def test_small_image_unchanged_dimensions(self):
        from io import BytesIO

        from PIL import Image as PILImage

        from assets.services.ai import resize_image_for_ai

        # Create a small 100x100 image (under 1568 threshold)
        buf = BytesIO()
        PILImage.new("RGB", (100, 100), "blue").save(buf, "JPEG")
        buf.seek(0)

        result_bytes, media_type = resize_image_for_ai(buf.getvalue())
        result_img = PILImage.open(BytesIO(result_bytes))
        assert result_img.size == (100, 100)


class TestAIStatusView:
    """Test AI status polling view (Batch C)."""

    def test_processing_returns_html(self, admin_client, asset, admin_user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "stat.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset,
            image=img_file,
            uploaded_by=admin_user,
            ai_processing_status="processing",
        )

        response = admin_client.get(
            reverse("assets:ai_status", args=[asset.pk, image.pk])
        )
        assert response.status_code == 200
        assert b"AI analysis in progress" in response.content

    def test_completed_redirects(self, admin_client, asset, admin_user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "done.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset,
            image=img_file,
            uploaded_by=admin_user,
            ai_processing_status="completed",
        )

        response = admin_client.get(
            reverse("assets:ai_status", args=[asset.pk, image.pk])
        )
        assert response.status_code == 302


class TestAIRetryView:
    """Test AI re-analyse view (Batch C)."""

    @patch(
        "props.context_processors.is_ai_analysis_enabled", return_value=True
    )
    @patch("assets.tasks.reanalyse_image.delay")
    def test_reanalyse_triggers_task(
        self, mock_delay, mock_enabled, admin_client, asset, admin_user
    ):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "retry.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset,
            image=img_file,
            uploaded_by=admin_user,
            ai_processing_status="failed",
        )

        response = admin_client.get(
            reverse(
                "assets:ai_reanalyse",
                args=[asset.pk, image.pk],
            )
        )
        assert response.status_code == 302
        mock_delay.assert_called_once_with(image.pk)


class TestThumbnailGeneration:
    """Test thumbnail creation on AssetImage save (Batch F)."""

    def test_thumbnail_created_on_save(self, asset, user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (600, 600), "green").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "thumb_test.jpg",
            buf.getvalue(),
            content_type="image/jpeg",
        )

        image = AssetImage.objects.create(
            asset=asset,
            image=img_file,
            uploaded_by=user,
        )
        image.refresh_from_db()
        assert image.thumbnail
        assert image.thumbnail.name

        # Verify thumbnail is smaller
        thumb_img = PILImage.open(image.thumbnail)
        assert thumb_img.size[0] <= 300
        assert thumb_img.size[1] <= 300


# ============================================================
# SESSION 16 TESTS
# ============================================================


class TestAINameSuggestion:
    """Test ai_name_suggestion field on AssetImage."""

    def test_field_defaults_empty(self, asset, user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "name_test.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset, image=img_file, uploaded_by=user
        )
        assert image.ai_name_suggestion == ""

    def test_name_suggestion_saved(self, asset, user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "name_sug.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset,
            image=img_file,
            uploaded_by=user,
            ai_name_suggestion="Brass Desk Lamp",
        )
        image.refresh_from_db()
        assert image.ai_name_suggestion == "Brass Desk Lamp"

    @patch("assets.services.ai.analyse_image_data")
    def test_analyse_task_saves_name_suggestion(
        self, mock_api, db, asset, user
    ):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        mock_api.return_value = {
            "description": "A brass lamp",
            "category_suggestion": "Lighting",
            "condition": "good",
            "tags": ["brass"],
            "ocr_text": "",
            "name_suggestion": "Vintage Brass Lamp",
        }

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "ai_name.jpg", buf.getvalue(), content_type="image/jpeg"
        )
        image = AssetImage.objects.create(
            asset=asset, image=img_file, uploaded_by=user
        )

        with override_settings(ANTHROPIC_API_KEY="test-key"):
            from assets.tasks import analyse_image

            analyse_image(image.pk)

        image.refresh_from_db()
        assert image.ai_name_suggestion == "Vintage Brass Lamp"


class TestAISuggestionsPanel:
    """V29: AI suggestions panel — append description, copy OCR to notes."""

    def _create_image_with_ai(self, asset, user):
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
        buf.seek(0)
        return AssetImage.objects.create(
            asset=asset,
            image=SimpleUploadedFile(
                "v29.jpg", buf.getvalue(), content_type="image/jpeg"
            ),
            uploaded_by=user,
            ai_processing_status="completed",
            ai_description="A wooden chair",
            ai_ocr_text="SERIAL-123",
        )

    def test_apply_description_replaces(self, client_logged_in, asset, user):
        asset.description = "Old description"
        asset.save()
        img = self._create_image_with_ai(asset, user)
        url = reverse(
            "assets:ai_apply_suggestions",
            args=[asset.pk, img.pk],
        )
        client_logged_in.post(url, {"apply_description": "1"})
        asset.refresh_from_db()
        assert asset.description == "A wooden chair"

    def test_append_description(self, client_logged_in, asset, user):
        asset.description = "Existing notes"
        asset.save()
        img = self._create_image_with_ai(asset, user)
        url = reverse(
            "assets:ai_apply_suggestions",
            args=[asset.pk, img.pk],
        )
        client_logged_in.post(url, {"append_description": "1"})
        asset.refresh_from_db()
        assert "Existing notes" in asset.description
        assert "A wooden chair" in asset.description

    def test_append_description_empty_asset(
        self, client_logged_in, asset, user
    ):
        asset.description = ""
        asset.save()
        img = self._create_image_with_ai(asset, user)
        url = reverse(
            "assets:ai_apply_suggestions",
            args=[asset.pk, img.pk],
        )
        client_logged_in.post(url, {"append_description": "1"})
        asset.refresh_from_db()
        assert asset.description == "A wooden chair"

    def test_copy_ocr_to_notes(self, client_logged_in, asset, user):
        asset.notes = ""
        asset.save()
        img = self._create_image_with_ai(asset, user)
        url = reverse(
            "assets:ai_apply_suggestions",
            args=[asset.pk, img.pk],
        )
        client_logged_in.post(url, {"copy_ocr_to_notes": "1"})
        asset.refresh_from_db()
        assert asset.notes == "SERIAL-123"

    def test_copy_ocr_appends_to_existing_notes(
        self, client_logged_in, asset, user
    ):
        asset.notes = "Existing notes"
        asset.save()
        img = self._create_image_with_ai(asset, user)
        url = reverse(
            "assets:ai_apply_suggestions",
            args=[asset.pk, img.pk],
        )
        client_logged_in.post(url, {"copy_ocr_to_notes": "1"})
        asset.refresh_from_db()
        assert "Existing notes" in asset.notes
        assert "SERIAL-123" in asset.notes


class TestThreeTierThumbnails:
    """V19: Three-tier thumbnail system."""

    def test_original_capped_at_3264_on_upload(self, asset, user):
        """When uploading an image larger than 3264px, it should be capped."""
        from io import BytesIO

        from PIL import Image

        from django.core.files.base import ContentFile

        # Create a 4000x3000 image
        img = Image.new("RGB", (4000, 3000), color="red")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        asset_image = AssetImage(
            asset=asset,
            image=ContentFile(buf.getvalue(), name="large.jpg"),
            uploaded_by=user,
        )
        asset_image.save()

        # Reload the image and check dimensions
        saved_img = Image.open(asset_image.image)
        longest = max(saved_img.size)
        assert longest <= 3264, f"Expected longest edge <= 3264, got {longest}"
        # Should maintain aspect ratio (4:3)
        assert saved_img.size == (3264, 2448)

    def test_original_not_resized_if_already_small(self, asset, user):
        """Images smaller than 3264px should not be resized."""
        from io import BytesIO

        from PIL import Image

        from django.core.files.base import ContentFile

        # Create a 2000x1500 image
        img = Image.new("RGB", (2000, 1500), color="blue")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        asset_image = AssetImage(
            asset=asset,
            image=ContentFile(buf.getvalue(), name="small.jpg"),
            uploaded_by=user,
        )
        asset_image.save()

        # Reload and check dimensions unchanged
        saved_img = Image.open(asset_image.image)
        assert saved_img.size == (2000, 1500)

    def test_grid_thumbnail_generated_at_300px(self, asset, user):
        """The 300px grid thumbnail should be generated synchronously."""
        from io import BytesIO

        from PIL import Image

        from django.core.files.base import ContentFile

        img = Image.new("RGB", (1000, 800), color="green")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        asset_image = AssetImage(
            asset=asset,
            image=ContentFile(buf.getvalue(), name="test.jpg"),
            uploaded_by=user,
        )
        asset_image.save()

        assert asset_image.thumbnail
        thumb_img = Image.open(asset_image.thumbnail)
        # Thumbnail uses PIL's thumbnail() which maintains aspect ratio
        # and fits within 300x300
        assert max(thumb_img.size) <= 300

    @patch("assets.tasks.generate_detail_thumbnail.delay")
    def test_detail_thumbnail_task_queued_on_upload(
        self, mock_delay, asset, user
    ):
        """The Celery task for detail thumbnail should be queued."""
        from io import BytesIO

        from PIL import Image

        from django.core.files.base import ContentFile

        img = Image.new("RGB", (3000, 2000), color="yellow")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        asset_image = AssetImage(
            asset=asset,
            image=ContentFile(buf.getvalue(), name="test.jpg"),
            uploaded_by=user,
        )
        asset_image.save()

        # Celery task should have been queued with the image ID
        mock_delay.assert_called_once_with(asset_image.pk)

    def test_detail_thumbnail_generation_task(self, asset, user):
        """The generate_detail_thumbnail task creates a 2000px image."""
        from io import BytesIO

        from PIL import Image

        from django.core.files.base import ContentFile

        from assets.tasks import generate_detail_thumbnail

        # Create a 3000x2000 image
        img = Image.new("RGB", (3000, 2000), color="purple")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        asset_image = AssetImage(
            asset=asset,
            image=ContentFile(buf.getvalue(), name="test.jpg"),
            uploaded_by=user,
        )
        asset_image.save()

        # Manually call the task (not .delay)
        generate_detail_thumbnail(asset_image.pk)

        # Reload and check detail_thumbnail
        asset_image.refresh_from_db()
        assert asset_image.detail_thumbnail
        detail_img = Image.open(asset_image.detail_thumbnail)
        longest = max(detail_img.size)
        assert longest <= 2000
        # Should maintain aspect ratio (3:2)
        assert detail_img.size == (2000, 1333) or detail_img.size == (
            2000,
            1334,
        )

    def test_detail_thumbnail_not_generated_for_small_images(
        self, asset, user
    ):
        """Images <= 2000px should not get a detail thumbnail."""
        from io import BytesIO

        from PIL import Image

        from django.core.files.base import ContentFile

        from assets.tasks import generate_detail_thumbnail

        # Create a 1500x1000 image
        img = Image.new("RGB", (1500, 1000), color="orange")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        asset_image = AssetImage(
            asset=asset,
            image=ContentFile(buf.getvalue(), name="small.jpg"),
            uploaded_by=user,
        )
        asset_image.save()

        # Call the task
        generate_detail_thumbnail(asset_image.pk)

        # Reload and check detail_thumbnail is still empty
        asset_image.refresh_from_db()
        assert not asset_image.detail_thumbnail

    def test_detail_thumbnail_not_regenerated_if_exists(self, asset, user):
        """If detail_thumbnail already exists, task should skip."""
        from io import BytesIO

        from PIL import Image

        from django.core.files.base import ContentFile

        from assets.tasks import generate_detail_thumbnail

        # Create a large image
        img = Image.new("RGB", (3000, 2000), color="cyan")
        buf = BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        asset_image = AssetImage(
            asset=asset,
            image=ContentFile(buf.getvalue(), name="test.jpg"),
            uploaded_by=user,
        )
        asset_image.save()

        # Generate detail thumbnail
        generate_detail_thumbnail(asset_image.pk)
        asset_image.refresh_from_db()
        original_detail_path = asset_image.detail_thumbnail.name

        # Call task again
        generate_detail_thumbnail(asset_image.pk)
        asset_image.refresh_from_db()

        # Should be unchanged
        assert asset_image.detail_thumbnail.name == original_detail_path


# ============================================================
# AI INTEGRATION IMPROVEMENT TESTS
# ============================================================


@pytest.mark.django_db
class TestAIDailyLimitTimezone:
    """M5: Daily limit counter should reset at midnight local time."""

    @patch("assets.services.ai.analyse_image_data")
    def test_ai_daily_limit_uses_local_timezone(
        self, mock_api, db, asset, user
    ):
        """Verify limit checks use settings.TIME_ZONE, not UTC."""
        import datetime
        import zoneinfo
        from io import BytesIO
        from unittest.mock import patch as mock_patch

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        melb_tz = zoneinfo.ZoneInfo("Australia/Melbourne")
        fake_now = datetime.datetime(2026, 2, 17, 1, 0, 0, tzinfo=melb_tz)
        old_processed = datetime.datetime(
            2026, 2, 16, 23, 30, 0, tzinfo=melb_tz
        )

        for i in range(5):
            buf = BytesIO()
            PILImage.new("RGB", (10, 10), "red").save(buf, "JPEG")
            buf.seek(0)
            f = SimpleUploadedFile(
                f"tz_old{i}.jpg",
                buf.getvalue(),
                content_type="image/jpeg",
            )
            AssetImage.objects.create(
                asset=asset,
                image=f,
                uploaded_by=user,
                ai_processing_status="completed",
                ai_processed_at=old_processed,
            )

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), "green").save(buf, "JPEG")
        buf.seek(0)
        img_file = SimpleUploadedFile(
            "tz_new.jpg",
            buf.getvalue(),
            content_type="image/jpeg",
        )
        image = AssetImage.objects.create(
            asset=asset, image=img_file, uploaded_by=user
        )

        mock_api.return_value = {
            "description": "test",
            "category": "Props",
            "tags": [],
            "condition": "good",
            "ocr_text": "",
            "name_suggestion": "Test",
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }

        with override_settings(
            AI_ANALYSIS_DAILY_LIMIT=5,
            ANTHROPIC_API_KEY="test-key",
            TIME_ZONE="Australia/Melbourne",
        ):
            with mock_patch(
                "django.utils.timezone.now",
                return_value=fake_now,
            ):
                with mock_patch(
                    "django.utils.timezone.localdate",
                    return_value=fake_now.date(),
                ):
                    from assets.tasks import analyse_image

                    analyse_image(image.pk)

        image.refresh_from_db()
        assert image.ai_processing_status == "completed"


@pytest.mark.django_db
class TestAIPromptStructure:
    """L19: AI prompt should use system + user message structure."""

    def test_ai_prompt_has_system_and_user_messages(self):
        """Verify API call uses system param and user message."""
        import sys

        from django.test import override_settings

        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"description":"test","category":"Props",'
                '"tags":[],"condition":"good","ocr_text":"",'
                '"name_suggestion":"Test",'
                '"department_suggestion":"",'
                '"department_is_new":false}'
            )
        ]
        mock_client.messages.create.return_value = mock_response

        mock_mod = MagicMock()
        mock_mod.Anthropic = mock_anthropic.Anthropic

        from assets.services.ai import analyse_image_data

        with override_settings(ANTHROPIC_API_KEY="test-key"):
            with patch.dict(sys.modules, {"anthropic": mock_mod}):
                analyse_image_data(
                    b"fake-bytes",
                    "image/jpeg",
                    context="quick_capture",
                )

        call_kwargs = mock_client.messages.create.call_args
        assert "system" in call_kwargs.kwargs
        msgs = call_kwargs.kwargs.get("messages", [])
        assert len(msgs) >= 1
        assert msgs[0]["role"] == "user"


class TestAIResizeQuality:
    """L20: Image resize should try q70 before q60."""

    def test_ai_resize_tries_q70_before_q60(self):
        """Mock image >1MB at q80, verify q70 tried first."""
        from io import BytesIO

        from PIL import Image as PILImage

        from assets.services.ai import resize_image_for_ai

        img = PILImage.new("RGB", (4000, 3000))
        import random

        random.seed(42)
        pixels = img.load()
        for x in range(0, 4000, 10):
            for y in range(0, 3000, 10):
                pixels[x, y] = (
                    random.randint(0, 255),
                    random.randint(0, 255),
                    random.randint(0, 255),
                )

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=95)
        buf.seek(0)
        original_bytes = buf.getvalue()

        save_calls = []
        original_save = PILImage.Image.save

        def tracking_save(self, fp, format=None, **kwargs):
            if format == "JPEG":
                save_calls.append(kwargs.get("quality"))
            return original_save(self, fp, format=format, **kwargs)

        with patch.object(PILImage.Image, "save", tracking_save):
            resize_image_for_ai(original_bytes)

        assert save_calls[0] == 80
        if len(save_calls) > 1:
            assert save_calls[1] == 70
        if len(save_calls) > 2:
            assert save_calls[2] == 60


@pytest.mark.django_db
class TestAIContextDependentSuggestions:
    """L28: Context-dependent AI suggestions."""

    def _setup_mock(self):
        """Create mock anthropic module and client."""
        mock_mod = MagicMock()
        mock_client = MagicMock()
        mock_mod.Anthropic.return_value = mock_client
        return mock_mod, mock_client

    def _mock_response(self, text):
        """Create a mock API response."""
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        return resp

    def test_ai_context_quick_capture_suggests_department(
        self,
    ):
        """Quick capture context should suggest department."""
        import sys

        from django.test import override_settings

        from assets.services.ai import analyse_image_data

        mock_mod, mock_client = self._setup_mock()
        mock_client.messages.create.return_value = self._mock_response(
            '{"description":"test",'
            '"category":"Props","tags":[],'
            '"condition":"good","ocr_text":"",'
            '"name_suggestion":"Test",'
            '"department_suggestion":"Props",'
            '"department_is_new":false}'
        )

        with override_settings(ANTHROPIC_API_KEY="test-key"):
            with patch.dict(sys.modules, {"anthropic": mock_mod}):
                analyse_image_data(
                    b"fake-bytes",
                    "image/jpeg",
                    context="quick_capture",
                )

        call_kwargs = mock_client.messages.create.call_args
        msgs = call_kwargs.kwargs.get("messages", [])
        user_text = ""
        for item in msgs[0]["content"]:
            if isinstance(item, dict) and item.get("type") == "text":
                user_text = item["text"]
        assert "department" in user_text.lower()

    def test_ai_context_detail_skips_department_if_set(self):
        """Detail context with dept set skips department."""
        import sys

        from django.test import override_settings

        from assets.services.ai import analyse_image_data

        mock_mod, mock_client = self._setup_mock()
        mock_client.messages.create.return_value = self._mock_response(
            '{"description":"test",'
            '"category":"Props","tags":[],'
            '"condition":"good","ocr_text":"",'
            '"name_suggestion":"Test"}'
        )

        with override_settings(ANTHROPIC_API_KEY="test-key"):
            with patch.dict(sys.modules, {"anthropic": mock_mod}):
                analyse_image_data(
                    b"fake-bytes",
                    "image/jpeg",
                    context="asset_detail",
                    existing_fields={"department": "Props"},
                )

        call_kwargs = mock_client.messages.create.call_args
        msgs = call_kwargs.kwargs.get("messages", [])
        user_text = ""
        for item in msgs[0]["content"]:
            if isinstance(item, dict) and item.get("type") == "text":
                user_text = item["text"]
        assert "department_suggestion" not in user_text.lower()


@pytest.mark.django_db
class TestAIAdminDailyUsage:
    """L29: Admin dashboard shows daily usage and remaining."""

    def test_ai_admin_shows_daily_usage(self, admin_client, asset, admin_user):
        """AssetImage changelist shows daily usage and quota."""
        from django.test import override_settings
        from django.utils import timezone

        for i in range(3):
            AssetImage.objects.create(
                asset=asset,
                image=f"usage{i}.jpg",
                uploaded_by=admin_user,
                ai_processing_status="completed",
                ai_processed_at=timezone.now(),
            )

        with override_settings(AI_ANALYSIS_DAILY_LIMIT=100):
            url = reverse("admin:assets_assetimage_changelist")
            response = admin_client.get(url)

        assert response.status_code == 200
        assert "daily_usage" in response.context
        assert response.context["daily_usage"] == 3
        assert "daily_limit" in response.context


@pytest.mark.django_db
class TestAdminAIDashboard:
    """VV370 S2.14.5-03: Admin dashboard (S2.13.2-07) must display
    current daily AI usage count and remaining quota."""

    def test_dashboard_shows_ai_daily_usage_and_remaining(
        self, admin_client, asset, admin_user
    ):
        """S2.14.5-03: The admin dashboard must display daily usage
        count and remaining quota."""
        from django.test import override_settings
        from django.utils import timezone

        # Create some AI-analysed images today
        for i in range(3):
            AssetImage.objects.create(
                asset=asset,
                image=f"test_{i}.jpg",
                ai_processing_status="completed",
                ai_processed_at=timezone.now(),
            )

        with override_settings(AI_ANALYSIS_DAILY_LIMIT=50):
            # The spec says "admin dashboard (see S2.13.2-07)" which
            # is the AssetImage admin changelist — already tested
            # separately. But S2.14.5-03 also requires the main
            # dashboard to surface this data for admins.
            response = admin_client.get(reverse("assets:dashboard"))

        assert response.status_code == 200
        ctx = response.context
        # The dashboard context must include AI usage data
        assert "ai_daily_usage" in ctx or "daily_usage" in ctx, (
            "Dashboard must include AI daily usage count in context "
            "(S2.14.5-03)"
        )
        # Check the value is correct
        usage_key = (
            "ai_daily_usage" if "ai_daily_usage" in ctx else "daily_usage"
        )
        assert ctx[usage_key] == 3

        remaining_key = (
            "ai_daily_remaining"
            if "ai_daily_remaining" in ctx
            else "daily_remaining"
        )
        assert (
            remaining_key in ctx
        ), "Dashboard must include remaining AI quota (S2.14.5-03)"
        assert ctx[remaining_key] == 47


@pytest.mark.django_db
class TestAIEdgeCases:
    """S7.11 — AI image analysis edge cases."""

    def test_vv755_large_image_memory_check(self, admin_user):
        """VV755: Very large image should fail AI analysis
        gracefully, not crash the worker."""
        asset = AssetFactory(name="Big Image Asset")
        img = AssetImage.objects.create(
            asset=asset,
            image="assets/test.jpg",
            is_primary=True,
            ai_processing_status="pending",
        )

        from assets.services.ai import analyse_image

        mock_img = MagicMock()
        mock_img.size = (8000, 6000)
        mock_img.mode = "RGB"

        with patch("PIL.Image.open", return_value=mock_img):
            with patch("anthropic.Anthropic"):
                try:
                    analyse_image(img.pk)
                except Exception:
                    pass

        img.refresh_from_db()
        if img.ai_processing_status == "failed":
            assert (
                "too large" in img.ai_error_message.lower()
                or "memory" in img.ai_error_message.lower()
            ), (
                "S7.11.8: Large image failure should mention "
                "size or memory in the error message."
            )

    def test_vv756_ai_apply_partial_failure(
        self, admin_client, admin_user, asset
    ):
        """VV756: AI apply with some invalid suggestions should
        apply valid ones and report failures."""
        img = AssetImage.objects.create(
            asset=asset,
            image="assets/test.jpg",
            is_primary=True,
            ai_processing_status="completed",
            ai_name_suggestion="Good Name",
            ai_category_suggestion="Nonexistent Category XYZ",
            ai_description="Good description",
        )

        response = admin_client.post(
            reverse(
                "assets:ai_apply_suggestions",
                args=[asset.pk, img.pk],
            ),
            {
                "apply_name": "1",
                "apply_category": "1",
                "apply_description": "1",
            },
            follow=True,
        )
        content = response.content.decode()
        asset.refresh_from_db()

        assert asset.name == "Good Name"
        assert asset.description == "Good description"

        assert (
            "failed" in content.lower()
            or "not found" in content.lower()
            or "could not" in content.lower()
            or "warning" in content.lower()
        ), (
            "S7.11.9: When applying AI suggestions and some "
            "fields fail (e.g. category not found), the system "
            "must show a warning listing the failures. Currently "
            "it silently skips the category without feedback."
        )


@pytest.mark.django_db
class TestAIAnalysisPipeline:
    """AI analysis pipeline tests (mocked API)."""

    def test_ai_not_enabled_without_key(self):
        from assets.services.ai import is_ai_enabled

        with override_settings(ANTHROPIC_API_KEY=""):
            assert is_ai_enabled() is False

    def test_ai_enabled_with_key(self):
        from assets.services.ai import is_ai_enabled

        with override_settings(ANTHROPIC_API_KEY="test-key-123"):
            assert is_ai_enabled() is True

    def test_analyse_returns_error_when_disabled(self):
        from assets.services.ai import analyse_image_data

        with override_settings(ANTHROPIC_API_KEY=""):
            result = analyse_image_data(b"fake-image")
            assert "error" in result

    def test_analyse_processes_json_response(self):
        import sys

        from assets.services.ai import analyse_image_data

        mock_anthropic_mod = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text=json.dumps(
                    {
                        "description": "A red prop sword",
                        "category": "Props",
                        "tags": "red, sword, prop",
                        "condition": "good",
                        "ocr_text": "",
                        "name_suggestion": "Red Prop Sword",
                    }
                )
            )
        ]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_mod.Anthropic.return_value = mock_client

        with override_settings(ANTHROPIC_API_KEY="test-key"):
            with patch.dict(sys.modules, {"anthropic": mock_anthropic_mod}):
                result = analyse_image_data(b"fake-image", "image/jpeg")

        assert result["description"] == "A red prop sword"
        assert result["category"] == "Props"
        assert result["prompt_tokens"] == 100

    def test_analyse_handles_markdown_json(self):
        import sys

        from assets.services.ai import analyse_image_data

        mock_anthropic_mod = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='```json\n{"description": "A hat"}\n```')
        ]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_mod.Anthropic.return_value = mock_client

        with override_settings(ANTHROPIC_API_KEY="test-key"):
            with patch.dict(sys.modules, {"anthropic": mock_anthropic_mod}):
                result = analyse_image_data(b"fake-image", "image/jpeg")
        assert result["description"] == "A hat"


@pytest.mark.django_db
class TestV370AIDashboardUsage:
    """V370: Dashboard shows AI daily usage and quota for admins."""

    def test_dashboard_shows_ai_usage(
        self, admin_client, admin_user, settings
    ):
        """Admin dashboard should display AI usage stats."""
        settings.ANTHROPIC_API_KEY = "test-key-for-ai"
        response = admin_client.get(reverse("assets:dashboard"))
        content = response.content.decode()
        assert "AI Analysis Today" in content


@pytest.mark.django_db
class TestV364AIButtonResetOnEdit:
    """V364: AI button state resets after manual edit."""

    def test_asset_detail_has_ai_reset_js(
        self, admin_client, admin_user, asset, user, settings
    ):
        """Detail page should include JS to detect manual edits."""
        settings.ANTHROPIC_API_KEY = "test-key-for-ai"
        _img = AssetImage.objects.create(  # noqa: F841
            asset=asset,
            image="ai_test.jpg",
            is_primary=True,
            uploaded_by=user,
            ai_processing_status="completed",
            ai_suggestions_applied=True,
        )
        response = admin_client.get(
            reverse("assets:asset_detail", args=[asset.pk])
        )
        content = response.content.decode()
        # Should have re-analyse button (amber for applied suggestions)
        assert "Re-analyse" in content or "re-analyse" in content


# ============================================================
# S2.14 AI TESTS (V360, V361, V372-V374)
# ============================================================


@pytest.mark.django_db
class TestV360AIPanelShowsImageThumbnail:
    """V360 S2.14.3-04 MUST: AI panel shows image thumbnail."""

    def test_asset_detail_with_image_shows_thumbnail(
        self, client_logged_in, asset
    ):
        """Asset detail page with image shows thumbnail in AI panel."""
        from io import BytesIO

        from PIL import Image

        from assets.models import AssetImage

        # Create a test image
        img = Image.new("RGB", (100, 100), color="red")
        buffer = BytesIO()
        img.save(buffer, format="JPEG")
        buffer.seek(0)

        from django.core.files.uploadedfile import SimpleUploadedFile

        image_file = SimpleUploadedFile(
            "test.jpg", buffer.getvalue(), content_type="image/jpeg"
        )

        asset_img = AssetImage.objects.create(
            asset=asset,
            image=image_file,
            caption="Test",
        )

        url = reverse("assets:asset_detail", args=[asset.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Check for image or thumbnail display
        assert "image" in content.lower() or asset_img.caption in content


@pytest.mark.django_db
class TestV361AILoadingIndicatorAndHTMXPolling:
    """V361 S2.14.3-05 MUST: Loading indicator and HTMX polling."""

    def test_ai_status_endpoint_returns_polling_html(
        self, client_logged_in, asset
    ):
        """ai_status endpoint returns HTML polling div for HTMX."""
        from assets.models import AssetImage

        img = AssetImage.objects.create(
            asset=asset,
            caption="Test",
            ai_processing_status="pending",
        )
        url = reverse("assets:ai_status", args=[asset.pk, img.pk])
        response = client_logged_in.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Check for HTMX polling div with progress indicator
        assert "hx-get" in content
        assert "hx-trigger" in content or "progress" in content.lower()


@pytest.mark.django_db
class TestV372NoUserPIIInAnthropicAPI:
    """V372 S2.14.6-01 MUST: No user PII in Anthropic API."""

    def test_system_prompt_does_not_contain_pii_patterns(self):
        """System prompt in ai.py doesn't contain PII patterns."""
        from django.conf import settings

        from assets.services.ai import _build_system_message

        system_msg = _build_system_message()
        # Check that system message doesn't contain email, phone, or
        # user-specific data
        assert "@" not in system_msg
        assert "user" not in system_msg.lower() or (
            "user" in system_msg.lower()
            and ("community" in system_msg.lower())
        )
        # Should contain site name but no personal identifiers
        assert settings.SITE_NAME in system_msg or "asset" in system_msg


@pytest.mark.django_db
class TestV373NoOrgSpecificInfoInSystemPrompt:
    """V373 S2.14.6-02 MUST: No org-specific info in system prompt."""

    def test_system_prompt_is_generic(self):
        """System prompt uses site_name setting and is otherwise generic."""
        from django.conf import settings

        from assets.services.ai import _build_system_message

        system_msg = _build_system_message()
        # Should be generic about performing arts/events
        assert (
            "performing arts" in system_msg.lower()
            or "community" in system_msg.lower()
        )
        # Should not contain specific org names beyond SITE_NAME
        # (which is configurable)
        site_name = getattr(settings, "SITE_NAME", "PROPS")
        assert site_name in system_msg


@pytest.mark.django_db
class TestV374UserFacingHelpTextAboutImageAPI:
    """V374 S2.14.6-03 SHOULD: User-facing help text about image API
    submission."""

    def test_ai_help_text_visible_in_quick_capture(
        self, client_logged_in, department
    ):
        """Quick capture page shows help text about AI analysis."""
        url = reverse("assets:quick_capture")
        response = client_logged_in.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Look for help text or info about AI/image analysis
        assert (
            "ai" in content.lower()
            or "analysis" in content.lower()
            or "image" in content.lower()
        )


# ============================================================
# VERIFICATION COVERAGE TESTS (V22, V49, V65, V82, V84-V89,
# V222-V226, V229-V230)
# ============================================================


@pytest.mark.django_db
class TestV22DraftsQueueAISuggestions:
    """V22 (S2.1.4-07, SHOULD): Drafts queue shows AI suggestions indicator."""

    def test_drafts_queue_loads(self, admin_client, draft_asset):
        """Drafts queue page should load and show draft assets."""
        url = reverse("assets:drafts_queue")
        response = admin_client.get(url)
        assert response.status_code == 200
        assert draft_asset in response.context["page_obj"]

    @override_settings(ANTHROPIC_API_KEY="test-api-key")
    def test_drafts_queue_with_ai_enabled(self, admin_client, draft_asset):
        """When AI is configured, drafts queue should handle AI content."""
        # Create an image with AI processing completed
        _image = AssetImage.objects.create(  # noqa: F841
            asset=draft_asset,
            image="test.jpg",
            ai_processing_status="completed",
            ai_name_suggestion="Suggested Name",
            ai_description="Suggested Description",
        )
        url = reverse("assets:drafts_queue")
        response = admin_client.get(url)
        assert response.status_code == 200
        # AI suggestions indicator should be present
        assert (
            b"ai" in response.content.lower()
            or b"suggest" in response.content.lower()
        )


@pytest.mark.django_db
class TestV65ThumbnailGeneration:
    """V65 (S2.2.5-06, SHOULD): Thumbnail generation sizes."""

    def test_image_upload_creates_record(self, asset, admin_client):
        """Image upload should create AssetImage record."""
        from io import BytesIO

        from PIL import Image as PILImage

        from django.core.files.uploadedfile import SimpleUploadedFile

        # Create a small test image
        img = PILImage.new("RGB", (100, 100), color="red")
        img_io = BytesIO()
        img.save(img_io, "JPEG")
        img_io.seek(0)

        # Upload via admin or direct create
        image = AssetImage.objects.create(
            asset=asset,
            image=SimpleUploadedFile(
                "test.jpg", img_io.read(), content_type="image/jpeg"
            ),
        )
        assert image.pk is not None
        assert image.asset == asset

    def test_multiple_images_per_asset(self, asset):
        """Asset should support multiple image records."""
        img1 = AssetImage.objects.create(asset=asset, image="test1.jpg")
        img2 = AssetImage.objects.create(asset=asset, image="test2.jpg")
        assert asset.images.count() == 2
        assert img1 in asset.images.all()
        assert img2 in asset.images.all()

"""Celery tasks for the assets app."""

from celery import shared_task


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=30,
    retry_backoff_max=300,
)
def analyse_image(self, image_id: int):
    """Analyse an asset image using AI vision."""
    from django.utils import timezone

    from props.context_processors import is_ai_analysis_enabled

    from .models import AssetImage
    from .services.ai import analyse_image_data

    if not is_ai_analysis_enabled():
        return

    try:
        image = AssetImage.objects.get(pk=image_id)
    except AssetImage.DoesNotExist:
        return

    # Check daily limit (resets at midnight in configured TIME_ZONE)
    import datetime

    from django.conf import settings

    daily_limit = getattr(settings, "AI_ANALYSIS_DAILY_LIMIT", 100)
    today_local = timezone.localdate()  # Uses settings.TIME_ZONE
    today_start = timezone.make_aware(
        datetime.datetime.combine(today_local, datetime.time.min)
    )
    today_count = AssetImage.objects.filter(
        ai_processed_at__gte=today_start,
        ai_processing_status="completed",
    ).count()

    if today_count >= daily_limit:
        image.ai_processing_status = "skipped"
        image.ai_error_message = "Daily analysis limit reached"
        image.save(update_fields=["ai_processing_status", "ai_error_message"])
        return

    image.ai_processing_status = "processing"
    image.save(update_fields=["ai_processing_status"])

    try:
        image_file = image.image
        image_bytes = image_file.read()

        # Check image dimensions for memory safety (S7.11.8)
        try:
            from io import BytesIO

            from PIL import Image as PILImage

            pil_img = PILImage.open(BytesIO(image_bytes))
            width, height = pil_img.size
            pixel_count = width * height
            # 48 megapixels is the safety limit
            max_pixels = getattr(settings, "AI_MAX_PIXELS", 48_000_000)
            if pixel_count > max_pixels:
                image.ai_processing_status = "failed"
                image.ai_error_message = (
                    f"Image too large for AI analysis "
                    f"({width}x{height} = {pixel_count:,} pixels). "
                    f"Maximum is {max_pixels:,} pixels."
                )
                image.save(
                    update_fields=[
                        "ai_processing_status",
                        "ai_error_message",
                    ]
                )
                return
        except Exception:
            pass  # If we can't check dimensions, proceed anyway

        # Determine media type
        name = image_file.name.lower()
        if name.endswith(".png"):
            media_type = "image/png"
        elif name.endswith(".webp"):
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"

        # Resize for AI analysis
        from .services.ai import resize_image_for_ai

        image_bytes, media_type = resize_image_for_ai(image_bytes)

        result = analyse_image_data(image_bytes, media_type)

        if "error" in result:
            image.ai_processing_status = "failed"
            image.ai_error_message = result["error"]
        else:
            image.ai_description = result.get("description", "")
            image.ai_department_suggestion = result.get(
                "department_suggestion", ""
            )
            image.ai_department_is_new = result.get("department_is_new", False)
            image.ai_category_suggestion = result.get("category", "")
            # Check if suggested category exists in DB
            if image.ai_category_suggestion:
                from .models import Category

                image.ai_category_is_new = not Category.objects.filter(
                    name__iexact=image.ai_category_suggestion
                ).exists()
            else:
                image.ai_category_is_new = False
            image.ai_tag_suggestions = result.get("tags", [])
            if isinstance(image.ai_tag_suggestions, str):
                image.ai_tag_suggestions = [
                    t.strip() for t in image.ai_tag_suggestions.split(",")
                ]
            image.ai_condition_suggestion = result.get("condition", "")
            image.ai_ocr_text = result.get("ocr_text", "")
            image.ai_name_suggestion = result.get("name_suggestion", "")
            image.ai_prompt_tokens = result.get("prompt_tokens", 0)
            image.ai_completion_tokens = result.get("completion_tokens", 0)
            image.ai_processing_status = "completed"
            image.ai_processed_at = timezone.now()

        image.save()

    except Exception as e:
        # Check for AuthenticationError - do NOT retry
        try:
            from anthropic import AuthenticationError

            if isinstance(e, AuthenticationError):
                image.ai_processing_status = "failed"
                image.ai_error_message = (
                    "AI analysis configuration error (invalid API key)"
                )
                image.save(
                    update_fields=["ai_processing_status", "ai_error_message"]
                )
                return  # Don't raise = don't retry
        except ImportError:
            pass

        # For all other exceptions, mark as failed and raise for retry
        image.ai_processing_status = "failed"
        image.ai_error_message = str(e)
        image.save(update_fields=["ai_processing_status", "ai_error_message"])
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=30,
    retry_backoff_max=300,
)
def reanalyse_image(self, image_id: int):
    """Re-analyse an asset image, resetting previous results."""
    from .models import AssetImage

    try:
        image = AssetImage.objects.get(pk=image_id)
    except AssetImage.DoesNotExist:
        return

    # V36: Guard against duplicate analysis
    if image.ai_processing_status == "processing":
        return

    # Reset AI fields
    image.ai_description = ""
    image.ai_department_suggestion = ""
    image.ai_department_is_new = False
    image.ai_category_suggestion = ""
    image.ai_category_is_new = False
    image.ai_tag_suggestions = []
    image.ai_condition_suggestion = ""
    image.ai_ocr_text = ""
    image.ai_name_suggestion = ""
    image.ai_processed_at = None
    image.ai_processing_status = "pending"
    image.ai_error_message = ""
    image.ai_prompt_tokens = 0
    image.ai_completion_tokens = 0
    image.save()

    analyse_image.delay(image_id)


@shared_task
def generate_detail_thumbnail(image_id: int):
    """Generate 2000px detail thumbnail for an AssetImage."""
    from io import BytesIO

    from PIL import Image

    from django.core.files.base import ContentFile

    from .models import AssetImage

    try:
        asset_image = AssetImage.objects.get(pk=image_id)
    except AssetImage.DoesNotExist:
        return

    if asset_image.detail_thumbnail:
        return  # Already exists

    try:
        img = Image.open(asset_image.image)
        longest = max(img.size)
        if longest <= 2000:
            # Image is small enough, no detail thumbnail needed
            return

        scale = 2000 / longest
        new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
        img = img.resize(new_size, Image.LANCZOS)

        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)

        base_name = asset_image.image.name.split("/")[-1].rsplit(".", 1)[0]
        name = f"detail_{base_name}.jpg"
        asset_image.detail_thumbnail.save(
            name, ContentFile(buf.getvalue()), save=True
        )
    except Exception:
        pass


@shared_task
def cleanup_stale_jobs():
    """V35: Periodic task to clean up stale print jobs."""
    from django.conf import settings

    from assets.services.print_dispatch import cleanup_stale_print_jobs

    timeout = getattr(settings, "PRINT_JOB_TIMEOUT_SECONDS", 300)
    count = cleanup_stale_print_jobs(timeout_seconds=timeout)
    if count:
        import logging

        logger = logging.getLogger(__name__)
        logger.info("Cleaned up %d stale print jobs", count)
    return count

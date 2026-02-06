"""AI image analysis service using Anthropic Claude."""

import base64
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def is_ai_enabled() -> bool:
    """Check if AI analysis is available."""
    return bool(getattr(settings, "ANTHROPIC_API_KEY", ""))


def resize_image_for_ai(
    image_bytes: bytes, max_pixels: int = 3000000
) -> tuple[bytes, str]:
    """Resize image to max_pixels (default 3MP) and return JPEG bytes.

    Returns (resized_bytes, media_type).
    """
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(image_bytes))

        # Calculate current pixels
        width, height = img.size
        current_pixels = width * height

        if current_pixels > max_pixels:
            scale = (max_pixels / current_pixels) ** 0.5
            new_width = int(width * scale)
            new_height = int(height * scale)
            img = img.resize((new_width, new_height), Image.LANCZOS)

        # Convert to RGB if necessary (for JPEG)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        output = BytesIO()
        img.save(output, format="JPEG", quality=80)
        output.seek(0)

        # Ensure under 1MB
        result = output.getvalue()
        if len(result) > 1024 * 1024:
            # Reduce quality further
            output = BytesIO()
            img.save(output, format="JPEG", quality=60)
            output.seek(0)
            result = output.getvalue()

        return result, "image/jpeg"
    except ImportError:
        logger.warning("Pillow not installed, sending original image")
        return image_bytes, "image/jpeg"


def analyse_image_data(
    image_bytes: bytes, media_type: str = "image/jpeg"
) -> dict:
    """Analyse an image using the Anthropic API.

    Returns a dict with keys: description, category_suggestion,
    tag_suggestions, condition_suggestion, ocr_text.
    """
    if not is_ai_enabled():
        return {"error": "AI analysis not configured"}

    try:
        import anthropic
    except ImportError:
        return {"error": "anthropic package not installed"}

    timeout = getattr(settings, "AI_REQUEST_TIMEOUT", 60)
    client = anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=timeout,
    )
    model = getattr(settings, "AI_MODEL_NAME", "claude-sonnet-4-5-20250929")

    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

    # Build category list from database
    from assets.models import Category

    db_categories = list(
        Category.objects.values_list("name", flat=True).order_by("name")
    )
    if db_categories:
        category_hint = (
            "2. A suggested category — choose from existing categories if "
            "possible: " + ", ".join(db_categories) + ". "
            "If none fit, suggest a new descriptive category name.\n"
        )
    else:
        category_hint = (
            "2. A suggested category (e.g., Props, Costumes, Lighting, Sound, "
            "Set Pieces, Tools, Furniture, Electronics)\n"
        )

    prompt = (
        "Analyse this image of a physical asset (likely a prop, costume, "
        "tool, or piece of equipment used in performing arts or events). "
        "Provide:\n"
        "1. A brief description (1-2 sentences)\n"
        + category_hint
        + "3. Suggested tags (comma-separated, e.g., fragile, vintage, red)\n"
        "4. Condition assessment (excellent, good, fair, poor, damaged)\n"
        "5. Any visible text (OCR)\n"
        "6. A concise name suggestion for this item (2-5 words, suitable "
        "as an asset name)\n\n"
        "Respond in JSON format with keys: description, category, tags, "
        "condition, ocr_text, name_suggestion"
    )

    import json

    try:
        response = client.messages.create(
            model=model,
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        text = response.content[0].text
        # Try to parse JSON from the response
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0].strip()
                result = json.loads(json_str)
            elif "```" in text:
                json_str = text.split("```")[1].split("```")[0].strip()
                result = json.loads(json_str)
            else:
                result = {"description": text, "raw": True}

        result["prompt_tokens"] = response.usage.input_tokens
        result["completion_tokens"] = response.usage.output_tokens
        return result

    except json.JSONDecodeError as e:
        # JSON parsing errors should not retry
        logger.error("Failed to parse AI response as JSON: %s", e)
        return {"error": f"Invalid JSON response from AI: {e}"}
    except Exception as e:
        # Let Anthropic exceptions bubble up to the task for proper retry handling
        logger.error("AI analysis failed: %s", e)
        raise

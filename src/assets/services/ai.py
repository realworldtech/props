"""AI image analysis service using Anthropic Claude."""

import base64
import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def is_ai_enabled() -> bool:
    """Check if AI analysis is available."""
    return bool(getattr(settings, "ANTHROPIC_API_KEY", ""))


def resize_image_for_ai(
    image_bytes: bytes,
    max_dimension: int = None,
    max_pixels: int = 3000000,
) -> tuple[bytes, str]:
    """Resize image by longest edge and return JPEG bytes.

    If max_dimension is set, scales so longest edge <= max_dimension.
    Falls back to max_pixels for backward compatibility.
    Returns (resized_bytes, media_type).

    Quality fallback chain: q80 -> q70 -> q60 to stay under 1MB.
    """
    if max_dimension is None:
        max_dimension = getattr(settings, "AI_MAX_IMAGE_DIMENSION", 1568)
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(image_bytes))

        width, height = img.size
        longest = max(width, height)

        if longest > max_dimension:
            scale = max_dimension / longest
            new_width = int(width * scale)
            new_height = int(height * scale)
            img = img.resize((new_width, new_height), Image.LANCZOS)

        # Convert to RGB if necessary (for JPEG)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        output = BytesIO()
        img.save(output, format="JPEG", quality=80)
        output.seek(0)

        # Ensure under 1MB with progressive quality reduction
        result = output.getvalue()
        if len(result) > 1024 * 1024:
            # Try quality 70 first
            output = BytesIO()
            img.save(output, format="JPEG", quality=70)
            output.seek(0)
            result = output.getvalue()

        if len(result) > 1024 * 1024:
            # Then try quality 60
            output = BytesIO()
            img.save(output, format="JPEG", quality=60)
            output.seek(0)
            result = output.getvalue()

        return result, "image/jpeg"
    except ImportError:
        logger.warning("Pillow not installed, sending original image")
        return image_bytes, "image/jpeg"


def _build_system_message() -> str:
    """Build the system message for AI analysis."""
    site_name = getattr(settings, "SITE_NAME", "PROPS")
    return (
        f"You are an asset analysis assistant for {site_name}, "
        "a physical asset tracking system used by community "
        "organisations in performing arts and events. "
        "Your role is to analyse images of physical assets "
        "(props, costumes, tools, equipment) and provide "
        "structured metadata suggestions.\n\n"
        "Always respond in valid JSON format. Do not include "
        "markdown code fences or any text outside the JSON "
        "object. Be concise and accurate in your descriptions."
    )


def _build_prompt(
    context: str = None,
    existing_fields: dict = None,
) -> tuple[str, list[str]]:
    """Build the user prompt and expected JSON keys.

    Args:
        context: Either 'quick_capture' or 'asset_detail'.
            quick_capture: suggest department, category, name,
                description, tags.
            asset_detail: suggest category, tags, condition,
                description. Skip department if already set.
        existing_fields: Dict of field names already populated
            on the asset (e.g. {'department': 'Props'}).

    Returns:
        Tuple of (prompt_text, json_keys).
    """
    from assets.models import Category, Department

    if existing_fields is None:
        existing_fields = {}

    skip_department = (
        context == "asset_detail" and "department" in existing_fields
    )

    # Build department hint
    department_hint = ""
    department_keys = []
    if not skip_department:
        db_departments = list(
            Department.objects.filter(is_active=True)
            .values_list("name", flat=True)
            .order_by("name")
        )
        if db_departments:
            department_hint = (
                "- A suggested department — choose from existing "
                "departments if possible: " + ", ".join(db_departments) + ". "
                "If none fit, suggest a new descriptive "
                "department name. "
                "Set department_is_new to false if you chose an "
                "existing one, or true if you suggest a new "
                "name.\n"
            )
        else:
            department_hint = (
                "- A suggested department (e.g., Props, "
                "Costumes, Lighting, Sound, Staging, "
                "Administration). Set department_is_new to "
                "true.\n"
            )
        department_keys = [
            "department_suggestion",
            "department_is_new",
        ]

    # Build category hint
    db_categories = list(
        Category.objects.values_list("name", flat=True).order_by("name")
    )
    if db_categories:
        category_hint = (
            "- A suggested category — choose from existing "
            "categories if possible: " + ", ".join(db_categories) + ". "
            "If none fit, suggest a new descriptive category "
            "name.\n"
        )
    else:
        category_hint = (
            "- A suggested category (e.g., Props, Costumes, "
            "Lighting, Sound, Set Pieces, Tools, Furniture, "
            "Electronics)\n"
        )

    # Build context-specific instructions
    items = []
    json_keys = ["description"]

    items.append("- A brief description (1-2 sentences)")

    if not skip_department:
        items.append(department_hint.rstrip("\n"))

    items.append(category_hint.rstrip("\n"))
    json_keys.append("category")
    json_keys.extend(department_keys)

    items.append(
        "- Suggested tags (comma-separated, e.g., fragile, " "vintage, red)"
    )
    json_keys.append("tags")

    if context != "quick_capture":
        items.append(
            "- Condition assessment (excellent, good, fair, " "poor, damaged)"
        )
        json_keys.append("condition")

    items.append("- Any visible text (OCR)")
    json_keys.append("ocr_text")

    items.append(
        "- A concise name suggestion for this item (2-5 words, "
        "suitable as an asset name)"
    )
    json_keys.append("name_suggestion")

    prompt = (
        "Analyse this image of a physical asset (likely a prop, "
        "costume, tool, or piece of equipment used in performing "
        "arts or events). Provide:\n"
        + "\n".join(items)
        + "\n\nRespond in JSON format with keys: "
        + ", ".join(json_keys)
    )

    return prompt, json_keys


def analyse_image_data(
    image_bytes: bytes,
    media_type: str = "image/jpeg",
    context: str = None,
    existing_fields: dict = None,
) -> dict:
    """Analyse an image using the Anthropic API.

    Args:
        image_bytes: Raw image bytes.
        media_type: MIME type of the image.
        context: Either 'quick_capture' or 'asset_detail' to
            adjust what suggestions are requested.
        existing_fields: Dict of already-populated fields to
            avoid redundant suggestions.

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

    system_message = _build_system_message()
    prompt, _ = _build_prompt(
        context=context,
        existing_fields=existing_fields,
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=500,
            system=system_message,
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
        # Let Anthropic exceptions bubble up for retry
        logger.error("AI analysis failed: %s", e)
        raise


def analyse_image(image_id: int):
    """Synchronous wrapper around the Celery task for direct calls.

    Delegates to the task implementation in assets.tasks.
    """
    from assets.tasks import analyse_image as _task

    return _task(image_id)

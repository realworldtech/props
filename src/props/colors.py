"""OKLch-based colour palette generation for brand theming."""

import math

from coloraide import Color

# Chroma reduction factor for dark mode palettes
DARK_MODE_CHROMA_FACTOR = 0.75


def hex_to_oklch(hex_color: str) -> tuple[float, float, float]:
    """Convert a hex colour string to OKLch (Lightness, Chroma, Hue).

    Uses coloraide for accurate colour space conversion.
    Returns (L, C, H) where L is 0-1, C >= 0, H is 0-360.
    """
    hex_color = hex_color.strip().lstrip("#")
    hex_color = f"#{hex_color}"
    c = Color(hex_color).convert("oklch")
    return (c["lightness"], c["chroma"], c["hue"])


def _is_valid_hex(hex_color: str | None) -> bool:
    """Check if the input is a valid hex colour string."""
    if not hex_color or not isinstance(hex_color, str):
        return False
    return hex_color.startswith("#") and len(hex_color) in (4, 7)


def generate_oklch_palette(hex_color: str) -> dict[str, str]:
    """Generate CSS custom property shades (50-950) from a hex colour.

    Uses OKLch colour space for perceptually uniform lightness scaling.
    Returns a dict of shade names to hex values.
    """
    if not hex_color or not hex_color.startswith("#"):
        return {}

    try:
        base = Color(hex_color).convert("oklch")
    except Exception:
        return {}

    # Target lightness values for shade scale (OKLch L: 0-1)
    shade_lightness = {
        "50": 0.97,
        "100": 0.93,
        "200": 0.87,
        "300": 0.78,
        "400": 0.66,
        "500": 0.55,
        "600": 0.47,
        "700": 0.39,
        "800": 0.32,
        "900": 0.25,
        "950": 0.18,
    }

    palette = {}
    for shade, lightness in shade_lightness.items():
        variant = base.clone()
        variant["lightness"] = lightness
        # Clamp to sRGB gamut
        variant = variant.fit("srgb")
        hex_val = variant.convert("srgb").to_string(hex=True)
        palette[shade] = hex_val

    return palette


def generate_dark_palette(hex_color: str) -> dict[str, str]:
    """Generate dark mode shades (50-950) with reduced chroma.

    Dark mode colours have inverted lightness mapping and reduced
    chroma for comfortable viewing on dark backgrounds.
    Returns a dict of shade names to hex values.
    """
    if not hex_color or not isinstance(hex_color, str):
        return {}
    if not hex_color.startswith("#"):
        return {}

    try:
        base = Color(hex_color).convert("oklch")
    except Exception:
        return {}

    # Dark mode: lighter shades become darker, darker become lighter
    # (inverted relative to light mode) with reduced chroma
    shade_lightness = {
        "50": 0.15,
        "100": 0.20,
        "200": 0.25,
        "300": 0.32,
        "400": 0.40,
        "500": 0.48,
        "600": 0.56,
        "700": 0.65,
        "800": 0.75,
        "900": 0.85,
        "950": 0.92,
    }

    base_chroma = base["chroma"]
    if math.isnan(base_chroma):
        base_chroma = 0.0
    dark_chroma = base_chroma * DARK_MODE_CHROMA_FACTOR

    palette = {}
    for shade, lightness in shade_lightness.items():
        variant = base.clone()
        variant["lightness"] = lightness
        variant["chroma"] = dark_chroma
        variant = variant.fit("srgb")
        hex_val = variant.convert("srgb").to_string(hex=True)
        palette[shade] = hex_val

    return palette


def auto_derive_secondary(primary_hex: str) -> str:
    """Derive a secondary colour from primary via +120 degree hue shift.

    Uses OKLch colour space for perceptually meaningful hue rotation.
    Returns a hex colour string, or empty string on invalid input.
    """
    if not _is_valid_hex(primary_hex):
        return ""

    try:
        base = Color(primary_hex).convert("oklch")
    except Exception:
        return ""

    hue = base["hue"]
    if math.isnan(hue):
        hue = 0.0

    secondary = base.clone()
    secondary["hue"] = (hue + 120) % 360
    secondary["lightness"] = min(base["lightness"] * 1.05, 1.0)
    secondary = secondary.fit("srgb")
    return secondary.convert("srgb").to_string(hex=True)


def auto_derive_accent(primary_hex: str) -> str:
    """Derive an accent colour from primary via +30 degree hue shift.

    Uses an analogous colour scheme with adjusted chroma for a
    complementary but harmonious accent.
    Returns a hex colour string, or empty string on invalid input.
    """
    if not _is_valid_hex(primary_hex):
        return ""

    try:
        base = Color(primary_hex).convert("oklch")
    except Exception:
        return ""

    hue = base["hue"]
    if math.isnan(hue):
        hue = 0.0

    chroma = base["chroma"]
    if math.isnan(chroma):
        chroma = 0.0

    accent = base.clone()
    accent["hue"] = (hue + 30) % 360
    accent["chroma"] = chroma * 0.85
    accent["lightness"] = min(base["lightness"] * 1.1, 1.0)
    accent = accent.fit("srgb")
    return accent.convert("srgb").to_string(hex=True)


def generate_brand_css_properties(
    primary_hex: str = "",
    secondary_hex: str = "",
    accent_hex: str = "",
) -> str:
    """Generate CSS custom properties block for brand colours.

    The primary colour uses Tailwind's ``--color-brand-*`` naming
    convention so that Tailwind utility classes (``bg-brand-500``,
    ``text-brand-400``, etc.) pick up the values automatically.

    Secondary and accent use ``--brand-secondary-*`` /
    ``--brand-accent-*`` naming (not Tailwind theme vars).

    Dark mode palettes use a ``-dark-`` infix for each colour.
    """
    lines = []

    for name, hex_color in [
        ("primary", primary_hex),
        ("secondary", secondary_hex),
        ("accent", accent_hex),
    ]:
        if not hex_color:
            continue

        # Tailwind theme vars for primary, custom vars for others
        if name == "primary":
            prefix = "--color-brand"
        else:
            prefix = f"--brand-{name}"

        # Light mode palette
        palette = generate_oklch_palette(hex_color)
        for shade, value in palette.items():
            lines.append(f"{prefix}-{shade}: {value};")

        # Dark mode palette
        dark_palette = generate_dark_palette(hex_color)
        for shade, value in dark_palette.items():
            lines.append(f"{prefix}-dark-{shade}: {value};")

    return "\n".join(lines)

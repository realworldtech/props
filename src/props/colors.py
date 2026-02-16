"""OKLch-based colour palette generation for brand theming."""

from coloraide import Color


def hex_to_oklch(hex_color: str) -> tuple[float, float, float]:
    """Convert a hex colour string to OKLch (Lightness, Chroma, Hue).

    Uses coloraide for accurate colour space conversion.
    Returns (L, C, H) where L is 0-1, C >= 0, H is 0-360.
    """
    hex_color = hex_color.strip().lstrip("#")
    hex_color = f"#{hex_color}"
    c = Color(hex_color).convert("oklch")
    return (c["lightness"], c["chroma"], c["hue"])


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


def generate_brand_css_properties(
    primary_hex: str = "",
    secondary_hex: str = "",
    accent_hex: str = "",
) -> str:
    """Generate CSS custom properties block for brand colours.

    Returns a string like:
      --brand-primary-50: #fef2f2;
      --brand-primary-100: #fee2e2;
      ...
    """
    lines = []

    for name, hex_color in [
        ("primary", primary_hex),
        ("secondary", secondary_hex),
        ("accent", accent_hex),
    ]:
        if not hex_color:
            continue
        palette = generate_oklch_palette(hex_color)
        for shade, value in palette.items():
            lines.append(f"--brand-{name}-{shade}: {value};")

    return "\n".join(lines)

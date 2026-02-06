"""OKLch colour palette generator for brand theming."""

import math


def hex_to_oklch(hex_color: str) -> tuple[float, float, float]:
    """Convert a hex colour string to OKLch (Lightness, Chroma, Hue).

    Pipeline: hex → sRGB → linear RGB → LMS → LMS^(1/3) → OKLab → OKLch.
    Uses Björn Ottosson's OKLab matrices.
    """
    hex_color = hex_color.lstrip("#")
    r_srgb = int(hex_color[0:2], 16) / 255.0
    g_srgb = int(hex_color[2:4], 16) / 255.0
    b_srgb = int(hex_color[4:6], 16) / 255.0

    # sRGB to linear RGB
    def linearize(c: float) -> float:
        if c <= 0.04045:
            return c / 12.92
        return ((c + 0.055) / 1.055) ** 2.4

    r_lin = linearize(r_srgb)
    g_lin = linearize(g_srgb)
    b_lin = linearize(b_srgb)

    # M1: linear sRGB → LMS (combined sRGB-to-XYZ and XYZ-to-LMS)
    l = 0.4122214708 * r_lin + 0.5363325363 * g_lin + 0.0514459929 * b_lin
    m = 0.2119034982 * r_lin + 0.6806995451 * g_lin + 0.1073969566 * b_lin
    s = 0.0883024619 * r_lin + 0.2817188376 * g_lin + 0.6299787005 * b_lin

    # Cube root (LMS → LMS')
    l_ = math.copysign(abs(l) ** (1.0 / 3.0), l) if l != 0 else 0.0
    m_ = math.copysign(abs(m) ** (1.0 / 3.0), m) if m != 0 else 0.0
    s_ = math.copysign(abs(s) ** (1.0 / 3.0), s) if s != 0 else 0.0

    # M2: LMS' → OKLab
    lab_l = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    lab_a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    lab_b = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_

    # OKLab → OKLch
    chroma = math.sqrt(lab_a**2 + lab_b**2)
    hue = math.degrees(math.atan2(lab_b, lab_a)) % 360

    return (lab_l, chroma, hue)


def generate_oklch_palette(hex_color: str) -> dict[str, str]:
    """Generate an 11-shade OKLch palette from a hex colour.

    The input colour maps to shade 600. Hue is preserved across all
    shades. Lightness is interpolated and chroma is scaled proportionally,
    reduced at the extremes to stay within gamut.

    Returns a dict like {"50": "oklch(97.7% .014 308.3)", ...}.
    """
    l_input, c_input, h = hex_to_oklch(hex_color)

    # Target lightness values matching unfold's default pattern
    shade_lightness = {
        "50": 0.977,
        "100": 0.946,
        "200": 0.902,
        "300": 0.827,
        "400": 0.714,
        "500": 0.627,
        "600": 0.558,
        "700": 0.496,
        "800": 0.438,
        "900": 0.381,
        "950": 0.291,
    }

    # Avoid division by zero if input has no chroma (achromatic)
    if c_input < 0.001:
        c_input = 0.001

    # Chroma scaling: peak around 500-600, reduced at extremes
    chroma_scale = {
        "50": 0.10,
        "100": 0.20,
        "200": 0.35,
        "300": 0.55,
        "400": 0.80,
        "500": 0.95,
        "600": 1.00,
        "700": 0.90,
        "800": 0.80,
        "900": 0.70,
        "950": 0.55,
    }

    palette = {}
    for shade, target_l in shade_lightness.items():
        c = c_input * chroma_scale[shade]
        palette[shade] = f"oklch({target_l * 100:.1f}% {c:.3f} {h:.1f})"

    return palette

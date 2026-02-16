"""Context processors for site-wide template variables."""

from django.conf import settings


def is_ai_analysis_enabled():
    """Return True if AI image analysis is enabled."""
    return bool(getattr(settings, "ANTHROPIC_API_KEY", ""))


def site_settings(request):
    """Add site configuration to template context."""
    from assets.models import SiteBranding

    branding = SiteBranding.get_cached()
    logo_url = None
    if branding and branding.logo_light:
        logo_url = branding.logo_light.url

    # Generate brand CSS custom properties
    brand_css = ""
    primary = settings.BRAND_PRIMARY_COLOR
    secondary = ""
    accent = ""
    if branding:
        if branding.primary_color:
            primary = branding.primary_color
        secondary = branding.secondary_color or ""
        accent = branding.accent_color or ""

    if primary:
        from props.colors import generate_brand_css_properties

        brand_css = generate_brand_css_properties(
            primary_hex=primary,
            secondary_hex=secondary,
            accent_hex=accent,
        )

    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_SHORT_NAME": settings.SITE_SHORT_NAME,
        "BARCODE_PREFIX": settings.BARCODE_PREFIX,
        "AI_ANALYSIS_ENABLED": is_ai_analysis_enabled(),
        "brand_primary_color": primary,
        "brand_css_properties": brand_css,
        "logo_url": logo_url,
    }


def user_role(request):
    """Expose the current user's role and capability flags to templates."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {
            "user_role": "anonymous",
            "can_capture": False,
            "can_manage": False,
        }

    from assets.services.permissions import get_user_role

    role = get_user_role(user)

    # Pending approvals count for nav badge (S2.15.4-07)
    pending_approvals_count = 0
    if role == "system_admin":
        from accounts.models import CustomUser

        pending_approvals_count = CustomUser.objects.filter(
            is_active=False,
            email_verified=True,
            rejection_reason="",
        ).count()

    return {
        "user_role": role,
        "can_capture": role
        in ("system_admin", "department_manager", "member"),
        "can_manage": role in ("system_admin", "department_manager"),
        "pending_approvals_count": pending_approvals_count,
    }

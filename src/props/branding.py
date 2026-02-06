"""Branding callables for django-unfold SITE_LOGO and SITE_FAVICONS."""


def get_site_logo(request):
    """Return the URL of the site logo, or None for text fallback."""
    from assets.models import SiteBranding

    branding = SiteBranding.get_cached()
    if branding and branding.logo_light:
        return branding.logo_light.url
    return None


def get_site_favicons(request):
    """Return a list of favicon dicts for unfold, with static fallback."""
    from assets.models import SiteBranding

    branding = SiteBranding.get_cached()
    if branding and branding.favicon:
        return [{"href": branding.favicon.url}]
    return [{"href": "/static/favicon.ico"}]

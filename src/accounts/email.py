"""Branded email utility for PROPS."""

import logging

from django.conf import settings
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_branded_email(
    template_name: str,
    context: dict,
    subject: str,
    recipient: str | list[str],
) -> None:
    """Render and dispatch a branded email via Celery.

    Args:
        template_name: Template base name (e.g. "verification"). Will load
            ``emails/{template_name}.html`` and ``emails/{template_name}.txt``.
        context: Template context variables specific to this email.
        subject: Email subject line.
        recipient: Single email address or list of addresses.
    """
    from assets.models import SiteBranding

    branding = SiteBranding.get_cached()
    logo_url = None
    if branding and branding.logo_light:
        logo_url = branding.logo_light.url

    branding_context = {
        "site_name": settings.SITE_NAME,
        "brand_primary_color": settings.BRAND_PRIMARY_COLOR,
        "logo_url": logo_url,
    }
    full_context = {**branding_context, **context}

    html_body = render_to_string(f"emails/{template_name}.html", full_context)
    text_body = render_to_string(f"emails/{template_name}.txt", full_context)

    recipient_list = [recipient] if isinstance(recipient, str) else recipient

    from accounts.tasks import send_email_task

    send_email_task.delay(
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
    )

"""Branded email utility for PROPS.

Email sending is synchronous per §4.14.5-03. Volume is low (password
reset, registration notifications) so async via Celery is unnecessary
for v1.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_branded_email(
    template_name: str,
    context: dict,
    subject: str,
    recipient: str | list[str],
) -> None:
    """Render and send a branded email synchronously.

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

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipient_list,
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send()
    logger.info("Email sent: '%s' to %s", subject, recipient_list)

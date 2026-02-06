"""Celery tasks for the accounts app."""

import logging

from celery import shared_task

from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def send_email_task(
    self,
    subject: str,
    text_body: str,
    html_body: str,
    from_email: str,
    recipient_list: list[str],
) -> None:
    """Send an email with HTML and plain-text alternatives via Celery."""
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=recipient_list,
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send()
    logger.info("Email sent: '%s' to %s", subject, recipient_list)

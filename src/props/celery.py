"""Celery configuration for PROPS."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "props.settings")

app = Celery("props")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Sentry integration for Celery is auto-configured by sentry-sdk
# when the Django integration is active and SENTRY_DSN is set.
# The sentry_sdk.init() call in settings.py handles this via the
# CeleryIntegration that ships with sentry-sdk[celery].

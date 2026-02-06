"""Celery configuration for PROPS."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "props.settings")

app = Celery("props")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

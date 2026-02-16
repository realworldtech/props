"""Celery tasks for the accounts app.

Note: Email sending is synchronous per §4.14.5-03. The send_email_task
has been removed. If async email is needed in the future, re-add it here.
"""

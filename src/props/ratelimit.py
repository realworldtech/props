"""Client IP resolution for django-ratelimit.

Behind Traefik every request reaches gunicorn from the proxy container,
so REMOTE_ADDR is the same for all users and they share one rate-limit
bucket. Traefik strips client-supplied X-Forwarded-For before it adds
its own, so the first hop is trustworthy exactly when the proxy is
trusted for the scheme header too.
"""

from django.conf import settings


def client_ip(request):
    """Return the real client IP, or REMOTE_ADDR when there is no proxy."""
    if getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")

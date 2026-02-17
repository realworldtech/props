"""Project-level views for PROPS."""

import mimetypes

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, JsonResponse


def media_proxy(request, path):
    """Proxy media files from S3 storage through Django."""
    try:
        f = default_storage.open(path)
    except Exception:
        raise Http404

    content_type, _ = mimetypes.guess_type(path)
    return FileResponse(
        f, content_type=content_type or "application/octet-stream"
    )


def ratelimited_view(request, exception=None):
    """V894: Return 429 with Retry-After header on rate limit."""
    response = JsonResponse(
        {"error": "Rate limit exceeded. Please try again later."},
        status=429,
    )
    response["Retry-After"] = "60"
    return response


def health_check(request):
    """Health check endpoint for monitoring and load balancers."""
    from django.db import connection

    db_ok = True
    try:
        connection.ensure_connection()
    except Exception:
        db_ok = False

    cache_ok = True
    try:
        from django.core.cache import cache

        cache.set("_health_check", "1", timeout=10)
        cache_ok = cache.get("_health_check") == "1"
    except Exception:
        cache_ok = False

    status = "ok" if db_ok and cache_ok else "degraded"
    status_code = 200 if db_ok else 503

    return JsonResponse(
        {"status": status, "db": db_ok, "cache": cache_ok},
        status=status_code,
    )

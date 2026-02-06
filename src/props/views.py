"""Project-level views for PROPS."""

import mimetypes

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404


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

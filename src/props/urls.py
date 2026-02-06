"""URL configuration for PROPS project."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path

from props.views import media_proxy

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("", include("assets.urls")),
]

if settings.USE_S3:
    urlpatterns += [
        re_path(r"^media/(?P<path>.+)$", media_proxy, name="media_proxy"),
    ]
elif settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL, document_root=settings.MEDIA_ROOT
    )

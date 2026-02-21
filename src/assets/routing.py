"""WebSocket URL routing for the assets app."""

from django.urls import path

from assets.consumers import PrintServiceConsumer

websocket_urlpatterns = [
    path(
        "ws/print-service/",
        PrintServiceConsumer.as_asgi(),
    ),
]

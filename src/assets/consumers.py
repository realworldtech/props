"""WebSocket consumers for the print service."""

from channels.generic.websocket import JsonWebsocketConsumer


class PrintServiceConsumer(JsonWebsocketConsumer):
    """WebSocket consumer for remote print service clients.

    Handles pairing, authentication, and print job dispatch
    per §4.3.3.4 and §4.3.3.5.
    """

    def connect(self):
        self.accept()

    def disconnect(self, close_code):
        pass

    def receive_json(self, content, **kwargs):
        pass

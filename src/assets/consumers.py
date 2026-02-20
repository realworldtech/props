"""WebSocket consumers for the print service."""

import asyncio
import hashlib
import logging
import secrets

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from django.conf import settings
from django.utils import timezone

from assets.models import PrintClient

logger = logging.getLogger(__name__)

# Supported protocol versions
SUPPORTED_PROTOCOL_VERSIONS = {"1"}

# Unauthenticated connection timeout in seconds.
# Configurable via settings.PRINT_SERVICE_AUTH_TIMEOUT (default 30).
AUTH_TIMEOUT_SECONDS = getattr(settings, "PRINT_SERVICE_AUTH_TIMEOUT", 30)


class PrintServiceConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket consumer for remote print service clients.

    Handles pairing, authentication, and print job dispatch
    per §4.3.3.4 and §4.3.3.5.
    """

    async def connect(self):
        self.print_client_pk = None
        self.authenticated = False
        self.pairing_group = None
        self._timeout_handle = None
        await self.accept()
        self._schedule_auth_timeout()

    def _schedule_auth_timeout(self):
        """Schedule closing the connection after AUTH_TIMEOUT_SECONDS.

        Creates an asyncio task that sleeps then closes the
        connection if still unauthenticated.
        """
        self._timeout_handle = asyncio.ensure_future(self._auth_timeout_coro())

    async def _auth_timeout_coro(self):
        """Coroutine that waits then closes idle connections."""
        try:
            await asyncio.sleep(AUTH_TIMEOUT_SECONDS)
            if not self.authenticated and self.print_client_pk is None:
                await self.close()
        except asyncio.CancelledError:
            pass

    def _cancel_timeout(self):
        """Cancel the auth timeout if it exists."""
        if self._timeout_handle is not None:
            self._timeout_handle.cancel()
            self._timeout_handle = None

    async def disconnect(self, close_code):
        self._cancel_timeout()

        # Leave pairing group if we were in one
        if self.pairing_group:
            try:
                await self.channel_layer.group_discard(
                    self.pairing_group, self.channel_name
                )
            except Exception:
                pass

        # Update PrintClient on disconnect
        if self.print_client_pk and self.authenticated:

            @database_sync_to_async
            def update_disconnect(pk):
                try:
                    client = PrintClient.objects.get(pk=pk)
                    client.is_connected = False
                    client.last_seen_at = timezone.now()
                    client.save(update_fields=["is_connected", "last_seen_at"])
                except PrintClient.DoesNotExist:
                    pass

            await update_disconnect(self.print_client_pk)

    async def receive_json(self, content, **kwargs):
        msg_type = content.get("type")

        if msg_type == "pairing_request":
            await self._handle_pairing_request(content)
        elif msg_type == "authenticate":
            await self._handle_authenticate(content)
        else:
            await self.send_json(
                {
                    "type": "error",
                    "code": "invalid_message",
                    "message": (f"Unrecognised message type: {msg_type}"),
                }
            )

    # -----------------------------------------------------------------
    # Pairing flow
    # -----------------------------------------------------------------

    async def _handle_pairing_request(self, content):
        """Handle pairing_request message per §4.3.3.4."""
        protocol_version = content.get("protocol_version", "")
        if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            await self.send_json(
                {
                    "type": "error",
                    "code": "version_mismatch",
                    "message": (
                        f"Unsupported protocol version: "
                        f"{protocol_version}. "
                        f"Supported: "
                        f"{', '.join(SUPPORTED_PROTOCOL_VERSIONS)}"
                    ),
                }
            )
            return

        client_name = content.get("client_name", "")
        if not client_name:
            await self.send_json(
                {
                    "type": "error",
                    "code": "invalid_message",
                    "message": "client_name is required",
                }
            )
            return

        @database_sync_to_async
        def get_or_create_client(name):
            try:
                return PrintClient.objects.get(name=name, status="pending")
            except PrintClient.DoesNotExist:
                placeholder_hash = hashlib.sha256(
                    secrets.token_bytes(32)
                ).hexdigest()
                return PrintClient.objects.create(
                    name=name,
                    token_hash=placeholder_hash,
                    status="pending",
                )

        print_client = await get_or_create_client(client_name)

        self.print_client_pk = print_client.pk

        # Join the channel layer group for push notifications
        group_name = f"print_client_{print_client.pk}"
        self.pairing_group = group_name
        await self.channel_layer.group_add(group_name, self.channel_name)

        # Cancel timeout — client is in pairing flow
        self._cancel_timeout()

        await self.send_json(
            {
                "type": "pairing_pending",
                "client_id": print_client.pk,
                "message": (
                    "Pairing request received. " "Awaiting admin approval."
                ),
            }
        )

    # -----------------------------------------------------------------
    # Channel layer handlers for pairing push notifications
    # -----------------------------------------------------------------

    async def pairing_approved(self, event):
        """Handle approval notification from channel layer.

        Generate a token, store its hash, and send it to the client.
        """
        print_client_id = event.get("print_client_id", self.print_client_pk)
        if not print_client_id:
            return

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        @database_sync_to_async
        def update_token(pk, new_hash):
            try:
                pc = PrintClient.objects.get(pk=pk)
                pc.token_hash = new_hash
                pc.save(update_fields=["token_hash"])
            except PrintClient.DoesNotExist:
                pass

        await update_token(print_client_id, token_hash)

        server_name = getattr(settings, "SITE_NAME", "PROPS")

        await self.send_json(
            {
                "type": "pairing_approved",
                "token": raw_token,
                "server_name": server_name,
            }
        )

        # Leave pairing group after approval
        if self.pairing_group:
            await self.channel_layer.group_discard(
                self.pairing_group, self.channel_name
            )
            self.pairing_group = None

    async def pairing_denied(self, event):
        """Handle denial notification from channel layer."""
        await self.send_json({"type": "pairing_denied"})

        # Leave pairing group after denial
        if self.pairing_group:
            await self.channel_layer.group_discard(
                self.pairing_group, self.channel_name
            )
            self.pairing_group = None

    # -----------------------------------------------------------------
    # Authentication flow
    # -----------------------------------------------------------------

    async def _handle_authenticate(self, content):
        """Handle authenticate message per §4.3.3.4."""
        protocol_version = content.get("protocol_version", "")
        if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            await self.send_json(
                {
                    "type": "error",
                    "code": "version_mismatch",
                    "message": (
                        f"Unsupported protocol version: "
                        f"{protocol_version}. "
                        f"Supported: "
                        f"{', '.join(SUPPORTED_PROTOCOL_VERSIONS)}"
                    ),
                }
            )
            return

        raw_token = content.get("token", "")
        client_name = content.get("client_name", "")
        printers = content.get("printers", [])

        if not raw_token:
            await self.send_json(
                {
                    "type": "auth_result",
                    "success": False,
                    "message": "Token is required",
                }
            )
            await self.close()
            return

        # Hash the token and look up the client
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        @database_sync_to_async
        def lookup_client(t_hash):
            try:
                return PrintClient.objects.get(token_hash=t_hash)
            except PrintClient.DoesNotExist:
                return None

        print_client = await lookup_client(token_hash)

        if print_client is None:
            await self.send_json(
                {
                    "type": "auth_result",
                    "success": False,
                    "message": "Invalid token",
                }
            )
            await self.close()
            return

        # Check approval and active status
        if print_client.status != "approved" or not print_client.is_active:
            await self.send_json(
                {
                    "type": "auth_result",
                    "success": False,
                    "message": "Client is not approved or is inactive",
                }
            )
            await self.close()
            return

        # Single connection enforcement: if already connected,
        # close the old connection via channel layer
        if print_client.is_connected:
            conn_group = f"print_client_conn_{print_client.pk}"
            try:
                await self.channel_layer.group_send(
                    conn_group,
                    {"type": "force.disconnect"},
                )
            except Exception:
                pass

        # Cancel auth timeout
        self._cancel_timeout()

        # Token rotation: generate new token
        new_token = secrets.token_urlsafe(32)
        new_hash = hashlib.sha256(new_token.encode()).hexdigest()

        @database_sync_to_async
        def update_client(pc, n_hash, p, c_name):
            pc.token_hash = n_hash
            pc.is_connected = True
            pc.printers = p
            pc.last_seen_at = timezone.now()
            if c_name:
                pc.name = c_name
            pc.save(
                update_fields=[
                    "token_hash",
                    "is_connected",
                    "printers",
                    "last_seen_at",
                    "name",
                ]
            )

        await update_client(print_client, new_hash, printers, client_name)

        self.print_client_pk = print_client.pk
        self.authenticated = True

        # Join the connection group for single-connection enforcement
        conn_group = f"print_client_conn_{print_client.pk}"
        await self.channel_layer.group_add(conn_group, self.channel_name)

        server_name = getattr(settings, "SITE_NAME", "PROPS")

        await self.send_json(
            {
                "type": "auth_result",
                "success": True,
                "server_name": server_name,
                "new_token": new_token,
            }
        )

    # -----------------------------------------------------------------
    # Force disconnect handler (for single connection enforcement)
    # -----------------------------------------------------------------

    async def force_disconnect(self, event):
        """Close this connection — superseded by a new connection."""
        await self.close()

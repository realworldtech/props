"""WebSocket consumers for the print service."""

import asyncio
import hashlib
import logging
import secrets

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from assets.models import PrintClient, PrintRequest

logger = logging.getLogger(__name__)

# Supported protocol versions
SUPPORTED_PROTOCOL_VERSIONS = {"1", "2"}

# Unauthenticated connection timeout in seconds.
# Configurable via settings.PRINT_SERVICE_AUTH_TIMEOUT (default 30).
AUTH_TIMEOUT_SECONDS = getattr(settings, "PRINT_SERVICE_AUTH_TIMEOUT", 30)

# V20: Auth rate limit — max attempts per minute per IP
AUTH_RATE_LIMIT_MAX = 5
AUTH_RATE_LIMIT_WINDOW = 60  # seconds


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

        # §4.3.3.4-26: Reject non-TLS in production
        if getattr(settings, "SECURE_WEBSOCKET", True):
            scheme = self.scope.get("scheme", "")
            if scheme == "ws":  # not wss
                await self.close()
                return

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

    def _get_client_ip(self):
        """Extract IP address from scope for rate limiting."""
        client = self.scope.get("client")
        if client:
            return client[0]
        return "unknown"

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

        # Leave active and connection groups
        if self.print_client_pk and self.authenticated:
            # V18: Fail in-flight jobs on disconnect
            await self._fail_inflight_jobs(
                self.print_client_pk, "Connection lost"
            )

            for grp in (
                f"print_client_active_{self.print_client_pk}",
                f"print_client_conn_{self.print_client_pk}",
            ):
                try:
                    await self.channel_layer.group_discard(
                        grp, self.channel_name
                    )
                except Exception:
                    pass

            @database_sync_to_async
            def update_disconnect(pk):
                try:
                    client = PrintClient.objects.get(pk=pk)
                    client.is_connected = False
                    client.last_seen_at = timezone.now()
                    client.save(
                        update_fields=[
                            "is_connected",
                            "last_seen_at",
                        ]
                    )
                except PrintClient.DoesNotExist:
                    pass

            await update_disconnect(self.print_client_pk)

    @database_sync_to_async
    def _fail_inflight_jobs(self, client_pk, reason):
        """V18: Transition pending/sent/acked jobs to failed."""
        inflight = PrintRequest.objects.filter(
            print_client_id=client_pk,
            status__in=["pending", "sent", "acked"],
        )
        for pr in inflight:
            try:
                pr.transition_to("failed", error_message=reason)
            except Exception:
                logger.exception("Error failing in-flight job %s", pr.job_id)

    async def receive_json(self, content, **kwargs):
        msg_type = content.get("type")

        if msg_type == "pairing_request":
            await self._handle_pairing_request(content)
        elif msg_type == "authenticate":
            await self._handle_authenticate(content)
        elif msg_type in ("print_ack", "print_status"):
            if not self.authenticated:
                await self.send_json(
                    {
                        "type": "error",
                        "code": "not_authenticated",
                        "message": "Authentication required",
                    }
                )
                return
            if msg_type == "print_ack":
                await self._handle_print_ack(content)
            else:
                await self._handle_print_status(content)
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
        def get_or_create_client(name, pv):
            try:
                pc = PrintClient.objects.get(name=name, status="pending")
                if pv:
                    pc.protocol_version = pv
                    pc.save(update_fields=["protocol_version"])
                return pc
            except PrintClient.DoesNotExist:
                placeholder_hash = hashlib.sha256(
                    secrets.token_bytes(32)
                ).hexdigest()
                return PrintClient.objects.create(
                    name=name,
                    token_hash=placeholder_hash,
                    status="pending",
                    protocol_version=pv or "1",
                )

        print_client = await get_or_create_client(
            client_name, protocol_version
        )

        self.print_client_pk = print_client.pk

        # Join the channel layer group for push notifications
        group_name = f"print_client_{print_client.pk}"
        self.pairing_group = group_name
        logger = logging.getLogger("assets.consumers")
        logger.info(
            "pairing_request: joining group=%s channel=%s pk=%s",
            group_name,
            self.channel_name,
            print_client.pk,
        )
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

        Generate a token, store its hash, mark the client as connected,
        and transition the consumer to fully authenticated state so it
        can receive print jobs immediately without reconnecting.
        """
        logger = logging.getLogger("assets.consumers")
        logger.info(
            "pairing_approved: received event=%r on channel=%s",
            event,
            self.channel_name,
        )
        print_client_id = event.get("print_client_id", self.print_client_pk)
        if not print_client_id:
            logger.warning("pairing_approved: no print_client_id, ignoring")
            return

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        @database_sync_to_async
        def update_client_after_approval(pk, new_hash):
            try:
                pc = PrintClient.objects.get(pk=pk)
                pc.token_hash = new_hash
                pc.is_connected = True
                pc.last_seen_at = timezone.now()
                pc.save(
                    update_fields=[
                        "token_hash",
                        "is_connected",
                        "last_seen_at",
                    ]
                )
            except PrintClient.DoesNotExist:
                pass

        await update_client_after_approval(print_client_id, token_hash)

        # Mark consumer as authenticated so disconnect cleans up
        self.authenticated = True

        # Join active and connection groups for job dispatch
        # and single-connection enforcement
        conn_group = f"print_client_conn_{print_client_id}"
        await self.channel_layer.group_add(conn_group, self.channel_name)
        active_group = f"print_client_active_{print_client_id}"
        await self.channel_layer.group_add(active_group, self.channel_name)

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

    def _validate_printers(self, printers):
        """V24/V25/V26: Validate printer list from authenticate.

        Returns (is_valid, error_message).
        """
        if not isinstance(printers, list):
            return False, "Printers must be a list"

        # V25: Max 10 printers per client
        if len(printers) > 10:
            return False, "Maximum 10 printers allowed per client"

        seen_ids = set()
        for p in printers:
            if not isinstance(p, dict):
                return False, "Each printer must be an object"
            # V24: id and name keys required
            if "id" not in p:
                return False, "Printer missing required 'id' key"
            if "name" not in p:
                return False, "Printer missing required 'name' key"
            # V26: Unique printer ids
            pid = p["id"]
            if pid in seen_ids:
                return (
                    False,
                    f"Duplicate printer id: {pid}",
                )
            seen_ids.add(pid)

        return True, ""

    async def _check_rate_limit(self, ip):
        """V20: Rate limit auth attempts — 5/min/IP.

        Returns True if rate limited (should reject).
        Only counts failed attempts.
        """
        cache_key = f"print_auth_attempts:{ip}"

        @database_sync_to_async
        def _check():
            attempts = cache.get(cache_key, 0)
            return attempts >= AUTH_RATE_LIMIT_MAX

        return await _check()

    @database_sync_to_async
    def _increment_rate_limit(self, ip):
        """Increment failed auth attempt counter."""
        cache_key = f"print_auth_attempts:{ip}"
        attempts = cache.get(cache_key, 0)
        cache.set(
            cache_key,
            attempts + 1,
            AUTH_RATE_LIMIT_WINDOW,
        )

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

        # V20: Rate limiting
        ip = self._get_client_ip()
        if await self._check_rate_limit(ip):
            await self.send_json(
                {
                    "type": "error",
                    "code": "rate_limited",
                    "message": (
                        "Too many authentication attempts. " "Try again later."
                    ),
                }
            )
            await self.close()
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

        # V24/V25/V26: Validate printers
        valid, error_msg = self._validate_printers(printers)
        if not valid:
            await self.send_json(
                {
                    "type": "auth_result",
                    "success": False,
                    "message": error_msg,
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
            await self._increment_rate_limit(ip)
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
            await self._increment_rate_limit(ip)
            await self.send_json(
                {
                    "type": "auth_result",
                    "success": False,
                    "message": ("Client is not approved or is inactive"),
                }
            )
            await self.close()
            return

        # Single connection enforcement: if already connected,
        # close the old connection via channel layer
        if print_client.is_connected:
            # V18: Fail in-flight jobs on the old connection
            await self._fail_inflight_jobs(
                print_client.pk, "Connection superseded"
            )
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
        def update_client(pc, n_hash, p, c_name, pv):
            pc.token_hash = n_hash
            pc.is_connected = True
            pc.printers = p
            pc.last_seen_at = timezone.now()
            if c_name:
                pc.name = c_name
            if pv:
                pc.protocol_version = pv
            pc.save(
                update_fields=[
                    "token_hash",
                    "is_connected",
                    "printers",
                    "last_seen_at",
                    "name",
                    "protocol_version",
                ]
            )

        await update_client(
            print_client,
            new_hash,
            printers,
            client_name,
            protocol_version,
        )

        self.print_client_pk = print_client.pk
        self.authenticated = True

        # Join the connection group for single-connection enforcement
        conn_group = f"print_client_conn_{print_client.pk}"
        await self.channel_layer.group_add(conn_group, self.channel_name)

        # Join the active group for print job dispatch
        active_group = f"print_client_active_{print_client.pk}"
        await self.channel_layer.group_add(active_group, self.channel_name)

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

    # -----------------------------------------------------------------
    # Print job dispatch (channel layer → WebSocket)
    # -----------------------------------------------------------------

    async def print_job(self, event):
        """Handle print.job from channel layer.

        Forwards the job as a WebSocket ``print`` message and
        transitions the PrintRequest from pending to sent.
        """
        job_id = event.get("job_id")

        # Forward all fields to the client as a print message
        label_type = event.get("label_type", "asset")
        msg = {
            "type": "print",
            "job_id": job_id,
            "printer_id": event.get("printer_id", ""),
            "label_type": label_type,
            "qr_content": event.get("qr_content", ""),
            "quantity": event.get("quantity", 1),
        }

        if label_type == "location":
            msg["location_name"] = event.get("location_name", "")
            msg["location_description"] = event.get("location_description", "")
            msg["location_categories"] = event.get("location_categories", "")
            msg["location_departments"] = event.get("location_departments", "")
        else:
            msg["barcode"] = event.get("barcode", "")
            msg["asset_name"] = event.get("asset_name", "")[:30]
            msg["category_name"] = event.get("category_name", "")
            msg["department_name"] = event.get("department_name", "")

        # Pass through optional fields
        if "site_short_name" in event:
            msg["site_short_name"] = event["site_short_name"]

        await self.send_json(msg)

        # Transition PrintRequest to sent
        @database_sync_to_async
        def mark_sent(j_id):
            try:
                pr = PrintRequest.objects.get(job_id=j_id)
                pr.transition_to("sent")
            except PrintRequest.DoesNotExist:
                logger.warning(
                    "PrintRequest %s not found for mark_sent",
                    j_id,
                )
            except Exception:
                logger.exception(
                    "Error transitioning PrintRequest %s to sent",
                    j_id,
                )

        await mark_sent(job_id)

    # -----------------------------------------------------------------
    # Print ack / status from client (WebSocket → model update)
    # -----------------------------------------------------------------

    async def _handle_print_ack(self, content):
        """Handle print_ack from authenticated client."""
        job_id = content.get("job_id")
        if not job_id:
            return

        @database_sync_to_async
        def ack_job(j_id):
            try:
                pr = PrintRequest.objects.get(job_id=j_id)
                pr.transition_to("acked")
            except PrintRequest.DoesNotExist:
                logger.warning("print_ack for unknown job_id %s", j_id)
            except Exception:
                logger.exception("Error handling print_ack for %s", j_id)

        await ack_job(job_id)

    async def _handle_print_status(self, content):
        """Handle print_status from authenticated client."""
        job_id = content.get("job_id")
        status = content.get("status")
        error = content.get("error")
        if not job_id or not status:
            return

        @database_sync_to_async
        def update_status(j_id, new_status, err):
            try:
                pr = PrintRequest.objects.get(job_id=j_id)
                error_msg = err if err else ""
                pr.transition_to(new_status, error_message=error_msg)
            except PrintRequest.DoesNotExist:
                logger.warning(
                    "print_status for unknown job_id %s",
                    j_id,
                )
            except Exception:
                logger.exception(
                    "Error handling print_status for %s",
                    j_id,
                )

        await update_status(job_id, status, error)

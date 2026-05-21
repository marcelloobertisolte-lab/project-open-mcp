"""Pass-through HTTP authentication for the streamable-HTTP transport.

The MCP endpoint expects HTTP Basic credentials. Those credentials are NOT
checked against a local store: they are validated against Project-Open itself
(delegated auth) and then reused as the credentials for every REST call made
while serving that request. This gives per-user ]po[ permissions and means the
MCP server holds no service account of its own.

Implemented as a *pure ASGI* middleware (not Starlette ``BaseHTTPMiddleware``)
so the credentials contextvar set here reliably propagates to the tool
coroutines downstream.
"""

from __future__ import annotations

import base64
import binascii
import contextvars
import logging
import time

from .client import ClientConfig, ProjectOpenClient

logger = logging.getLogger(__name__)

# Holds the (username, password) tuple for the request currently being served.
current_credentials: contextvars.ContextVar[tuple[str, str] | None] = (
    contextvars.ContextVar("po_credentials", default=None)
)

# Simple in-memory TTL cache of validated credentials so we do not hit ]po[ on
# every JSON-RPC message. Keyed by username; stores (password, expires_at).
_VALIDATION_TTL_SECONDS = 60.0
_validation_cache: dict[str, tuple[str, float]] = {}


def parse_basic_auth(header_value: str | None) -> tuple[str, str] | None:
    """Extract (username, password) from an HTTP Basic ``Authorization`` header."""
    if not header_value:
        return None
    scheme, _, encoded = header_value.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


async def validate_credentials(creds: tuple[str, str]) -> bool:
    """Validate credentials against ]po[, with a short-lived cache."""
    username, password = creds
    cached = _validation_cache.get(username)
    now = time.monotonic()
    if cached and cached[0] == password and cached[1] > now:
        logger.debug("auth cache hit for user=%s", username)
        return True

    config = ClientConfig.from_env(username=username, password=password)
    async with ProjectOpenClient(config) as client:
        ok = await client.validate()

    if ok:
        _validation_cache[username] = (password, now + _VALIDATION_TTL_SECONDS)
        logger.info("auth ok for user=%s", username)
    else:
        _validation_cache.pop(username, None)
        logger.warning("auth FAILED for user=%s", username)
    return ok


async def _send_401(send, message: str) -> None:
    body = (
        b'{"error": "unauthorized", "message": "' + message.encode("utf-8") + b'"}'
    )
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Basic realm="Project-Open MCP"'),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class PassThroughAuthMiddleware:
    """Pure ASGI middleware enforcing ]po[-delegated Basic auth on HTTP requests."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        creds = parse_basic_auth(
            headers.get(b"authorization", b"").decode("latin-1") or None
        )
        if creds is None:
            logger.warning(
                "401 missing/malformed Basic auth for %s %s",
                scope.get("method"),
                scope.get("path"),
            )
            await _send_401(send, "Missing or malformed Basic credentials.")
            return
        if not await validate_credentials(creds):
            await _send_401(send, "Invalid Project-Open credentials.")
            return

        token = current_credentials.set(creds)
        try:
            await self.app(scope, receive, send)
        finally:
            current_credentials.reset(token)

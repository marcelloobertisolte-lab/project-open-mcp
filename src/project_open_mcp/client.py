"""HTTP client for the Project-Open ]po[ ``intranet-rest`` package.

The remote API exposes ~40 object types under ``/intranet-rest/<object_type>``
and ``/intranet-rest/<object_type>/<object_id>``, using HTTP Basic Auth. This
instance returns JSON (``format=json``) wrapped in an envelope of the form
``{"success": true, "total": N, "message": "...", "data": [...]}``. This module
wraps those endpoints with a small async client and unwraps the envelope so
MCP tools get the payload directly.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ProjectOpenError(RuntimeError):
    """Raised when the ]po[ REST endpoint returns an error or invalid body."""


@dataclass(frozen=True)
class ClientConfig:
    base_url: str
    username: str
    password: str
    timeout: float = 30.0
    verify_tls: bool = True

    @classmethod
    def from_env(
        cls,
        *,
        username: str | None = None,
        password: str | None = None,
    ) -> "ClientConfig":
        """Build a config from environment variables.

        ``base_url``/``timeout``/``verify_tls`` always come from the environment.
        Credentials default to ``PO_USERNAME``/``PO_PASSWORD`` but can be
        overridden per request (HTTP pass-through auth).
        """
        base_url = os.environ.get("PO_BASE_URL", "").rstrip("/")
        if not base_url:
            raise ProjectOpenError("PO_BASE_URL must be set.")
        username = username if username is not None else os.environ.get("PO_USERNAME", "")
        password = password if password is not None else os.environ.get("PO_PASSWORD", "")
        if not username or not password:
            raise ProjectOpenError(
                "Project-Open credentials missing (set PO_USERNAME/PO_PASSWORD "
                "or provide them via the request Authorization header)."
            )
        return cls(
            base_url=base_url,
            username=username,
            password=password,
            timeout=float(os.environ.get("PO_TIMEOUT", "30")),
            verify_tls=os.environ.get("PO_VERIFY_TLS", "true").lower() != "false",
        )


def _strip_html(body: str) -> str:
    """Reduce an OpenACS HTML error page to its readable text."""
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class ProjectOpenClient:
    """Async wrapper around the ]po[ ``intranet-rest`` endpoints."""

    def __init__(self, config: ClientConfig | None = None) -> None:
        self._config = config or ClientConfig.from_env()
        self._client = httpx.AsyncClient(
            base_url=f"{self._config.base_url}/intranet-rest",
            auth=(self._config.username, self._config.password),
            timeout=self._config.timeout,
            verify=self._config.verify_tls,
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ProjectOpenClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        merged_params = {"format": "json"}
        if params:
            merged_params.update({k: v for k, v in params.items() if v is not None})
        logger.info(
            "REST %s %s params=%s%s",
            method,
            path,
            merged_params,
            " body=" + str(json_body) if json_body is not None else "",
        )
        try:
            response = await self._client.request(
                method,
                path,
                params=merged_params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            logger.error("REST %s %s transport error: %s", method, path, exc)
            raise ProjectOpenError(f"HTTP error contacting Project-Open: {exc}") from exc

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            snippet = _strip_html(response.text)[:400]
            logger.error(
                "REST %s %s -> HTTP %s non-JSON: %s",
                method,
                path,
                response.status_code,
                snippet,
            )
            raise ProjectOpenError(
                f"Project-Open returned HTTP {response.status_code} "
                f"(non-JSON): {snippet}"
            )

        payload = response.json()
        if isinstance(payload, dict) and payload.get("success") is False:
            message = payload.get("message", "unknown error")
            logger.warning(
                "REST %s %s -> success=false: %s", method, path, message
            )
            raise ProjectOpenError(f"Project-Open error: {message}")
        if response.status_code >= 400:
            logger.error(
                "REST %s %s -> HTTP %s: %s", method, path, response.status_code, payload
            )
            raise ProjectOpenError(
                f"Project-Open returned HTTP {response.status_code}: {payload}"
            )
        total = payload.get("total") if isinstance(payload, dict) else None
        logger.info(
            "REST %s %s -> HTTP %s success total=%s",
            method,
            path,
            response.status_code,
            total,
        )
        return payload

    async def list_objects(
        self,
        object_type: str,
        *,
        filters: dict[str, Any] | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """List objects of ``object_type``.

        ``filters`` are individual ``col=value`` params (each becomes a raw SQL
        equality, so values are NOT auto-quoted). ``query`` is a full SQL
        where-clause fragment (e.g. ``day >= '2026-01-01'``) — the right tool for
        dates, ranges, and reaching beyond the row cap by narrowing the set.
        ``limit`` caps rows; ]po[ sets ``total`` to the rows *returned*, and
        results come oldest-first, so without a narrowing ``query`` a capped
        response never reaches recent rows.
        """
        params = dict(filters or {})
        if query:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        return await self._request("GET", f"/{object_type}", params=params)

    async def validate(self) -> bool:
        """Return True if the configured credentials authenticate against ]po[.

        ]po[ returns HTTP 200 with ``success: false`` on bad/missing auth, so we
        issue a tiny authenticated read and inspect the envelope.
        """
        try:
            payload = await self._request(
                "GET", "/im_company", params={"limit": 1}
            )
        except ProjectOpenError:
            return False
        return isinstance(payload, dict) and payload.get("success") is True

    async def get_object(self, object_type: str, object_id: int | str) -> Any:
        """Fetch a single object by its id."""
        return await self._request("GET", f"/{object_type}/{object_id}")

    async def create_object(
        self,
        object_type: str,
        attributes: dict[str, Any],
    ) -> Any:
        """Create a new object of ``object_type`` with the given attributes."""
        return await self._request("POST", f"/{object_type}", json_body=attributes)

    async def update_object(
        self,
        object_type: str,
        object_id: int | str,
        attributes: dict[str, Any],
    ) -> Any:
        """Update an existing object."""
        return await self._request(
            "POST",
            f"/{object_type}/{object_id}",
            json_body=attributes,
        )

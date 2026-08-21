"""Async low-level client for the myGEKKO Query API.

Covers the read side used by Phase 0: resource discovery (``/var``) and current
values (``.../status``). Write (``scmd/set``) is intentionally NOT implemented
here yet — it arrives in Phase 1 behind the write gate.

Design notes (see docs/query-api-notes.md):
- The credential is injected as query params on every request and is redacted in
  all log output.
- A ``status`` query *subscribes* the resource; the first response may be empty.
  We therefore "prime -> wait ~2s -> re-poll" until values appear or attempts run
  out (configurable).
- HTTP status codes carry meaning (410 = bad creds, 429 = rate limit,
  444 = not found / not writable / offline). We map them to ``GekkoApiError``.
"""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType
from typing import Any

import httpx

logger = logging.getLogger("mygekko_mcp.client")

# Human-readable meaning per documented status code.
_STATUS_MEANING: dict[int, str] = {
    400: "Bad Request — likely a syntax/path error",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    410: "Gone — username, password or key is wrong",
    429: "Too Many Requests — rate/burst limit reached",
    444: "No Response — resource not found, read-only, offline, or wrong gekkoid",
}

# Query-param keys that carry secrets; masked before logging a URL.
_SECRET_PARAMS = ("key", "password")


class GekkoApiError(RuntimeError):
    """Raised when the Query API returns a non-200 status or is unreachable."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _redact_url(url: httpx.URL | str) -> str:
    """Return a string URL with secret query params masked."""
    u = httpx.URL(str(url))
    mask = {k: "***" for k in _SECRET_PARAMS if k in u.params}
    if mask:
        u = u.copy_merge_params(mask)
    return str(u)


def _has_values(payload: Any) -> bool:
    """True if the payload contains at least one non-empty ``value`` anywhere.

    Used as the "primed" predicate for status re-polling.
    """
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k == "value" and isinstance(v, (str, list)) and v not in ("", []):
                return True
            if _has_values(v):
                return True
    elif isinstance(payload, list):
        return any(_has_values(item) for item in payload)
    return False


class QueryApiClient:
    """Thin async wrapper around the myGEKKO Query API.

    Use as an async context manager::

        async with QueryApiClient(base_url, auth_params, ...) as api:
            tree = await api.get_var()
            state = await api.get_status("lights")
    """

    def __init__(
        self,
        base_url: str,
        auth_params: dict[str, str],
        *,
        timeout_seconds: float = 10.0,
        repoll_delay_seconds: float = 2.0,
        repoll_max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth_params
        self._repoll_delay = repoll_delay_seconds
        self._repoll_max = max(1, repoll_max_attempts)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Accept": "application/json"},
        )

    # --- lifecycle ---------------------------------------------------------
    async def __aenter__(self) -> QueryApiClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # --- core request ------------------------------------------------------
    async def _get(self, path: str, extra_params: dict[str, str] | None = None) -> httpx.Response:
        url = f"{self._base_url}/{path.strip('/')}"
        params = {**self._auth, **(extra_params or {})}
        try:
            resp = await self._client.get(url, params=params)
        except httpx.HTTPError as e:  # connect/timeout/etc.
            logger.error("Request failed: %s (%s)", _redact_url(f"{url}"), e.__class__.__name__)
            raise GekkoApiError(f"HTTP transport error: {e.__class__.__name__}") from e

        if resp.status_code != 200:
            meaning = _STATUS_MEANING.get(resp.status_code, "Unexpected status")
            logger.warning("Query API %s for %s", resp.status_code, _redact_url(resp.request.url))
            raise GekkoApiError(
                f"Query API returned {resp.status_code}: {meaning}",
                status_code=resp.status_code,
            )
        return resp

    def _json(self, resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError as e:
            text = resp.text.strip()
            # Successful writes return the literal "OK"; reads should be JSON.
            raise GekkoApiError(f"Expected JSON, got: {text[:80]!r}") from e

    # --- public reads ------------------------------------------------------
    async def get_var(self, path: str = "") -> dict[str, Any]:
        """Discovery: return the resource tree at ``var/<path>`` (default: root)."""
        sub = f"var/{path.strip('/')}" if path else "var"
        resp = await self._get(sub)
        return self._json(resp)

    async def get_status(self, path: str = "", *, as_array: bool = False) -> dict[str, Any]:
        """Read current values under ``var/<path>/status``.

        Applies the prime -> re-poll strategy: because the first status query only
        subscribes the resource, we retry (after ``repoll_delay`` seconds) until at
        least one value appears or ``repoll_max_attempts`` is reached.
        """
        sub = f"var/{path.strip('/')}/status" if path else "var/status"
        extra = {"format": "array"} if as_array else None

        payload: dict[str, Any] = {}
        for attempt in range(1, self._repoll_max + 1):
            resp = await self._get(sub, extra)
            payload = self._json(resp)
            if _has_values(payload) or attempt == self._repoll_max:
                if attempt > 1:
                    logger.debug("Status primed for %r on attempt %d", path or "/", attempt)
                return payload
            logger.debug(
                "Status for %r empty on attempt %d, re-polling in %.1fs",
                path or "/",
                attempt,
                self._repoll_delay,
            )
            await asyncio.sleep(self._repoll_delay)
        return payload

    # --- public write ------------------------------------------------------
    async def set_value(self, system: str, resource_id: str, value: str) -> str:
        """Write a command via ``var/<system>/<resource_id>/scmd/set?value=<value>``.

        Returns the raw response text — the API replies with ``OK`` on success.
        This is the ONLY write path; callers MUST gate it through ``WritePolicy``
        and validate ``value`` against the item's advertised command grammar.
        """
        sub = f"var/{system.strip('/')}/{resource_id.strip('/')}/scmd/set"
        resp = await self._get(sub, {"value": value})
        return resp.text.strip()

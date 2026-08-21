"""Client for GEKKO BRAIN's own history export (NOT the live myGEKKO controller).

GET {base_url}/history and GET {base_url}/event-summary, bearer-token authenticated with the
SAME gkmcp_... token that resolved the caller's Credentials (see credentials.py's raw_token and
tenant_auth.py's TokenResolver). Sibling to tenant_auth.py: injectable httpx.AsyncClient for
tests, no network in unit tests, never logs the token.
"""

from __future__ import annotations

import logging

import httpx

from mygekko_mcp.security import AsyncRateLimiter

logger = logging.getLogger("mygekko_mcp.history_client")

_STATUS_MESSAGES = {
    401: "token invalid or revoked",
    403: "history export not enabled for this tenant — enable it in GEKKO BRAIN Settings",
    409: "no active controller for this tenant",
    429: "rate limited by GEKKO BRAIN, retry later",
}


class HistoryError(RuntimeError):
    """Raised on any failure resolving/fetching history. Never includes the token."""


class BrainHistoryClient:
    """Reads GEKKO BRAIN's accumulated tsdb/events history for one tenant per call."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))
        self._owns_client = client is None
        self._rl = rate_limiter or AsyncRateLimiter(rate_per_sec=1.0, burst=5)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, token: str, params: dict[str, object]) -> dict:
        await self._rl.acquire()
        try:
            resp = await self._client.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:  # network/timeout — never leak the token
            raise HistoryError(f"history request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            msg = _STATUS_MESSAGES.get(resp.status_code, f"unexpected HTTP {resp.status_code}")
            raise HistoryError(msg)
        return resp.json()

    async def get_series(self, token: str, *, metric: str, from_ms: int, to_ms: int) -> dict:
        """Historical time-series for one metric. See GEKKO BRAIN's mcp-history.mjs for shape."""
        return await self._get("/history", token, {"metric": metric, "from": from_ms, "to": to_ms})

    async def get_event_summary(
        self, token: str, *, category: str, from_ms: int, to_ms: int
    ) -> dict:
        """Event counts/history for one category. Sensitive categories are always day-counts."""
        return await self._get(
            "/event-summary", token, {"category": category, "from": from_ms, "to": to_ms}
        )

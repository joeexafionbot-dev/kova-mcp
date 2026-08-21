"""Seamless per-tenant token auth: resolve a GEKKO BRAIN token → myGEKKO creds.

The hosted client presents ONE opaque per-tenant token as ``Authorization: Bearer
<gkmcp_…>``. This resolver POSTs ``{token}`` to GEKKO BRAIN's internal
``/api/mcp-auth/resolve`` endpoint (guarded by a shared secret) and gets back
``{username, key, gekkoid}`` — so the user never re-enters credentials.

Resolved identities are cached briefly (TTL) so we don't hit the app on every
tool call; the TTL bounds how long a revoked/rotated token keeps working. The
httpx client and clock are injectable for tests (no network).
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable

import httpx

from mygekko_mcp.credentials import CredentialError, Credentials, credentials_from_resolve

logger = logging.getLogger("mygekko_mcp.tenant_auth")


class TokenResolver:
    """Resolve per-tenant bearer tokens to :class:`Credentials`, with a TTL cache."""

    def __init__(
        self,
        resolve_url: str,
        secret: str,
        *,
        ttl: float = 300.0,
        cache_max: int = 512,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        if not resolve_url or not secret:
            raise ValueError("TokenResolver requires resolve_url and secret")
        self._url = resolve_url
        self._secret = secret
        self._ttl = max(0.0, ttl)
        self._cache_max = max(1, cache_max)
        self._now = now or time.monotonic
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))
        self._owns_client = client is None
        self._cache: OrderedDict[str, tuple[float, Credentials]] = OrderedDict()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _cached(self, token: str) -> Credentials | None:
        hit = self._cache.get(token)
        if hit is None:
            return None
        expiry, creds = hit
        if self._now() >= expiry:
            self._cache.pop(token, None)
            return None
        self._cache.move_to_end(token)
        return creds

    def _store(self, token: str, creds: Credentials) -> None:
        self._cache[token] = (self._now() + self._ttl, creds)
        self._cache.move_to_end(token)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)  # evict least-recently-used

    async def resolve(self, token: str) -> Credentials:
        """Return the tenant's credentials for ``token``. Raises CredentialError on failure."""
        token = (token or "").strip()
        if not token:
            raise CredentialError("no bearer token presented")
        cached = self._cached(token)
        if cached is not None:
            return cached
        try:
            resp = await self._client.post(
                self._url,
                json={"token": token},
                headers={"X-Internal-Secret": self._secret},
            )
        except httpx.HTTPError as exc:  # network/timeout — never leak the token
            raise CredentialError(f"token resolve failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            # 404 = unknown/revoked token; 401 = bad shared secret; 409 = no REST controller
            raise CredentialError(f"token not accepted (HTTP {resp.status_code})")
        creds = credentials_from_resolve(resp.json(), raw_token=token)
        self._store(token, creds)
        return creds

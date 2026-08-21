"""Multi-tenant backend router (Stage 2: hosted LibreChat).

One process serves many users. This router implements the :class:`Backend`
protocol but owns no state of its own — on every call it resolves the *current
request's* credentials and delegates to a per-tenant :class:`RestBackend`, one
per distinct credential fingerprint. Inventory/state caches therefore stay
isolated per user, and a request can only ever address the gekkoid in its own
headers.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from mygekko_mcp.backends.rest import RestBackend
from mygekko_mcp.config import Settings
from mygekko_mcp.credentials import CredentialProvider, StaticCredentialProvider
from mygekko_mcp.model.inventory import Inventory, InventoryItem

logger = logging.getLogger("mygekko_mcp.tenant")


class TenantBackendRouter:
    """Routes each call to the RestBackend for the current request's credentials."""

    def __init__(self, credentials: CredentialProvider, settings: Settings) -> None:
        self._credentials = credentials
        self._settings = settings
        self._max = max(1, settings.tenant_cache_max)
        self._backends: OrderedDict[str, RestBackend] = OrderedDict()
        self._lock = asyncio.Lock()

    async def _current(self) -> RestBackend:
        """Get (or create) the backend for the caller's credentials."""
        creds = self._credentials.get()  # raises CredentialError if absent
        key = creds.fingerprint()
        async with self._lock:
            backend = self._backends.get(key)
            if backend is not None:
                self._backends.move_to_end(key)  # LRU touch
                return backend
            backend = RestBackend(StaticCredentialProvider(creds), self._settings)
            self._backends[key] = backend
            logger.info(
                "New tenant backend (%s) for gekkoid %s; %d cached",
                key,
                creds.gekkoid,
                len(self._backends),
            )
            evicted = None
            if len(self._backends) > self._max:
                _, evicted = self._backends.popitem(last=False)  # drop least-recent
        if evicted is not None:
            await evicted.aclose()
        return backend

    # --- Backend protocol (all delegate to the current tenant) -------------
    async def get_inventory(self, *, refresh: bool = False) -> Inventory:
        return await (await self._current()).get_inventory(refresh=refresh)

    async def find_item(self, path: str) -> InventoryItem | None:
        return await (await self._current()).find_item(path)

    async def read_system(self, system: str) -> dict[str, dict[str, Any]]:
        return await (await self._current()).read_system(system)

    async def read_item(self, path: str) -> dict[str, Any] | None:
        return await (await self._current()).read_item(path)

    async def write(self, system: str, resource_id: str, token: str) -> str:
        return await (await self._current()).write(system, resource_id, token)

    async def aclose(self) -> None:
        async with self._lock:
            backends = list(self._backends.values())
            self._backends.clear()
        for b in backends:
            await b.aclose()

"""REST backend: implements :class:`Backend` over the Query API client.

Holds an inventory cache (the ``/var`` tree changes rarely) and decodes live
status values via each item's parsed field specs. All requests pass through a
rate limiter to stay under the Plus burst limit.
"""

from __future__ import annotations

import logging
from typing import Any

from mygekko_mcp.client import QueryApiClient
from mygekko_mcp.config import Settings
from mygekko_mcp.credentials import CredentialProvider
from mygekko_mcp.model.capabilities import counts_line, unhandled_writable_systems
from mygekko_mcp.model.inventory import Inventory, InventoryItem, build_inventory
from mygekko_mcp.model.semantics import interpret_value
from mygekko_mcp.security import AsyncRateLimiter

logger = logging.getLogger("mygekko_mcp.backend")


class RestBackend:
    """Backend backed by the myGEKKO REST Query API."""

    def __init__(
        self,
        credentials: CredentialProvider,
        settings: Settings,
        *,
        client: QueryApiClient | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self._credentials = credentials
        self._settings = settings
        self._client = client
        self._rl = rate_limiter or AsyncRateLimiter()
        self._inventory: Inventory | None = None
        self._index: dict[str, InventoryItem] = {}

    # --- client lifecycle --------------------------------------------------
    def _get_client(self) -> QueryApiClient:
        if self._client is None:
            creds = self._credentials.get()
            logger.info("Connecting to myGEKKO: %s", creds.redacted())
            self._client = QueryApiClient(
                creds.base_url,
                creds.auth_params,
                timeout_seconds=self._settings.timeout_seconds,
                repoll_delay_seconds=self._settings.repoll_delay_seconds,
                repoll_max_attempts=self._settings.repoll_max_attempts,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- inventory ---------------------------------------------------------
    async def get_inventory(self, *, refresh: bool = False) -> Inventory:
        if self._inventory is None or refresh:
            client = self._get_client()
            await self._rl.acquire()
            tree = await client.get_var()
            self._inventory = build_inventory(tree)
            self._index = {it.path: it for s in self._inventory.systems for it in s.all_entries}
            logger.info(
                "Inventory loaded: %d systems, %d resources",
                len(self._inventory.systems),
                len(self._index),
            )
            # Discovery: report what this specific controller can do, and flag
            # writable systems reachable only via generic control_device.
            logger.info("Capability discovery: %s", counts_line(self._inventory))
            unhandled = unhandled_writable_systems(self._inventory)
            if unhandled:
                logger.info(
                    "Generic-only writable systems (no typed tool): %s",
                    ", ".join(unhandled),
                )
        return self._inventory

    async def find_item(self, path: str) -> InventoryItem | None:
        await self.get_inventory()
        return self._index.get(path)

    # --- reads -------------------------------------------------------------
    async def read_system(self, system: str) -> dict[str, dict[str, Any]]:
        inv = await self.get_inventory()
        group = inv.system_map().get(system)
        if group is None:
            return {}
        client = self._get_client()
        await self._rl.acquire()
        resp = await client.get_status(system)
        # A branch query (var/lights/status) returns items at the top level;
        # a root query nests them under the system key. Handle both.
        if not isinstance(resp, dict):
            branch = {}
        elif system in resp and isinstance(resp[system], dict):
            branch = resp[system]
        else:
            branch = resp
        out: dict[str, dict[str, Any]] = {}
        for it in group.all_entries:
            node = branch.get(it.resource_id, {})
            value = (node.get("sumstate", {}) or {}).get("value")
            if value is None or not it.read_fields:
                continue
            out[it.path] = interpret_value(it.read_fields, value)
        return out

    async def read_item(self, path: str) -> dict[str, Any] | None:
        system = path.split("/", 1)[0]
        states = await self.read_system(system)
        return states.get(path)

    # --- write (mechanism only; policy is enforced by the caller) ----------
    async def write(self, system: str, resource_id: str, token: str) -> str:
        client = self._get_client()
        await self._rl.acquire()
        return await client.set_value(system, resource_id, token)

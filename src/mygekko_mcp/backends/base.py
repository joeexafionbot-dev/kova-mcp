"""Backend protocol: the swappable device/state layer.

Resources and tools depend only on this interface, never on the REST client
directly — so a future ``mqtt`` backend drops in without touching them.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from mygekko_mcp.model.inventory import Inventory, InventoryItem


@runtime_checkable
class Backend(Protocol):
    """A source of device inventory + live state, and a gated write path."""

    async def get_inventory(self, *, refresh: bool = False) -> Inventory:
        """Return the (cached) device inventory, optionally forcing a refresh."""
        ...

    async def find_item(self, path: str) -> InventoryItem | None:
        """Look up a single item/group by its ``system/resource_id`` path."""
        ...

    async def read_system(self, system: str) -> dict[str, dict[str, Any]]:
        """Return decoded live states for every item in a system, keyed by path."""
        ...

    async def read_item(self, path: str) -> dict[str, Any] | None:
        """Return the decoded live state for a single item path."""
        ...

    async def write(self, system: str, resource_id: str, token: str) -> str:
        """Execute a raw ``scmd`` write. Callers MUST gate this via WritePolicy."""
        ...

    async def aclose(self) -> None:
        """Release any underlying resources (HTTP client)."""
        ...

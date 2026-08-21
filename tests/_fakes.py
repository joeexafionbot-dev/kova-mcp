"""Shared test doubles."""

from __future__ import annotations

from typing import Any

from mygekko_mcp.model.inventory import Inventory


class FakeBackend:
    """In-memory Backend implementation for offline tests."""

    def __init__(
        self,
        inv: Inventory,
        states: dict[str, dict[str, Any]],
        *,
        write_result: str = "OK",
    ) -> None:
        self._inv = inv
        self._index = {it.path: it for s in inv.systems for it in s.all_entries}
        self._states = states
        self._write_result = write_result
        self.writes: list[tuple[str, str, str]] = []

    async def get_inventory(self, *, refresh: bool = False) -> Inventory:
        return self._inv

    async def find_item(self, path: str):
        return self._index.get(path)

    async def read_system(self, system: str) -> dict[str, dict[str, Any]]:
        return {p: s for p, s in self._states.items() if p.startswith(f"{system}/")}

    async def read_item(self, path: str):
        return self._states.get(path)

    async def write(self, system: str, resource_id: str, token: str) -> str:
        self.writes.append((system, resource_id, token))
        return self._write_result

    async def aclose(self) -> None:
        pass

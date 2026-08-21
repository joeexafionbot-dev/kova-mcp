"""Turn the raw ``/api/v1/var`` discovery tree into a typed device inventory.

Fully data-driven: every system/item/group present in the tree becomes an entry.
Nothing is hardcoded per gewerk, so a new installation yields new entries without
code changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mygekko_mcp.model.semantics import (
    CommandSpec,
    FieldSpec,
    parse_scmd_format,
    parse_sumstate_format,
)

# Keys under a system that are not addressable items/groups.
_META_KEYS = {
    "sumstate",
    "scmd",
    "name",
    "page",
    "description",
    "format",
    "type",
    "permission",
    "index",
}


@dataclass
class InventoryItem:
    """A single addressable resource (item or group) within a system."""

    system: str
    resource_id: str  # e.g. "item0", "group3"
    kind: str  # "item" | "group"
    name: str
    page: str = ""
    index: int | None = None
    read_fields: list[FieldSpec] = field(default_factory=list)
    commands: list[CommandSpec] = field(default_factory=list)

    @property
    def path(self) -> str:
        """REST path fragment under /var, e.g. ``lights/item0``."""
        return f"{self.system}/{self.resource_id}"

    @property
    def writable(self) -> bool:
        return bool(self.commands)


@dataclass
class SystemGroup:
    """All items/groups belonging to one system key (e.g. ``lights``)."""

    key: str
    items: list[InventoryItem] = field(default_factory=list)
    groups: list[InventoryItem] = field(default_factory=list)

    @property
    def all_entries(self) -> list[InventoryItem]:
        return [*self.items, *self.groups]


@dataclass
class Inventory:
    """Complete parsed inventory of a myGEKKO installation."""

    systems: list[SystemGroup] = field(default_factory=list)
    globals_vars: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    # --- summaries ---
    def system_map(self) -> dict[str, SystemGroup]:
        return {s.key: s for s in self.systems}

    def counts(self) -> dict[str, int]:
        return {s.key: len(s.all_entries) for s in self.systems if s.all_entries}

    def writable_items(self) -> list[InventoryItem]:
        return [it for s in self.systems for it in s.all_entries if it.writable]


def _parse_entry(system: str, resource_id: str, node: dict[str, Any]) -> InventoryItem:
    kind = "group" if resource_id.startswith("group") else "item"
    sumstate = node.get("sumstate") or {}
    scmd = node.get("scmd") or {}
    index = scmd.get("index") if isinstance(scmd, dict) else None
    return InventoryItem(
        system=system,
        resource_id=resource_id,
        kind=kind,
        name=str(node.get("name", resource_id)),
        page=str(node.get("page", "")),
        index=index if isinstance(index, int) else None,
        read_fields=parse_sumstate_format(
            sumstate.get("format") if isinstance(sumstate, dict) else None
        ),
        commands=parse_scmd_format(scmd.get("format") if isinstance(scmd, dict) else None),
    )


def build_inventory(var_tree: dict[str, Any]) -> Inventory:
    """Build an :class:`Inventory` from a raw ``/var`` (root) response."""
    inv = Inventory(raw=var_tree)

    for system_key, system_node in var_tree.items():
        if not isinstance(system_node, dict):
            continue

        # `globals` is structurally different: nested groups of leaf variables.
        if system_key == "globals":
            inv.globals_vars = _flatten_globals(system_node)
            continue

        group = SystemGroup(key=system_key)
        for res_id, node in system_node.items():
            if res_id in _META_KEYS or not isinstance(node, dict):
                continue
            if res_id.startswith("item"):
                group.items.append(_parse_entry(system_key, res_id, node))
            elif res_id.startswith("group"):
                group.groups.append(_parse_entry(system_key, res_id, node))
        if group.all_entries:
            inv.systems.append(group)

    inv.systems.sort(key=lambda s: s.key)
    return inv


def _flatten_globals(globals_node: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Collect leaf variables under globals (network/meteo/alarm/...) as read-only."""
    out: dict[str, dict[str, Any]] = {}
    for group_name, group_node in globals_node.items():
        if not isinstance(group_node, dict):
            continue
        for var_name, var_node in group_node.items():
            if not isinstance(var_node, dict):
                continue
            # A leaf variable has "format"; a "sumstate" wrapper also carries it.
            leaf = var_node.get("sumstate", var_node)
            if isinstance(leaf, dict) and "format" in leaf:
                out[f"{group_name}/{var_name}"] = {
                    "description": leaf.get("description", ""),
                    "format": leaf.get("format", ""),
                    "unit": _unit_from_format(leaf.get("format", "")),
                    "permission": leaf.get("permission", "READ"),
                }
    return out


def _unit_from_format(fmt: str) -> str:
    fields = parse_sumstate_format(fmt) if fmt else []
    return fields[0].unit if fields else ""

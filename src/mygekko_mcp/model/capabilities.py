"""Controller capability discovery.

Every myGEKKO installation is configured differently — different systems, item
counts, device models, and even different command grammars per item. Instead of
hardcoding what a house *should* have, we read what THIS controller actually
advertises in ``/api/v1/var`` and decode each writable item's ``scmd`` grammar
into a human/LLM-readable list of available commands.

This is what makes the server work unchanged across controllers: a system we
have never special-cased is still discoverable and controllable via its
advertised grammar (see ``commands.build_generic_command`` / the
``control_device`` tool).
"""

from __future__ import annotations

import re
from typing import Any

from mygekko_mcp.model.commands import has_builder
from mygekko_mcp.model.inventory import Inventory, InventoryItem
from mygekko_mcp.model.semantics import CommandSpec

# system key -> the dedicated typed tool that handles it (nicer UX than generic).
# Kept in sync with commands._BUILDERS by test_capabilities.
TYPED_TOOLS: dict[str, str] = {
    "lights": "control_light",
    "blinds": "control_blind",
    "loads": "control_load",
    "roomtemps": "control_room_temp",
    "hotwater_systems": "control_hotwater",
    "heatingcircuits": "control_heating_circuit",
    "vents": "control_vent",
    "pools": "control_pool",
    "actions": "control_action",
}

_UNIT_RE = re.compile(r"\[([^\]]+)\]")


def _unit_from_label(label: str) -> str:
    """Pull a unit/range hint out of a label, e.g. 'Temperature[°C]' -> '°C'."""
    m = _UNIT_RE.search(label or "")
    return m.group(1).strip() if m else ""


def describe_command(spec: CommandSpec) -> dict[str, Any]:
    """One advertised command as a discovery descriptor."""
    d: dict[str, Any] = {
        "token": spec.token,
        "label": spec.label,
        "type": "value" if spec.takes_arg else "literal",
    }
    if spec.takes_arg:
        d["send"] = f"{spec.prefix}<value>"
        unit = _unit_from_label(spec.label)
        if unit:
            d["unit"] = unit
    return d


def describe_item(item: InventoryItem) -> dict[str, Any]:
    """A writable item plus the commands it accepts (for discovery)."""
    return {
        "path": item.path,
        "name": item.name,
        "system": item.system,
        "page": item.page,
        "typed_tool": TYPED_TOOLS.get(item.system),
        "commands": [describe_command(c) for c in item.commands],
    }


def _control_kind(system: str, has_writable: bool) -> str:
    if system in TYPED_TOOLS:
        return "typed"
    if has_writable:
        return "generic"
    return "read-only"


def summarize_capabilities(inv: Inventory, *, system: str | None = None) -> dict[str, Any]:
    """Decode the whole inventory into a capability map for the caller.

    Returns an overview of every system (how many items, how many writable, how
    to control them) plus, per controllable system, the decoded command grammar
    of each writable item.
    """
    overview: list[dict[str, Any]] = []
    controllable: dict[str, list[dict[str, Any]]] = {}

    groups = inv.systems if system is None else [g for g in inv.systems if g.key == system]
    for g in groups:
        writable = [it for it in g.all_entries if it.writable]
        overview.append(
            {
                "system": g.key,
                "items": len(g.all_entries),
                "writable": len(writable),
                "control": _control_kind(g.key, bool(writable)),
                "typed_tool": TYPED_TOOLS.get(g.key),
            }
        )
        if writable:
            controllable[g.key] = [describe_item(it) for it in writable]

    typed = sum(1 for o in overview if o["control"] == "typed")
    generic = sum(1 for o in overview if o["control"] == "generic")
    readonly = sum(1 for o in overview if o["control"] == "read-only")
    return {
        "summary": {
            "systems": len(overview),
            "typed": typed,
            "generic": generic,
            "read_only": readonly,
            "note": (
                "Prefer the typed control_* tool when present; otherwise use "
                "control_device with an advertised command token/label."
            ),
        },
        "systems": overview,
        "controllable": controllable,
    }


def counts_line(inv: Inventory) -> str:
    """A compact one-line discovery summary for startup logs."""
    s = summarize_capabilities(inv)["summary"]
    return (
        f"{s['systems']} systems discovered: {s['typed']} typed, "
        f"{s['generic']} generic-controllable, {s['read_only']} read-only"
    )


def unhandled_writable_systems(inv: Inventory) -> list[str]:
    """Writable systems with no dedicated builder — reachable only generically."""
    return sorted(
        g.key
        for g in inv.systems
        if any(it.writable for it in g.all_entries) and not has_builder(g.key)
    )

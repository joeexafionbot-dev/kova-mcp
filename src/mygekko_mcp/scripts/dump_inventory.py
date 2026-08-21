"""Phase-0 deliverable: connect to a myGEKKO and dump its device inventory.

READ-ONLY. This never issues a ``scmd/set`` write. It fetches the ``/api/v1/var``
discovery tree, parses it into a typed inventory + value semantics, writes two
JSON files and prints a human-readable summary.

Usage::

    uv run dump-inventory                 # discovery only (structure + semantics)
    uv run dump-inventory --status        # also read current live values
    uv run dump-inventory --out-dir .     # where to write the JSON files
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mygekko_mcp.client import GekkoApiError, QueryApiClient
from mygekko_mcp.config import Settings, load_settings
from mygekko_mcp.model import InventoryItem, build_inventory
from mygekko_mcp.model.semantics import interpret_value


def _item_to_dict(it: InventoryItem, live: dict[str, Any] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "resource_id": it.resource_id,
        "kind": it.kind,
        "name": it.name,
        "page": it.page,
        "path": it.path,
        "index": it.index,
        "writable": it.writable,
        "read_fields": [asdict(f) for f in it.read_fields],
        "commands": [{**asdict(c), "syntax": c.describe()} for c in it.commands],
    }
    if live is not None:
        d["live"] = live
    return d


async def _run(settings: Settings, want_status: bool, out_dir: Path) -> int:
    async with QueryApiClient(
        settings.base_url,
        settings.auth_params(),
        timeout_seconds=settings.timeout_seconds,
        repoll_delay_seconds=settings.repoll_delay_seconds,
        repoll_max_attempts=settings.repoll_max_attempts,
    ) as api:
        logging.info("Fetching /var discovery tree ...")
        var_tree = await api.get_var()
        inv = build_inventory(var_tree)

        live_by_path: dict[str, dict[str, Any]] = {}
        if want_status:
            live_by_path = await _fetch_status(api, inv)

    # --- write JSON artifacts ---
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "inventory.raw.json"
    norm_path = out_dir / "inventory.json"
    raw_path.write_text(json.dumps(var_tree, indent=2, ensure_ascii=False), encoding="utf-8")

    normalized = {
        "mode": settings.mode.value,
        "systems": {
            s.key: {
                "items": [_item_to_dict(it, live_by_path.get(it.path)) for it in s.items],
                "groups": [_item_to_dict(it, live_by_path.get(it.path)) for it in s.groups],
            }
            for s in inv.systems
        },
        "globals": inv.globals_vars,
    }
    norm_path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")

    _print_summary(settings, inv, live_by_path, raw_path, norm_path)
    return 0


async def _fetch_status(api: QueryApiClient, inv: Any) -> dict[str, dict[str, Any]]:
    """Read live values per system branch and decode them via field specs."""
    live: dict[str, dict[str, Any]] = {}
    for system in inv.systems:
        try:
            resp = await api.get_status(system.key)
        except GekkoApiError as e:
            logging.warning("Status for %s failed: %s", system.key, e)
            continue
        # Branch queries return items at top level; root queries nest them.
        if not isinstance(resp, dict):
            branch = {}
        elif system.key in resp and isinstance(resp[system.key], dict):
            branch = resp[system.key]
        else:
            branch = resp
        for it in system.all_entries:
            node = branch.get(it.resource_id, {})
            value = (node.get("sumstate", {}) or {}).get("value")
            if value is None or not it.read_fields:
                continue
            live[it.path] = interpret_value(it.read_fields, value)
    return live


def _print_summary(
    settings: Settings,
    inv: Any,
    live: dict[str, dict[str, Any]],
    raw_path: Path,
    norm_path: Path,
) -> None:
    print()
    print("=" * 68)
    print("  myGEKKO device inventory")
    print("=" * 68)
    for k, v in settings.redacted().items():
        print(f"  {k:>14}: {v}")
    print("-" * 68)

    counts = inv.counts()
    total_items = sum(counts.values())
    writable = inv.writable_items()
    print(
        f"  Systems: {len(counts)}   Resources: {total_items}   "
        f"Writable: {len(writable)}   Globals vars: {len(inv.globals_vars)}"
    )
    print("-" * 68)
    print(f"  {'system':<22}{'items':>7}{'groups':>8}{'writable':>10}")
    for s in inv.systems:
        w = sum(1 for it in s.all_entries if it.writable)
        print(f"  {s.key:<22}{len(s.items):>7}{len(s.groups):>8}{w:>10}")
    print("-" * 68)

    # Show one sample entry per system so the value semantics are visible.
    print("  Sample resource per system (read fields / write commands):")
    for s in inv.systems:
        if not s.all_entries:
            continue
        it = s.all_entries[0]
        print(f'\n  > {s.key} / {it.resource_id}  "{it.name}"  (page: {it.page or "-"})')
        if it.read_fields:
            fnames = ", ".join(f.name for f in it.read_fields)
            print(f"      read : {fnames}")
        if it.commands:
            cmds = " | ".join(c.describe() for c in it.commands)
            print(f"      write: {cmds}")
        if it.path in live:
            decoded = {k: v["value"] for k, v in live[it.path].items()}
            print(f"      live : {decoded}")

    print()
    print("-" * 68)
    print(f"  Wrote raw tree   -> {raw_path}")
    print(f"  Wrote inventory  -> {norm_path}")
    if not live:
        print("  (run again with --status to also read current live values)")
    print("=" * 68)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump the myGEKKO device inventory (read-only).")
    parser.add_argument(
        "--status", action="store_true", help="also read current live values (subscribes resources)"
    )
    parser.add_argument("--out-dir", default=".", help="directory for inventory JSON files")
    args = parser.parse_args()

    # myGEKKO data contains non-ASCII units (°C, €, ...). Force UTF-8 stdout so
    # it prints on Windows consoles (cp1252) without UnicodeEncodeError.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")

    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs the full request URL (incl. the secret key= param) at INFO.
    # Silence it so credentials never land in logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    problems = settings.validate_ready()
    if problems:
        print("Cannot connect — configuration incomplete:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nFill these in your .env (copy from .env.example). For cloud mode:\n"
            "  GEKKO_USERNAME = your myGEKKO Plus email\n"
            "  GEKKO_KEY      = Query API key (Settings > Globus > Plus Erweitert >\n"
            "                   Plus Query API > Verwaltung > Neu generieren)\n"
            "  GEKKO_GEKKOID  = Systeminfo > myGEKKO ID (XXXX-XXXX-XXXX-XXXX)",
            file=sys.stderr,
        )
        return 2

    try:
        return asyncio.run(_run(settings, args.status, Path(args.out_dir)))
    except GekkoApiError as e:
        print(f"\nQuery API error: {e}", file=sys.stderr)
        if e.status_code == 410:
            print("  -> Check GEKKO_USERNAME / GEKKO_KEY / GEKKO_GEKKOID.", file=sys.stderr)
        elif e.status_code == 429:
            print("  -> Rate limit hit; wait a moment and retry.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

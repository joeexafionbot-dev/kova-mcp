"""Application service: the read/control logic behind the MCP tools.

Kept separate from the MCP wiring so it can be tested without a transport.
This is the single choke point where the write gate, dry-run and audit are
enforced — the MCP tools are thin wrappers over these methods.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from mygekko_mcp.backends.base import Backend
from mygekko_mcp.model.capabilities import summarize_capabilities
from mygekko_mcp.model.commands import CommandError, build_command, build_generic_command
from mygekko_mcp.security import AuditLog, WritePolicy

logger = logging.getLogger("mygekko_mcp.service")


@dataclass(frozen=True)
class ControlResult:
    """Outcome of a control() call, shaped for an LLM to read back."""

    ok: bool
    executed: bool
    token: str | None
    description: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class GekkoService:
    def __init__(self, backend: Backend, policy: WritePolicy, audit: AuditLog) -> None:
        self._backend = backend
        self._policy = policy
        self._audit = audit

    # --- reads -------------------------------------------------------------
    async def list_devices(self, system: str | None = None) -> dict[str, Any]:
        inv = await self._backend.get_inventory()
        groups = inv.systems if system is None else [g for g in inv.systems if g.key == system]
        return {
            "systems": {
                g.key: [
                    {
                        "path": it.path,
                        "name": it.name,
                        "page": it.page,
                        "kind": it.kind,
                        "writable": it.writable,
                        "reads": [f.name for f in it.read_fields],
                        "commands": [c.token for c in it.commands],
                    }
                    for it in g.all_entries
                ]
                for g in groups
            },
            "globals": sorted(inv.globals_vars),
        }

    async def get_state(self, path: str) -> dict[str, Any]:
        item = await self._backend.find_item(path)
        if item is None:
            raise KeyError(f"unknown device path '{path}'")
        state = await self._backend.read_item(path)
        return {
            "path": item.path,
            "name": item.name,
            "system": item.system,
            "writable": item.writable,
            "state": {k: v["value"] for k, v in (state or {}).items()},
            "units": {k: v["unit"] for k, v in (state or {}).items() if v.get("unit")},
        }

    # --- control (gated) ---------------------------------------------------
    async def control(
        self,
        path: str,
        action: str,
        *,
        dry_run: bool = False,
        confirm: bool = False,
        level: float | None = None,
        position: float | None = None,
        angle: float | None = None,
        setpoint: float | None = None,
        mode: str | None = None,
        delta: float | None = None,
    ) -> ControlResult:
        item = await self._backend.find_item(path)
        if item is None:
            return ControlResult(False, False, None, "", f"Unknown device path '{path}'")

        params: dict[str, Any] = {}
        if level is not None:
            params["level"] = level
        if position is not None:
            params["position"] = position
        if angle is not None:
            params["angle"] = angle
        if setpoint is not None:
            params["setpoint"] = setpoint
        if mode is not None:
            params["mode"] = mode
        if delta is not None:
            params["delta"] = delta

        try:
            built = build_command(item, action, **params)
        except CommandError as e:
            return ControlResult(False, False, None, "", f"Invalid command: {e}")

        return await self._gate_and_send(item, built, dry_run=dry_run, confirm=confirm)

    async def control_generic(
        self,
        path: str,
        command: str,
        *,
        value: float | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> ControlResult:
        """Controller-agnostic control: send any command the device advertises.

        Same safety gate as :meth:`control` (deny-list, allow-list, confirm for
        critical systems) — only the command is derived generically from the
        item's grammar instead of a dedicated typed builder.
        """
        item = await self._backend.find_item(path)
        if item is None:
            return ControlResult(False, False, None, "", f"Unknown device path '{path}'")
        try:
            built = build_generic_command(item, command, value=value)
        except CommandError as e:
            return ControlResult(False, False, None, "", f"Invalid command: {e}")
        return await self._gate_and_send(item, built, dry_run=dry_run, confirm=confirm)

    async def describe_capabilities(self, system: str | None = None) -> dict[str, Any]:
        """Discover what THIS controller exposes: systems + decoded command grammar."""
        inv = await self._backend.get_inventory()
        return summarize_capabilities(inv, system=system)

    # --- shared write path -------------------------------------------------
    async def _gate_and_send(
        self, item: Any, built: Any, *, dry_run: bool, confirm: bool
    ) -> ControlResult:
        decision = self._policy.check(item.system)
        if decision.blocked:
            self._log(built, dry_run, f"blocked: {decision.reason}")
            return ControlResult(
                False, False, built.token, built.description, f"Blocked: {decision.reason}"
            )

        if dry_run:
            self._log(built, True, "dry-run")
            return ControlResult(
                True,
                False,
                built.token,
                built.description,
                f"Dry-run: would send '{built.token}' to {built.path} "
                f"({built.description}). Re-call with dry_run=false to execute.",
            )

        if decision.requires_confirm and not confirm:
            self._log(built, False, "needs-confirm")
            return ControlResult(
                True,
                False,
                built.token,
                built.description,
                f"Safety-critical action on {built.path}. Re-call with confirm=true to "
                f"execute '{built.token}' ({built.description}).",
            )

        result = await self._backend.write(built.system, built.resource_id, built.token)
        # The client raises on any non-200, so reaching here means the API
        # accepted the write. The success *body* differs by backend: local
        # replies "OK", but the cloud Query API replies with "{}" (or empty).
        # Treat those as success; surface anything else verbatim so an
        # unexpected error body stays visible.
        raw = (result or "").strip()
        ok = raw.upper().startswith("OK") or raw in ("", "{}")
        shown = raw or "OK"
        self._log(built, False, shown)
        return ControlResult(
            ok,
            True,
            built.token,
            built.description,
            f"Sent '{built.token}' to {built.path} ({built.description}): {shown}",
        )

    def _log(self, built: Any, dry_run: bool, result: str) -> None:
        self._audit.record(
            action="write",
            system=built.system,
            item=built.path,
            command=built.token,
            dry_run=dry_run,
            result=result,
        )

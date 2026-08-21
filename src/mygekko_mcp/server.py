"""FastMCP server wiring: resources (read-only) + tools (read + gated control).

Write tools are only registered when ``GEKKO_ALLOW_WRITE=true`` — with writes
disabled (the default) the server exposes no control surface at all.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from mcp.server.fastmcp import FastMCP

from mygekko_mcp.backends.base import Backend
from mygekko_mcp.config import Settings
from mygekko_mcp.credentials import CredentialError, RequestCredentialProvider
from mygekko_mcp.history_client import BrainHistoryClient, HistoryError
from mygekko_mcp.security import AuditLog, WritePolicy
from mygekko_mcp.service import GekkoService

logger = logging.getLogger("mygekko_mcp.server")


def _dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _parse_range(from_: str | None, to: str | None) -> tuple[int, int]:
    """ISO8601 (or bare date) strings -> (from_ms, to_ms). Defaults to the last 7 days."""

    def parse_one(value: str) -> datetime:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    to_dt = parse_one(to) if to else datetime.now(UTC)
    from_dt = parse_one(from_) if from_ else (to_dt - timedelta(days=7))
    return int(from_dt.timestamp() * 1000), int(to_dt.timestamp() * 1000)


def create_server(backend: Backend, settings: Settings) -> FastMCP:
    """Build a configured FastMCP server around a backend."""
    policy = WritePolicy(settings)
    audit = AuditLog(settings.audit_path)
    service = GekkoService(backend, policy, audit)

    mcp = FastMCP("myGEKKO")

    # --- resources (read-only) --------------------------------------------
    @mcp.resource("gekko://inventory")
    async def inventory_resource() -> str:
        """Full device inventory: systems, items, paths, and capabilities."""
        return _dumps(await service.list_devices())

    @mcp.resource("gekko://system/{system}")
    async def system_state_resource(system: str) -> str:
        """Live decoded state of every device in one system (e.g. 'lights')."""
        return _dumps(await backend.read_system(system))

    @mcp.resource("gekko://item/{system}/{resource_id}")
    async def item_state_resource(system: str, resource_id: str) -> str:
        """Live decoded state of a single device, e.g. lights/item0."""
        return _dumps(await service.get_state(f"{system}/{resource_id}"))

    @mcp.resource("gekko://capabilities")
    async def capabilities_resource() -> str:
        """What THIS controller can do: systems + decoded per-device command grammar."""
        return _dumps(await service.describe_capabilities())

    # --- read tools --------------------------------------------------------
    @mcp.tool()
    async def list_devices(system: str | None = None) -> dict:
        """List available myGEKKO devices, optionally filtered to one system.

        Args:
            system: optional system key (e.g. 'lights', 'blinds', 'roomtemps').
        """
        return await service.list_devices(system)

    @mcp.tool()
    async def get_state(path: str) -> dict:
        """Read the current state of a device by its path (e.g. 'lights/item0')."""
        return await service.get_state(path)

    @mcp.tool()
    async def describe_capabilities(system: str | None = None) -> dict:
        """Discover what this specific controller exposes and how to control it.

        Because every installation is configured differently, call this to learn
        which systems exist, which are controllable, and the exact command
        grammar of each writable device. Systems with a 'typed_tool' are best
        driven by that tool (e.g. control_light); the rest are driven by
        'control_device' using an advertised command token or label.

        Args:
            system: optional system key to describe just one (e.g. 'lights').
        """
        return await service.describe_capabilities(system)

    # --- write tools (only when enabled) ----------------------------------
    if settings.allow_write:
        _register_write_tools(mcp, service)
        logger.info("Write tools ENABLED (GEKKO_ALLOW_WRITE=true).")
    else:
        logger.info("Write tools disabled (read-only). Set GEKKO_ALLOW_WRITE=true to enable.")

    # --- history tools (only for a resolved GEKKO BRAIN tenant — env/header-only credentials
    # have no GEKKO BRAIN account behind them to read history from) ---------
    if settings.token_auth_enabled and settings.history_base_url_effective:
        history_client = BrainHistoryClient(
            settings.history_base_url_effective, timeout_seconds=settings.timeout_seconds
        )
        _register_history_tools(mcp, history_client)
        logger.info("History tools ENABLED (%s).", settings.history_base_url_effective)
    else:
        logger.info("History tools disabled (needs seamless token auth + a history URL).")

    return mcp


def _register_history_tools(mcp: FastMCP, history_client: BrainHistoryClient) -> None:
    def _require_token() -> str:
        creds = RequestCredentialProvider().get()
        if not creds.raw_token:
            raise CredentialError(
                "history export requires the GEKKO BRAIN bearer token — per-user header "
                "credentials do not carry one"
            )
        return creds.raw_token

    @mcp.tool()
    async def get_history(metric: str, from_: str | None = None, to: str | None = None) -> dict:
        """Historical time-series for a climate/energy/device metric (e.g. 'pv_w',
        'room_temp:roomtemps:item0'). Never presence/access data — call
        get_event_summary for those, which returns daily counts only, never per-event
        timestamps (privacy — see GEKKO BRAIN's DPIA).

        Args:
            metric: canonical GEKKO BRAIN tsdb metric name.
            from_: ISO8601 start (default: 7 days before `to`).
            to: ISO8601 end (default: now).
        """
        token = _require_token()
        from_ms, to_ms = _parse_range(from_, to)
        try:
            return await history_client.get_series(
                token, metric=metric, from_ms=from_ms, to_ms=to_ms
            )
        except HistoryError as exc:
            return {"error": str(exc)}

    @mcp.tool()
    async def get_event_summary(
        category: str, from_: str | None = None, to: str | None = None
    ) -> dict:
        """Event history for one category (control|automation|config|energy|access|presence|
        connection). For access/presence/connection this ALWAYS returns a daily count
        histogram — never per-event timestamps or subject names (privacy — see GEKKO
        BRAIN's DPIA P-R2). The other categories return full bounded event detail.

        Args:
            category: one of control|automation|config|energy|access|presence|connection.
            from_: ISO8601 start (default: 7 days before `to`).
            to: ISO8601 end (default: now).
        """
        token = _require_token()
        from_ms, to_ms = _parse_range(from_, to)
        try:
            return await history_client.get_event_summary(
                token, category=category, from_ms=from_ms, to_ms=to_ms
            )
        except HistoryError as exc:
            return {"error": str(exc)}


def _register_write_tools(mcp: FastMCP, service: GekkoService) -> None:
    @mcp.tool()
    async def control_device(
        path: str,
        command: str,
        value: float | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Generic control for ANY writable device on this controller.

        The controller-agnostic fallback: works for systems without a dedicated
        control_* tool (and on other installations with systems we have not
        special-cased). Call describe_capabilities first to see valid commands.
        Only ever sends a command the device advertises. Prefer a typed
        control_* tool when one exists for the system.

        Args:
            path: device path, e.g. 'saunas/item0' or 'multirooms/item1'.
            command: an advertised command — a literal token ('1', 'PLAY',
                'BY0'), a value-command prefix ('D', 'V', 'S'), or its label
                ('On', 'ChangeVolume').
            value: numeric value for value-commands (e.g. dim %, volume, °C).
            dry_run: if true, only preview the command without sending it.
            confirm: required=true for safety-critical systems.
        """
        res = await service.control_generic(
            path, command, value=value, dry_run=dry_run, confirm=confirm
        )
        return res.as_dict()

    @mcp.tool()
    async def control_light(
        path: str,
        action: str,
        level: float | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Control a light.

        Args:
            path: device path, e.g. 'lights/item0'.
            action: one of on | off | toggle | dim.
            level: brightness 0-100 (required for action='dim').
            dry_run: if true, only preview the command without sending it.
            confirm: required=true for safety-critical systems.
        """
        res = await service.control(path, action, level=level, dry_run=dry_run, confirm=confirm)
        return res.as_dict()

    @mcp.tool()
    async def control_blind(
        path: str,
        action: str,
        position: float | None = None,
        angle: float | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Control a blind / shutter (Jalousie).

        Args:
            path: device path, e.g. 'blinds/item0'.
            action: one of up | down | stop | hold_up | hold_down | toggle | position | angle.
            position: target position 0-100 (for action='position').
            angle: target slat angle in degrees (for action='angle').
            dry_run: if true, only preview the command without sending it.
            confirm: required=true for safety-critical systems.
        """
        res = await service.control(
            path, action, position=position, angle=angle, dry_run=dry_run, confirm=confirm
        )
        return res.as_dict()

    @mcp.tool()
    async def control_load(
        path: str,
        action: str,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Control a generic load (socket, pump, appliance circuit).

        Args:
            path: device path, e.g. 'loads/item0'.
            action: one of on | off | toggle | on_permanent | on_impulse.
                'on' stays on; 'on_impulse' pulses briefly.
            dry_run: if true, only preview the command without sending it.
            confirm: required=true for safety-critical systems.
        """
        res = await service.control(path, action, dry_run=dry_run, confirm=confirm)
        return res.as_dict()

    @mcp.tool()
    async def control_room_temp(
        path: str,
        action: str,
        setpoint: float | None = None,
        mode: str | None = None,
        delta: float | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Control a room thermostat (roomtemps).

        Args:
            path: device path, e.g. 'roomtemps/item0'.
            action: one of setpoint | adjust | mode.
            setpoint: absolute target temperature in °C (for action='setpoint').
            mode: one of off | on | comfort | reduced | manual | standby
                (for action='mode').
            delta: relative change in °C, may be negative (for action='adjust').
            dry_run: if true, only preview the command without sending it.
            confirm: required=true for safety-critical systems.
        """
        res = await service.control(
            path,
            action,
            setpoint=setpoint,
            mode=mode,
            delta=delta,
            dry_run=dry_run,
            confirm=confirm,
        )
        return res.as_dict()

    @mcp.tool()
    async def control_hotwater(
        path: str,
        action: str,
        setpoint: float | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Control a hot-water / boiler system (hotwater_systems).

        Args:
            path: device path, e.g. 'hotwater_systems/item0'.
            action: one of on | off | setpoint.
            setpoint: target water temperature in °C (for action='setpoint').
            dry_run: if true, only preview the command without sending it.
            confirm: required=true for safety-critical systems.
        """
        res = await service.control(
            path, action, setpoint=setpoint, dry_run=dry_run, confirm=confirm
        )
        return res.as_dict()

    @mcp.tool()
    async def control_heating_circuit(
        path: str,
        action: str,
        setpoint: float | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Control a heating circuit (heatingcircuits).

        Args:
            path: device path, e.g. 'heatingcircuits/item0'.
            action: one of on | off | auto | setpoint.
            setpoint: target temperature in °C (for action='setpoint').
            dry_run: if true, only preview the command without sending it.
            confirm: required=true for safety-critical systems.
        """
        res = await service.control(
            path, action, setpoint=setpoint, dry_run=dry_run, confirm=confirm
        )
        return res.as_dict()

    @mcp.tool()
    async def control_vent(
        path: str,
        action: str,
        level: float | None = None,
        mode: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Control a ventilation unit (vents).

        Args:
            path: device path, e.g. 'vents/item0'.
            action: one of off | toggle | level | bypass | cooling_on |
                cooling_off | dehumidify_on | dehumidify_off.
            level: fan stage 1-4 (for action='level').
            mode: bypass mode auto | manual | summer (for action='bypass').
            dry_run: if true, only preview the command without sending it.
            confirm: required=true for safety-critical systems.
        """
        res = await service.control(
            path, action, level=level, mode=mode, dry_run=dry_run, confirm=confirm
        )
        return res.as_dict()

    @mcp.tool()
    async def control_pool(
        path: str,
        action: str,
        mode: str | None = None,
        setpoint: float | None = None,
        cycle: int | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Control a pool (pools).

        Args:
            path: device path, e.g. 'pools/item0'.
            action: one of mode | setpoint | clean_filter.
            mode: operating mode off | rest | bathing (for action='mode').
            setpoint: target water temperature in °C (for action='setpoint').
            cycle: filter backwash cycle 1-3 (for action='clean_filter').
            dry_run: if true, only preview the command without sending it.
            confirm: required=true for safety-critical systems.
        """
        res = await service.control(
            path,
            action,
            mode=mode,
            setpoint=setpoint,
            level=cycle,
            dry_run=dry_run,
            confirm=confirm,
        )
        return res.as_dict()

    @mcp.tool()
    async def control_action(
        path: str,
        action: str,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Trigger a myGEKKO action / scene (actions).

        Args:
            path: device path, e.g. 'actions/item0'.
            action: one of start | stop | toggle.
            dry_run: if true, only preview the command without sending it.
            confirm: required=true for safety-critical systems.
        """
        res = await service.control(path, action, dry_run=dry_run, confirm=confirm)
        return res.as_dict()

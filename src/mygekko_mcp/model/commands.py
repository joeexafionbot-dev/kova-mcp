"""Build validated ``scmd`` tokens from high-level intents.

Every built token is checked against the *item's own advertised command grammar*
(the parsed ``scmd`` tokens from ``/var``). We never emit a token the device did
not advertise — a bogus command would otherwise surface as an alarm popup and be
logged in the controller's alarm history.

Phase 1 covers the two POC gewerke explicitly: **lights** and **blinds**. Their
grammars are non-trivial (placeholder args like ``D100`` / ``P55.4``), which is
why they get dedicated, tested builders rather than a generic guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mygekko_mcp.model.inventory import InventoryItem


class CommandError(ValueError):
    """Raised when an intent cannot be turned into a valid command for an item."""


@dataclass(frozen=True)
class BuiltCommand:
    """A validated command ready to send to ``scmd/set``."""

    token: str
    description: str
    system: str
    resource_id: str

    @property
    def path(self) -> str:
        return f"{self.system}/{self.resource_id}"


# --- grammar lookups --------------------------------------------------------
def _has_literal(item: InventoryItem, token: str) -> bool:
    return any(c.token == token and not c.takes_arg for c in item.commands)


def _has_prefix_arg(item: InventoryItem, prefix: str) -> bool:
    return any(c.prefix == prefix and c.takes_arg for c in item.commands)


def _fmt_number(value: float) -> str:
    """Format like the API examples: integers plain, else one decimal."""
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _require_literal(item: InventoryItem, token: str, action: str) -> None:
    if not _has_literal(item, token):
        raise CommandError(
            f"'{item.name}' ({item.system}) does not support action '{action}' "
            f"(token '{token}' not advertised)"
        )


def _pct(value: float, name: str) -> float:
    if not 0.0 <= value <= 100.0:
        raise CommandError(f"{name} must be between 0 and 100, got {value}")
    return value


# --- lights -----------------------------------------------------------------
def build_light_command(
    item: InventoryItem, action: str, *, level: float | None = None
) -> BuiltCommand:
    action = action.lower()
    if action == "on":
        _require_literal(item, "1", action)
        return BuiltCommand("1", f"turn on {item.name}", item.system, item.resource_id)
    if action == "off":
        _require_literal(item, "0", action)
        return BuiltCommand("0", f"turn off {item.name}", item.system, item.resource_id)
    if action == "toggle":
        _require_literal(item, "T", action)
        return BuiltCommand("T", f"toggle {item.name}", item.system, item.resource_id)
    if action == "dim":
        if level is None:
            raise CommandError("dim requires 'level' (0-100)")
        _pct(level, "level")
        if not _has_prefix_arg(item, "D"):
            raise CommandError(f"'{item.name}' does not support dimming")
        return BuiltCommand(
            f"D{_fmt_number(level)}",
            f"dim {item.name} to {_fmt_number(level)}%",
            item.system,
            item.resource_id,
        )
    raise CommandError(f"unknown light action '{action}' (use on|off|toggle|dim)")


# --- blinds -----------------------------------------------------------------
_BLIND_LITERALS = {
    "up": ("1", "raise"),
    "down": ("-1", "lower"),
    "stop": ("0", "stop"),
    "hold_up": ("2", "hold-up"),
    "hold_down": ("-2", "hold-down"),
    "toggle": ("T", "toggle"),
}


def build_blind_command(
    item: InventoryItem, action: str, *, position: float | None = None, angle: float | None = None
) -> BuiltCommand:
    action = action.lower()
    if action in _BLIND_LITERALS:
        token, verb = _BLIND_LITERALS[action]
        _require_literal(item, token, action)
        return BuiltCommand(token, f"{verb} {item.name}", item.system, item.resource_id)
    if action == "position":
        if position is None:
            raise CommandError("position requires 'position' (0-100)")
        _pct(position, "position")
        if not _has_prefix_arg(item, "P"):
            raise CommandError(f"'{item.name}' does not support positioning")
        return BuiltCommand(
            f"P{_fmt_number(position)}",
            f"move {item.name} to {_fmt_number(position)}%",
            item.system,
            item.resource_id,
        )
    if action == "angle":
        if angle is None:
            raise CommandError("angle requires 'angle' (degrees)")
        if not _has_prefix_arg(item, "S"):
            raise CommandError(f"'{item.name}' does not support slat angle")
        return BuiltCommand(
            f"S{_fmt_number(angle)}",
            f"set {item.name} slat angle to {_fmt_number(angle)}",
            item.system,
            item.resource_id,
        )
    raise CommandError(
        f"unknown blind action '{action}' "
        f"(use up|down|stop|hold_up|hold_down|toggle|position|angle)"
    )


# --- loads (generic switchable circuits: sockets, pumps, ...) ---------------
def build_load_command(item: InventoryItem, action: str) -> BuiltCommand:
    # grammar: 0|1|2|T  (Off | OnImpulse | OnPermanent | Toggle)
    action = action.lower()
    if action == "off":
        _require_literal(item, "0", action)
        return BuiltCommand("0", f"turn off {item.name}", item.system, item.resource_id)
    if action == "toggle":
        _require_literal(item, "T", action)
        return BuiltCommand("T", f"toggle {item.name}", item.system, item.resource_id)
    if action in ("on", "on_permanent"):
        # "on" prefers the permanent state (stays on); fall back to impulse.
        token = "2" if _has_literal(item, "2") else "1"
        _require_literal(item, token, action)
        verb = "turn on (permanent)" if token == "2" else "turn on (impulse)"
        return BuiltCommand(token, f"{verb} {item.name}", item.system, item.resource_id)
    if action in ("on_impulse", "impulse"):
        _require_literal(item, "1", action)
        return BuiltCommand("1", f"pulse {item.name} on", item.system, item.resource_id)
    raise CommandError(
        f"unknown load action '{action}' (use on|off|toggle|on_permanent|on_impulse)"
    )


# --- roomtemps (thermostats) ------------------------------------------------
# Advertised as literal mode tokens + two value setters. Names come from the
# scmd labels: M1=off M2=on M8=comfort M16=reduced M64=manual M256=standby.
_ROOMTEMP_MODES = {
    "off": "M1",
    "on": "M2",
    "comfort": "M8",
    "reduced": "M16",
    "manual": "M64",
    "standby": "M256",
}
# Sanity band for a room setpoint — the API accepts -100..100 °C, but an
# assistant should never send a wild value from a mis-parsed request.
_SETPOINT_MIN, _SETPOINT_MAX = 5.0, 35.0
_ADJUST_LIMIT = 10.0


def build_roomtemp_command(
    item: InventoryItem,
    action: str,
    *,
    setpoint: float | None = None,
    mode: str | None = None,
    delta: float | None = None,
) -> BuiltCommand:
    action = action.lower()
    if action == "setpoint":
        if setpoint is None:
            raise CommandError("setpoint requires 'setpoint' (°C)")
        if not _SETPOINT_MIN <= setpoint <= _SETPOINT_MAX:
            raise CommandError(
                f"setpoint must be between {_SETPOINT_MIN} and {_SETPOINT_MAX} °C, got {setpoint}"
            )
        if not _has_prefix_arg(item, "S"):
            raise CommandError(f"'{item.name}' does not support a setpoint")
        return BuiltCommand(
            f"S{_fmt_number(setpoint)}",
            f"set {item.name} target to {_fmt_number(setpoint)} °C",
            item.system,
            item.resource_id,
        )
    if action == "adjust":
        if delta is None:
            raise CommandError("adjust requires 'delta' (°C, may be negative)")
        if not -_ADJUST_LIMIT <= delta <= _ADJUST_LIMIT:
            raise CommandError(f"delta must be within ±{_ADJUST_LIMIT} °C, got {delta}")
        if not _has_prefix_arg(item, "K"):
            raise CommandError(f"'{item.name}' does not support relative adjustment")
        return BuiltCommand(
            f"K{_fmt_number(delta)}",
            f"adjust {item.name} by {_fmt_number(delta)} °C",
            item.system,
            item.resource_id,
        )
    if action == "mode":
        if mode is None:
            raise CommandError(f"mode requires 'mode' (one of {', '.join(_ROOMTEMP_MODES)})")
        token = _ROOMTEMP_MODES.get(mode.lower())
        if token is None:
            raise CommandError(f"unknown mode '{mode}' (use {', '.join(_ROOMTEMP_MODES)})")
        _require_literal(item, token, f"mode={mode}")
        return BuiltCommand(
            token, f"set {item.name} mode to {mode.lower()}", item.system, item.resource_id
        )
    raise CommandError(f"unknown roomtemp action '{action}' (use setpoint|adjust|mode)")


# --- hotwater systems (boilers) ---------------------------------------------
_HOTWATER_MIN, _HOTWATER_MAX = 20.0, 70.0


def build_hotwater_command(
    item: InventoryItem, action: str, *, setpoint: float | None = None
) -> BuiltCommand:
    # grammar: 0|1|T100  (Off | On | Temperature[°C])
    action = action.lower()
    if action == "off":
        _require_literal(item, "0", action)
        return BuiltCommand("0", f"turn off {item.name}", item.system, item.resource_id)
    if action == "on":
        _require_literal(item, "1", action)
        return BuiltCommand("1", f"turn on {item.name}", item.system, item.resource_id)
    if action == "setpoint":
        if setpoint is None:
            raise CommandError("setpoint requires 'setpoint' (°C)")
        if not _HOTWATER_MIN <= setpoint <= _HOTWATER_MAX:
            raise CommandError(
                f"setpoint must be between {_HOTWATER_MIN} and {_HOTWATER_MAX} °C, got {setpoint}"
            )
        if not _has_prefix_arg(item, "T"):
            raise CommandError(f"'{item.name}' does not support a temperature setpoint")
        return BuiltCommand(
            f"T{_fmt_number(setpoint)}",
            f"set {item.name} water temperature to {_fmt_number(setpoint)} °C",
            item.system,
            item.resource_id,
        )
    raise CommandError(f"unknown hotwater action '{action}' (use on|off|setpoint)")


# --- heating circuits -------------------------------------------------------
_HEATING_MIN, _HEATING_MAX = 5.0, 60.0


def build_heating_command(
    item: InventoryItem, action: str, *, setpoint: float | None = None
) -> BuiltCommand:
    # grammar: 0|1|A|T100  (Off | On | Auto | Temperature[°C])
    action = action.lower()
    if action == "off":
        _require_literal(item, "0", action)
        return BuiltCommand("0", f"turn off {item.name}", item.system, item.resource_id)
    if action == "on":
        _require_literal(item, "1", action)
        return BuiltCommand("1", f"turn on {item.name}", item.system, item.resource_id)
    if action == "auto":
        _require_literal(item, "A", action)
        return BuiltCommand("A", f"set {item.name} to auto", item.system, item.resource_id)
    if action == "setpoint":
        if setpoint is None:
            raise CommandError("setpoint requires 'setpoint' (°C)")
        if not _HEATING_MIN <= setpoint <= _HEATING_MAX:
            raise CommandError(
                f"setpoint must be between {_HEATING_MIN} and {_HEATING_MAX} °C, got {setpoint}"
            )
        if not _has_prefix_arg(item, "T"):
            raise CommandError(f"'{item.name}' does not support a temperature setpoint")
        return BuiltCommand(
            f"T{_fmt_number(setpoint)}",
            f"set {item.name} target to {_fmt_number(setpoint)} °C",
            item.system,
            item.resource_id,
        )
    raise CommandError(f"unknown heating action '{action}' (use on|off|auto|setpoint)")


# --- ventilation ------------------------------------------------------------
# grammar: -1|1|2|3|4|T|BY0|BY1|BY2|C0|C1|D0|D1|Mx
#          (Off|Level1..4|Toggle|Bypass{Auto,Manual,Summer}|Cool{Off,On}|Dehumid{Off,On}|Mode)
# The Mx "OperatingMode[0..x]" setter is device-specific (unknown range) and is
# intentionally NOT exposed — we only emit tokens with a well-defined meaning.
_VENT_BYPASS = {"auto": "BY0", "manual": "BY1", "summer": "BY2"}


def build_vent_command(
    item: InventoryItem, action: str, *, level: float | None = None, mode: str | None = None
) -> BuiltCommand:
    action = action.lower()
    if action == "off":
        _require_literal(item, "-1", action)
        return BuiltCommand("-1", f"turn off {item.name}", item.system, item.resource_id)
    if action == "toggle":
        _require_literal(item, "T", action)
        return BuiltCommand("T", f"toggle {item.name}", item.system, item.resource_id)
    if action == "level":
        if level is None:
            raise CommandError("level requires 'level' (1-4)")
        if float(level).is_integer() and 1 <= int(level) <= 4:
            token = str(int(level))
        else:
            raise CommandError(f"vent level must be an integer 1-4, got {level}")
        _require_literal(item, token, f"level={token}")
        return BuiltCommand(
            token, f"set {item.name} to level {token}", item.system, item.resource_id
        )
    if action == "bypass":
        token = _VENT_BYPASS.get((mode or "").lower())
        if token is None:
            raise CommandError(f"bypass requires 'mode' (one of {', '.join(_VENT_BYPASS)})")
        _require_literal(item, token, f"bypass={mode}")
        return BuiltCommand(
            token, f"set {item.name} bypass to {mode.lower()}", item.system, item.resource_id
        )
    _SWITCHES = {
        "cooling_on": ("C1", "enable cooling on"),
        "cooling_off": ("C0", "disable cooling on"),
        "dehumidify_on": ("D1", "enable dehumidify on"),
        "dehumidify_off": ("D0", "disable dehumidify on"),
    }
    if action in _SWITCHES:
        token, verb = _SWITCHES[action]
        _require_literal(item, token, action)
        return BuiltCommand(token, f"{verb} {item.name}", item.system, item.resource_id)
    raise CommandError(
        f"unknown vent action '{action}' (use off|toggle|level|bypass|"
        f"cooling_on|cooling_off|dehumidify_on|dehumidify_off)"
    )


# --- pools ------------------------------------------------------------------
# grammar: M0|M1|M2|C1|C2|C3|T100
#          (off|restMode|bathing|cleanFilter1|cleanFilter2|cleanFilter3|setTemperature)
_POOL_MODES = {
    "off": "M0",
    "rest": "M1",
    "standby": "M1",
    "bathing": "M2",
    "active": "M2",
}
_POOL_MIN, _POOL_MAX = 10.0, 40.0


def build_pool_command(
    item: InventoryItem,
    action: str,
    *,
    mode: str | None = None,
    setpoint: float | None = None,
    level: float | None = None,
) -> BuiltCommand:
    action = action.lower()
    if action == "mode":
        token = _POOL_MODES.get((mode or "").lower())
        if token is None:
            raise CommandError("mode requires 'mode' (off|rest|bathing)")
        _require_literal(item, token, f"mode={mode}")
        return BuiltCommand(
            token, f"set {item.name} mode to {mode.lower()}", item.system, item.resource_id
        )
    if action == "setpoint":
        if setpoint is None:
            raise CommandError("setpoint requires 'setpoint' (°C)")
        if not _POOL_MIN <= setpoint <= _POOL_MAX:
            raise CommandError(
                f"setpoint must be between {_POOL_MIN} and {_POOL_MAX} °C, got {setpoint}"
            )
        if not _has_prefix_arg(item, "T"):
            raise CommandError(f"'{item.name}' does not support a temperature setpoint")
        return BuiltCommand(
            f"T{_fmt_number(setpoint)}",
            f"set {item.name} water temperature to {_fmt_number(setpoint)} °C",
            item.system,
            item.resource_id,
        )
    if action == "clean_filter":
        if level is None:
            raise CommandError("clean_filter requires 'cycle' (1-3)")
        if float(level).is_integer() and 1 <= int(level) <= 3:
            token = f"C{int(level)}"
        else:
            raise CommandError(f"clean_filter cycle must be an integer 1-3, got {level}")
        _require_literal(item, token, f"clean_filter={token}")
        return BuiltCommand(
            token,
            f"start {item.name} filter backwash cycle {int(level)}",
            item.system,
            item.resource_id,
        )
    raise CommandError(f"unknown pool action '{action}' (use mode|setpoint|clean_filter)")


# --- actions (scenes / macros) ----------------------------------------------
_ACTION_TOKENS = {
    "start": ("1", "start"),
    "stop": ("-1", "stop"),
    "toggle": ("T", "toggle"),
}


def build_action_command(item: InventoryItem, action: str) -> BuiltCommand:
    # grammar: -1|1|T  (Stop | Start | Toggle)
    tok = _ACTION_TOKENS.get(action.lower())
    if tok is None:
        raise CommandError(f"unknown action command '{action}' (use start|stop|toggle)")
    token, verb = tok
    _require_literal(item, token, action)
    return BuiltCommand(token, f"{verb} {item.name}", item.system, item.resource_id)


# --- dispatch ---------------------------------------------------------------
_BUILDERS = {
    "lights": build_light_command,
    "blinds": build_blind_command,
    "loads": build_load_command,
    "roomtemps": build_roomtemp_command,
    "hotwater_systems": build_hotwater_command,
    "heatingcircuits": build_heating_command,
    "vents": build_vent_command,
    "pools": build_pool_command,
    "actions": build_action_command,
}


def build_command(item: InventoryItem, action: str, **params: object) -> BuiltCommand:
    """Dispatch to the per-system builder for the item's system."""
    builder = _BUILDERS.get(item.system)
    if builder is None:
        raise CommandError(
            f"no dedicated builder for system '{item.system}' — use generic control "
            f"(control_device) with an advertised command. "
            f"Typed systems: {', '.join(sorted(_BUILDERS))}"
        )
    return builder(item, action, **params)  # type: ignore[arg-type]


def has_builder(system: str) -> bool:
    """True if a dedicated, typed builder exists for this system."""
    return system in _BUILDERS


def _clean_label(label: str) -> str:
    """Strip the trailing ``[unit/range]`` hint from a scmd label."""
    return re.sub(r"\[.*?\]", "", label or "").strip()


def build_generic_command(
    item: InventoryItem, command: str, *, value: float | None = None
) -> BuiltCommand:
    """Build a command for ANY writable item straight from its advertised grammar.

    This is the controller-agnostic fallback: it never invents a token, only
    emits ones the device advertised (matched by exact token, value-prefix, or
    human label). Use it for systems without a dedicated typed builder, or on
    controllers configured with systems we have not special-cased.

    ``command`` may be an advertised literal token (``"1"``, ``"BY0"``), a
    value-command prefix or example (``"D"`` / ``"D100"``, ``"V"``), or the
    command's label (``"On"``, ``"ChangeVolume"``). ``value`` is required for
    value-commands.
    """
    cmds = item.commands
    if not cmds:
        raise CommandError(f"'{item.name}' ({item.system}) advertises no writable commands")
    q = str(command).strip()
    ql = q.lower()

    def _emit(spec: object) -> BuiltCommand:
        s = spec  # CommandSpec
        if s.takes_arg:  # type: ignore[attr-defined]
            if value is None:
                raise CommandError(
                    f"command '{s.label or s.prefix}' on {item.name} requires a value"  # type: ignore[attr-defined]
                )
            token = f"{s.prefix}{_fmt_number(float(value))}"  # type: ignore[attr-defined]
            desc = f"{item.name}: {_clean_label(s.label) or s.prefix} = {_fmt_number(float(value))}"  # type: ignore[attr-defined]
            return BuiltCommand(token, desc, item.system, item.resource_id)
        return BuiltCommand(
            s.token,  # type: ignore[attr-defined]
            f"{item.name}: {s.label or s.token}",  # type: ignore[attr-defined]
            item.system,
            item.resource_id,
        )

    # 1) exact literal token (e.g. "1", "-1", "M8", "BY0", "PLAY")
    for s in cmds:
        if not s.takes_arg and s.token == q:
            return _emit(s)
    # 2) value command by prefix or by its example token (e.g. "D" or "D100")
    for s in cmds:
        if s.takes_arg and (s.prefix == q or s.token == q):
            return _emit(s)
    # 3) fall back to the human label (case-insensitive, unit hint stripped)
    for s in cmds:
        if s.label and _clean_label(s.label).lower() == ql:
            return _emit(s)
    available = ", ".join(f"{s.prefix}<value>" if s.takes_arg else s.token for s in cmds)
    raise CommandError(
        f"'{q}' is not an advertised command for {item.name} ({item.system}). "
        f"Available: {available}"
    )

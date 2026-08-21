"""Write-gate policy — the single place that decides whether a write may happen.

Layers (all must pass), see docs/security.md:
1. Global master switch ``allow_write``.
2. Deny-list: safety-critical systems are blocked even if writes are on.
3. Allow-list: if set, only listed systems are writable (least privilege).
4. Critical systems that ARE explicitly allowed still require a second
   confirmation (dry-run first, then confirm) before execution.
"""

from __future__ import annotations

from dataclasses import dataclass

from mygekko_mcp.config import Settings

# Systems that control physical security / access. Even when a deployer opts
# into writing them, we require an explicit confirmation step.
CRITICAL_SYSTEMS: frozenset[str] = frozenset({"alarmsystem", "accessdoors", "door_intercom"})


@dataclass(frozen=True)
class WriteDecision:
    """Outcome of a policy check for one (system) write."""

    allowed: bool
    requires_confirm: bool
    reason: str

    @property
    def blocked(self) -> bool:
        return not self.allowed


class WritePolicy:
    """Evaluates whether writes to a given system are permitted."""

    def __init__(self, settings: Settings) -> None:
        self._allow_write = settings.allow_write
        self._deny = {s.lower() for s in settings.deny_systems}
        self._allow = {s.lower() for s in settings.allow_systems}

    @property
    def write_enabled(self) -> bool:
        return self._allow_write

    def is_critical(self, system: str) -> bool:
        return system.lower() in CRITICAL_SYSTEMS

    def check(self, system: str) -> WriteDecision:
        sys_l = system.lower()
        if not self._allow_write:
            return WriteDecision(
                False, False, "writes are globally disabled (GEKKO_ALLOW_WRITE=false)"
            )
        if sys_l in self._deny:
            return WriteDecision(False, False, f"system '{system}' is deny-listed")
        if self._allow and sys_l not in self._allow:
            return WriteDecision(False, False, f"system '{system}' is not in the allow-list")
        if sys_l in CRITICAL_SYSTEMS:
            return WriteDecision(
                True, True, f"system '{system}' is safety-critical: confirmation required"
            )
        return WriteDecision(True, False, "ok")

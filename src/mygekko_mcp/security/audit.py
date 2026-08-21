"""Append-only JSONL audit log for write actions. Secrets are never written."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("mygekko_mcp.audit")

# Keys whose values must always be masked if they ever appear in an audit record.
_SECRET_KEYS = {"key", "password", "secret", "auth_params", "authorization"}


def redact(data: Any) -> Any:
    """Recursively mask secret-looking keys in a mapping/sequence."""
    if isinstance(data, dict):
        return {k: ("***" if k.lower() in _SECRET_KEYS else redact(v)) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [redact(v) for v in data]
    return data


class AuditLog:
    """Writes one redacted JSON object per line for each auditable action."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def record(
        self,
        *,
        action: str,
        system: str,
        item: str,
        command: str | None = None,
        dry_run: bool,
        result: str,
        actor: str = "mcp",
        extra: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": action,
            "system": system,
            "item": item,
            "command": command,
            "dry_run": dry_run,
            "result": result,
        }
        if extra:
            entry["extra"] = redact(extra)
        line = json.dumps(redact(entry), ensure_ascii=False)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as e:  # never let auditing crash a request
            logger.warning("Audit write failed: %s", e)

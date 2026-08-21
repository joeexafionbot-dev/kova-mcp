"""Tests for the write gate, audit redaction, and rate limiter."""

import json
import time

from mygekko_mcp.config import Settings
from mygekko_mcp.security import AsyncRateLimiter, AuditLog, WritePolicy, redact


def _settings(**over) -> Settings:
    base = dict(
        username="u@example.com",
        key="SECRET",
        gekkoid="AAAA-BBBB-CCCC-DDDD",
        allow_write=False,
        deny_systems=["alarmsystem", "accessdoors", "door_intercom"],
        allow_systems=[],
    )
    base.update(over)
    return Settings(**base)


def test_writes_disabled_by_default():
    pol = WritePolicy(_settings())
    d = pol.check("lights")
    assert d.blocked and "globally disabled" in d.reason


def test_allowed_when_write_enabled():
    pol = WritePolicy(_settings(allow_write=True))
    d = pol.check("lights")
    assert d.allowed and not d.requires_confirm


def test_deny_list_blocks_even_when_enabled():
    pol = WritePolicy(_settings(allow_write=True))
    assert pol.check("alarmsystem").blocked
    assert pol.check("accessdoors").blocked


def test_allow_list_restricts():
    pol = WritePolicy(_settings(allow_write=True, allow_systems=["lights"]))
    assert pol.check("lights").allowed
    assert pol.check("blinds").blocked


def test_critical_system_requires_confirm_when_explicitly_allowed():
    # Deployer removes accessdoors from deny AND allow-lists it -> still critical.
    pol = WritePolicy(_settings(allow_write=True, deny_systems=[], allow_systems=["accessdoors"]))
    d = pol.check("accessdoors")
    assert d.allowed and d.requires_confirm


def test_redact_masks_secrets():
    payload = {"username": "u", "key": "SECRET", "nested": {"password": "p", "ok": 1}}
    r = redact(payload)
    assert r["key"] == "***"
    assert r["nested"]["password"] == "***"
    assert r["nested"]["ok"] == 1
    assert r["username"] == "u"  # username is not a secret


def test_audit_writes_redacted_jsonl(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(
        action="write",
        system="lights",
        item="lights/item0",
        command="D50",
        dry_run=True,
        result="dry-run",
        extra={"key": "SECRET", "note": "hi"},
    )
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["system"] == "lights"
    assert rec["command"] == "D50"
    assert rec["extra"]["key"] == "***"
    assert "SECRET" not in lines[0]


async def test_rate_limiter_throttles():
    rl = AsyncRateLimiter(rate_per_sec=20.0, burst=2)
    start = time.monotonic()
    for _ in range(5):  # 2 free (burst) + 3 throttled at 20/s -> ~0.15s
        await rl.acquire()
    assert time.monotonic() - start >= 0.1

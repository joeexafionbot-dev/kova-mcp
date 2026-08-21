"""Settings parsing — especially the list-from-env path that pydantic-settings
would otherwise try to JSON-decode (which crashed the CSV form shipped in
.env.example). See config.py NoDecode annotation + _split_csv validator."""

from __future__ import annotations

import pytest

from mygekko_mcp.config import Settings


def _base(**over):
    kw = dict(username="u@example.com", key="K", gekkoid="AAAA-BBBB-CCCC-DDDD")
    kw.update(over)
    return kw


def test_deny_systems_default():
    s = Settings(**_base())
    assert s.deny_systems == ["alarmsystem", "accessdoors", "door_intercom"]


def test_deny_systems_csv_from_env(monkeypatch):
    # The exact form .env.example ships — must NOT crash and must split.
    monkeypatch.setenv("GEKKO_DENY_SYSTEMS", "alarmsystem,accessdoors,door_intercom")
    s = Settings(**_base())
    assert s.deny_systems == ["alarmsystem", "accessdoors", "door_intercom"]


def test_allow_systems_csv_with_spaces(monkeypatch):
    monkeypatch.setenv("GEKKO_ALLOW_SYSTEMS", "lights, blinds ")
    s = Settings(**_base())
    assert s.allow_systems == ["lights", "blinds"]


def test_allow_systems_empty_env_is_empty_list(monkeypatch):
    monkeypatch.setenv("GEKKO_ALLOW_SYSTEMS", "")
    s = Settings(**_base())
    assert s.allow_systems == []


@pytest.mark.parametrize("value", ["lights", "lights,blinds"])
def test_single_and_multi(monkeypatch, value):
    monkeypatch.setenv("GEKKO_ALLOW_SYSTEMS", value)
    s = Settings(**_base())
    assert s.allow_systems == value.split(",")

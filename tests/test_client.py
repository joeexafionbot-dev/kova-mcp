"""Tests for the low-level Query API client: auth injection, re-poll, errors."""

import httpx
import pytest
import respx

from mygekko_mcp.client import GekkoApiError, QueryApiClient

BASE = "https://live.my-gekko.com/api/v1"
AUTH = {"username": "u@example.com", "key": "SECRET", "gekkoid": "AAAA-BBBB-CCCC-DDDD"}


def _client() -> QueryApiClient:
    return QueryApiClient(BASE, AUTH, repoll_delay_seconds=0.0, repoll_max_attempts=3)


@respx.mock
async def test_get_var_injects_auth_and_returns_json():
    route = respx.get(f"{BASE}/var").mock(
        return_value=httpx.Response(200, json={"lights": {"item0": {"name": "L1"}}})
    )
    async with _client() as api:
        tree = await api.get_var()
    assert tree["lights"]["item0"]["name"] == "L1"
    sent = route.calls.last.request.url
    assert sent.params["key"] == "SECRET"  # auth injected
    assert sent.params["gekkoid"] == "AAAA-BBBB-CCCC-DDDD"


@respx.mock
async def test_status_repolls_until_value_present():
    # First response is empty (subscribe only), second carries the value.
    responses = [
        httpx.Response(200, json={"lights": {"item0": {"sumstate": {"value": ""}}}}),
        httpx.Response(200, json={"lights": {"item0": {"sumstate": {"value": "1;60;0;"}}}}),
    ]
    respx.get(f"{BASE}/var/lights/status").mock(side_effect=responses)
    async with _client() as api:
        payload = await api.get_status("lights")
    assert payload["lights"]["item0"]["sumstate"]["value"] == "1;60;0;"


@respx.mock
async def test_status_gives_up_after_max_attempts():
    respx.get(f"{BASE}/var/lights/status").mock(
        return_value=httpx.Response(200, json={"lights": {"item0": {"sumstate": {"value": ""}}}})
    )
    async with _client() as api:
        payload = await api.get_status("lights")
    # Returns the last (still empty) payload rather than looping forever.
    assert payload["lights"]["item0"]["sumstate"]["value"] == ""


@respx.mock
async def test_410_maps_to_bad_credentials_error():
    respx.get(f"{BASE}/var").mock(return_value=httpx.Response(410))
    async with _client() as api:
        with pytest.raises(GekkoApiError) as exc:
            await api.get_var()
    assert exc.value.status_code == 410
    assert "key is wrong" in str(exc.value)


@respx.mock
async def test_429_maps_to_rate_limit_error():
    respx.get(f"{BASE}/var").mock(return_value=httpx.Response(429))
    async with _client() as api:
        with pytest.raises(GekkoApiError) as exc:
            await api.get_var()
    assert exc.value.status_code == 429

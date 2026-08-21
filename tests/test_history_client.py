"""Tests for BrainHistoryClient — the GEKKO BRAIN history-export client."""

from __future__ import annotations

import httpx
import pytest

from mygekko_mcp.history_client import BrainHistoryClient, HistoryError


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_get_series_sends_bearer_and_query_params():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["authorization"] = req.headers["authorization"]
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json={"success": True, "metric": "pv_w", "segments": []})

    c = BrainHistoryClient("https://app/api/mcp", client=_client(handler))
    out = await c.get_series("gkmcp_abc", metric="pv_w", from_ms=1000, to_ms=2000)
    assert seen["path"] == "/api/mcp/history"
    assert seen["authorization"] == "Bearer gkmcp_abc"
    assert seen["params"] == {"metric": "pv_w", "from": "1000", "to": "2000"}
    assert out["metric"] == "pv_w"


async def test_get_event_summary_sends_bearer_and_query_params():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json={"success": True, "category": "access", "counts": []})

    c = BrainHistoryClient("https://app/api/mcp", client=_client(handler))
    out = await c.get_event_summary("gkmcp_abc", category="access", from_ms=1000, to_ms=2000)
    assert seen["path"] == "/api/mcp/event-summary"
    assert seen["params"]["category"] == "access"
    assert out["category"] == "access"


@pytest.mark.parametrize(
    "status,fragment",
    [
        (401, "invalid or revoked"),
        (403, "not enabled"),
        (409, "no active controller"),
        (429, "rate limited"),
        (500, "HTTP 500"),
    ],
)
async def test_get_series_maps_status_codes_to_clear_messages(status, fragment):
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "x"})

    c = BrainHistoryClient("https://app/api/mcp", client=_client(handler))
    with pytest.raises(HistoryError, match=fragment):
        await c.get_series("gkmcp_abc", metric="pv_w", from_ms=0, to_ms=1)


async def test_token_never_appears_in_error_text():
    c = BrainHistoryClient("https://app/api/mcp", client=_client(lambda _r: httpx.Response(401)))
    with pytest.raises(HistoryError) as exc_info:
        await c.get_series("gkmcp_super_secret_token", metric="pv_w", from_ms=0, to_ms=1)
    assert "gkmcp_super_secret_token" not in str(exc_info.value)


async def test_network_error_is_wrapped():
    def handler(_req: httpx.Request):
        raise httpx.ConnectError("boom")

    c = BrainHistoryClient("https://app/api/mcp", client=_client(handler))
    with pytest.raises(HistoryError, match="history request failed"):
        await c.get_series("gkmcp_abc", metric="pv_w", from_ms=0, to_ms=1)


async def test_base_url_trailing_slash_is_normalized():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        return httpx.Response(200, json={})

    c = BrainHistoryClient("https://app/api/mcp/", client=_client(handler))
    await c.get_series("t", metric="pv_w", from_ms=0, to_ms=1)
    assert seen["path"] == "/api/mcp/history"  # no double slash

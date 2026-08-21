"""Tests for the seamless token bridge: credentials_from_resolve + TokenResolver."""

from __future__ import annotations

import json

import httpx
import pytest

from mygekko_mcp.credentials import CredentialError, credentials_from_resolve
from mygekko_mcp.tenant_auth import TokenResolver


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- pure builder ---------------------------------------------------------
def test_credentials_from_resolve_ok_uppercases_gekkoid():
    c = credentials_from_resolve({"username": "u@x", "key": "K", "gekkoid": "aaaa-bbbb-cccc-dddd"})
    assert c.gekkoid == "AAAA-BBBB-CCCC-DDDD"
    assert c.auth_params["key"] == "K"
    assert c.username == "u@x"


def test_credentials_from_resolve_raw_token_defaults_to_none():
    c = credentials_from_resolve({"username": "u@x", "key": "K", "gekkoid": "AAAA-BBBB-CCCC-DDDD"})
    assert c.raw_token is None


def test_credentials_from_resolve_raw_token_passthrough():
    c = credentials_from_resolve(
        {"username": "u@x", "key": "K", "gekkoid": "AAAA-BBBB-CCCC-DDDD"}, raw_token="gkmcp_abc"
    )
    assert c.raw_token == "gkmcp_abc"
    assert c.raw_token not in c.redacted().values()  # never surfaces via redacted()


def test_credentials_from_resolve_missing_field_raises():
    with pytest.raises(CredentialError):
        credentials_from_resolve({"username": "u", "key": ""})


def test_credentials_from_resolve_placeholder_raises():
    with pytest.raises(CredentialError):
        credentials_from_resolve({"username": "u", "key": "K", "gekkoid": "XXXX-XXXX-XXXX-XXXX"})


# --- resolver -------------------------------------------------------------
async def test_resolve_sends_secret_and_token_then_caches():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert req.headers["x-internal-secret"] == "S3"
        assert json.loads(req.content)["token"] == "gkmcp_abc"
        return httpx.Response(
            200, json={"username": "u@x", "key": "K", "gekkoid": "1111-2222-3333-4444"}
        )

    r = TokenResolver("https://app/api/mcp-auth/resolve", "S3", ttl=100, client=_client(handler))
    c1 = await r.resolve("gkmcp_abc")
    c2 = await r.resolve("gkmcp_abc")
    assert c1.gekkoid == "1111-2222-3333-4444"
    assert c2 is c1  # served from cache
    assert calls["n"] == 1  # only one network call
    assert c1.raw_token == "gkmcp_abc"  # threaded through so history tools can reuse it
    assert c2.raw_token == "gkmcp_abc"  # survives a cache hit too


async def test_resolve_refetches_after_ttl():
    calls = {"n": 0}
    clock = {"t": 0.0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200, json={"username": "u", "key": "K", "gekkoid": "1111-2222-3333-4444"}
        )

    r = TokenResolver(
        "https://app/resolve", "S", ttl=10, client=_client(handler), now=lambda: clock["t"]
    )
    await r.resolve("x")
    clock["t"] = 11  # past the TTL
    await r.resolve("x")
    assert calls["n"] == 2  # cache expired → re-fetched


async def test_resolve_non_200_raises_credential_error():
    r = TokenResolver(
        "https://app/resolve",
        "S",
        client=_client(lambda _req: httpx.Response(404, json={"error": "unknown"})),
    )
    with pytest.raises(CredentialError):
        await r.resolve("bad")


async def test_resolve_empty_token_raises():
    r = TokenResolver("https://app/resolve", "S", client=_client(lambda _req: httpx.Response(200)))
    with pytest.raises(CredentialError):
        await r.resolve("")


async def test_resolver_requires_url_and_secret():
    with pytest.raises(ValueError):
        TokenResolver("", "S")
    with pytest.raises(ValueError):
        TokenResolver("u", "")

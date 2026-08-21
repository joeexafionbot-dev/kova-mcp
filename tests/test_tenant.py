"""Multi-tenant hosting: per-request credentials, routing, and isolation."""

import pytest

from mygekko_mcp.backends.tenant import TenantBackendRouter
from mygekko_mcp.config import CLOUD_BASE_URL, Mode, Settings
from mygekko_mcp.credentials import (
    CredentialError,
    RequestCredentialProvider,
    credentials_from_headers,
)
from mygekko_mcp.request_context import (
    get_current_credentials,
    reset_current_credentials,
    set_current_credentials,
)
from mygekko_mcp.transport import TenantCredentialsMiddleware


def _headers(**over):
    h = {
        "x-mygekko-username": "user@example.com",
        "x-mygekko-key": "secret-key",
        "x-mygekko-gekkoid": "aaaa-bbbb-cccc-dddd",
    }
    h.update(over)
    return h.get


def _creds(gekkoid="AAAA-BBBB-CCCC-DDDD", key="secret-key", user="user@example.com"):
    return credentials_from_headers(
        {
            "X-MyGEKKO-Username": user,
            "X-MyGEKKO-Key": key,
            "X-MyGEKKO-Gekkoid": gekkoid,
        }.get
    )


# --- header parsing ---------------------------------------------------------
def test_credentials_from_headers_cloud():
    c = credentials_from_headers(_headers())
    assert c.mode is Mode.cloud
    assert c.base_url == CLOUD_BASE_URL
    assert c.gekkoid == "AAAA-BBBB-CCCC-DDDD"  # upper-cased
    assert c.auth_params == {
        "username": "user@example.com",
        "key": "secret-key",
        "gekkoid": "AAAA-BBBB-CCCC-DDDD",
    }


def test_missing_header_raises_without_leaking_key():
    with pytest.raises(CredentialError) as exc:
        credentials_from_headers(_headers(**{"x-mygekko-key": ""}))
    msg = str(exc.value)
    assert "X-MyGEKKO-Key" in msg and "secret-key" not in msg


def test_placeholder_gekkoid_rejected():
    with pytest.raises(CredentialError):
        credentials_from_headers(_headers(**{"x-mygekko-gekkoid": "XXXX-XXXX-XXXX-XXXX"}))


def test_custom_prefix():
    getter = {"Gk-Username": "u", "Gk-Key": "k", "Gk-Gekkoid": "z-z-z-z"}.get
    c = credentials_from_headers(getter, prefix="Gk-")
    assert c.username == "u" and c.gekkoid == "Z-Z-Z-Z"


# --- fingerprint (cache key) ------------------------------------------------
def test_fingerprint_stable_and_distinct():
    a = _creds()
    assert a.fingerprint() == _creds().fingerprint()  # stable
    assert a.fingerprint() != _creds(key="other").fingerprint()  # secret matters
    assert a.fingerprint() != _creds(gekkoid="Z-Z-Z-Z").fingerprint()
    assert a.fingerprint() != _creds(user="other@x").fingerprint()


# --- request-context provider ----------------------------------------------
def test_request_provider_reads_context():
    prov = RequestCredentialProvider()
    with pytest.raises(CredentialError):
        prov.get()  # nothing bound
    tok = set_current_credentials(_creds())
    try:
        assert prov.get().gekkoid == "AAAA-BBBB-CCCC-DDDD"
    finally:
        reset_current_credentials(tok)
    with pytest.raises(CredentialError):
        prov.get()  # reset again


# --- router: routing, caching, isolation, eviction --------------------------
def _fake_backend_factory(registry):
    class _FakeRest:
        def __init__(self, provider, settings):
            self.creds = provider.get()
            self.closed = False
            registry.append(self)

        async def get_inventory(self, *, refresh=False):
            return self.creds  # sentinel so tests can see which tenant answered

        async def write(self, system, resource_id, token):
            return f"OK {self.creds.gekkoid}"

        async def aclose(self):
            self.closed = True

    return _FakeRest


def _settings(**over):
    base = dict(multi_tenant=True, tenant_cache_max=2)
    base.update(over)
    return Settings(**base)


async def test_router_isolates_and_caches(monkeypatch):
    reg = []
    monkeypatch.setattr("mygekko_mcp.backends.tenant.RestBackend", _fake_backend_factory(reg))
    router = TenantBackendRouter(RequestCredentialProvider(), _settings())

    tok = set_current_credentials(_creds(gekkoid="A-A-A-A"))
    try:
        r1 = await router.get_inventory()
        w1 = await router.write("lights", "item0", "1")
    finally:
        reset_current_credentials(tok)
    assert r1.gekkoid == "A-A-A-A" and w1 == "OK A-A-A-A"

    # same credentials -> reused backend (no new instance)
    tok = set_current_credentials(_creds(gekkoid="A-A-A-A"))
    try:
        await router.get_inventory()
    finally:
        reset_current_credentials(tok)
    assert len(reg) == 1

    # different tenant -> its own backend, its own gekkoid
    tok = set_current_credentials(_creds(gekkoid="B-B-B-B"))
    try:
        r2 = await router.get_inventory()
    finally:
        reset_current_credentials(tok)
    assert r2.gekkoid == "B-B-B-B" and len(reg) == 2


async def test_router_requires_credentials(monkeypatch):
    reg = []
    monkeypatch.setattr("mygekko_mcp.backends.tenant.RestBackend", _fake_backend_factory(reg))
    router = TenantBackendRouter(RequestCredentialProvider(), _settings())
    with pytest.raises(CredentialError):
        await router.get_inventory()  # no context bound


async def test_router_evicts_and_closes_over_cap(monkeypatch):
    reg = []
    monkeypatch.setattr("mygekko_mcp.backends.tenant.RestBackend", _fake_backend_factory(reg))
    router = TenantBackendRouter(RequestCredentialProvider(), _settings(tenant_cache_max=2))
    for gk in ("A-A-A-A", "B-B-B-B", "C-C-C-C"):
        tok = set_current_credentials(_creds(gekkoid=gk))
        try:
            await router.get_inventory()
        finally:
            reset_current_credentials(tok)
    # first (A) evicted and closed; 2 remain cached
    assert reg[0].creds.gekkoid == "A-A-A-A" and reg[0].closed is True
    assert sum(1 for b in reg if not b.closed) == 2


# --- ASGI middleware --------------------------------------------------------
async def test_middleware_binds_and_resets():
    seen = {}

    async def inner(scope, receive, send):
        seen["creds"] = get_current_credentials()

    mw = TenantCredentialsMiddleware(inner, prefix="X-MyGEKKO-")
    scope = {
        "type": "http",
        "headers": [
            (b"x-mygekko-username", b"user@example.com"),
            (b"x-mygekko-key", b"secret-key"),
            (b"x-mygekko-gekkoid", b"aaaa-bbbb-cccc-dddd"),
        ],
    }
    await mw(scope, None, None)
    assert seen["creds"].gekkoid == "AAAA-BBBB-CCCC-DDDD"
    assert get_current_credentials() is None  # reset after the request


async def test_middleware_missing_headers_binds_none():
    seen = {}

    async def inner(scope, receive, send):
        seen["creds"] = get_current_credentials()

    mw = TenantCredentialsMiddleware(inner)
    await mw({"type": "http", "headers": []}, None, None)
    assert seen["creds"] is None  # deferred to the tool call


async def test_middleware_passes_non_http():
    called = {}

    async def inner(scope, receive, send):
        called["ok"] = True

    mw = TenantCredentialsMiddleware(inner)
    await mw({"type": "lifespan"}, None, None)
    assert called["ok"]

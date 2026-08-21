"""RestBackend tests: inventory cache, decoded reads, gated write mechanism."""

import httpx
import respx

from mygekko_mcp.backends import RestBackend
from mygekko_mcp.config import Mode, Settings
from mygekko_mcp.credentials import Credentials, StaticCredentialProvider
from mygekko_mcp.security import AsyncRateLimiter

BASE = "https://live.my-gekko.com/api/v1"
AUTH = {"username": "u@example.com", "key": "SECRET", "gekkoid": "AAAA-BBBB-CCCC-DDDD"}
CREDS = StaticCredentialProvider(
    Credentials(Mode.cloud, BASE, AUTH, "AAAA-BBBB-CCCC-DDDD", "u@example.com")
)


def _backend() -> RestBackend:
    settings = Settings(**{k: v for k, v in AUTH.items()})
    # No burst throttling in tests.
    return RestBackend(CREDS, settings, rate_limiter=AsyncRateLimiter(1000, 1000))


@respx.mock
async def test_inventory_cached_and_indexed(example_var):
    route = respx.get(f"{BASE}/var").mock(return_value=httpx.Response(200, json=example_var))
    be = _backend()
    inv1 = await be.get_inventory()
    inv2 = await be.get_inventory()  # cached: no second HTTP call
    assert inv1 is inv2
    assert route.call_count == 1
    item = await be.find_item("lights/item0")
    assert item is not None and item.name == "light 1"
    await be.aclose()


@respx.mock
async def test_read_system_decodes_values(example_var):
    respx.get(f"{BASE}/var").mock(return_value=httpx.Response(200, json=example_var))
    respx.get(f"{BASE}/var/lights/status").mock(
        return_value=httpx.Response(
            200, json={"lights": {"item0": {"sumstate": {"value": "1;60;0;80;0"}}}}
        )
    )
    be = _backend()
    states = await be.read_system("lights")
    assert states["lights/item0"]["currentState"]["value"] == "on"
    assert states["lights/item0"]["dimLevel"]["value"] == 60.0
    await be.aclose()


@respx.mock
async def test_read_system_handles_branch_shape(example_var):
    # A branch query (var/lights/status) returns items at the TOP level, with no
    # {"lights": ...} wrapper (verified against the live demo controller).
    respx.get(f"{BASE}/var").mock(return_value=httpx.Response(200, json=example_var))
    respx.get(f"{BASE}/var/lights/status").mock(
        return_value=httpx.Response(200, json={"item0": {"sumstate": {"value": "1;60;0;80;0"}}})
    )
    be = _backend()
    states = await be.read_system("lights")
    assert states["lights/item0"]["currentState"]["value"] == "on"
    await be.aclose()


@respx.mock
async def test_write_hits_scmd_set(example_var):
    respx.get(f"{BASE}/var").mock(return_value=httpx.Response(200, json=example_var))
    route = respx.get(f"{BASE}/var/lights/item0/scmd/set").mock(
        return_value=httpx.Response(200, text="OK")
    )
    be = _backend()
    result = await be.write("lights", "item0", "D50")
    assert result == "OK"
    assert route.calls.last.request.url.params["value"] == "D50"
    await be.aclose()

"""Server-wiring smoke tests: correct tools/resources registered per config."""

from _fakes import FakeBackend

from mygekko_mcp.config import Settings
from mygekko_mcp.model.inventory import build_inventory
from mygekko_mcp.server import create_server


def _backend(example_var) -> FakeBackend:
    return FakeBackend(build_inventory(example_var), {})


def _settings(allow_write: bool) -> Settings:
    return Settings(
        username="u@example.com", key="K", gekkoid="AAAA-BBBB-CCCC-DDDD", allow_write=allow_write
    )


async def test_read_only_registers_no_write_tools(example_var):
    mcp = create_server(_backend(example_var), _settings(allow_write=False))
    names = {t.name for t in await mcp.list_tools()}
    # discovery + reads are available even read-only
    assert {"list_devices", "get_state", "describe_capabilities"} <= names
    assert not any(n.startswith("control_") for n in names)


async def test_write_enabled_registers_control_tools(example_var):
    mcp = create_server(_backend(example_var), _settings(allow_write=True))
    names = {t.name for t in await mcp.list_tools()}
    assert {
        "control_light",
        "control_blind",
        "control_load",
        "control_room_temp",
        "control_hotwater",
        "control_heating_circuit",
        "control_vent",
        "control_pool",
        "control_action",
        "control_device",
    } <= names


async def test_capabilities_resource_registered(example_var):
    mcp = create_server(_backend(example_var), _settings(allow_write=False))
    resources = {str(r.uri) for r in await mcp.list_resources()}
    assert "gekko://capabilities" in resources


async def test_resources_registered(example_var):
    mcp = create_server(_backend(example_var), _settings(allow_write=False))
    templates = {t.uriTemplate for t in await mcp.list_resource_templates()}
    # templated resources (with path params)
    assert any("gekko://system/" in t for t in templates)
    assert any("gekko://item/" in t for t in templates)


def _token_auth_settings(**overrides) -> Settings:
    base = dict(
        username="u@example.com",
        key="K",
        gekkoid="AAAA-BBBB-CCCC-DDDD",
        resolve_url="https://app/api/mcp-auth/resolve",
        resolve_secret="S",
    )
    base.update(overrides)
    return Settings(**base)


async def test_history_tools_absent_without_token_auth(example_var):
    # No resolve_url/resolve_secret set -> token_auth_enabled is False -> no history tools,
    # even though nothing else is misconfigured.
    mcp = create_server(_backend(example_var), _settings(allow_write=False))
    names = {t.name for t in await mcp.list_tools()}
    assert not ({"get_history", "get_event_summary"} & names)


async def test_history_tools_registered_when_token_auth_and_history_url_present(example_var):
    mcp = create_server(_backend(example_var), _token_auth_settings())
    names = {t.name for t in await mcp.list_tools()}
    # history_base_url_effective derives from resolve_url automatically — no extra setting needed.
    assert {"get_history", "get_event_summary"} <= names


async def test_history_tools_absent_when_history_url_cannot_be_derived(example_var):
    # resolve_url set but not shaped like ".../mcp-auth/resolve" -> history_base_url_effective is ""
    # -> tools stay unregistered rather than pointing at a guessed/wrong URL.
    mcp = create_server(
        _backend(example_var), _token_auth_settings(resolve_url="https://app/weird-path")
    )
    names = {t.name for t in await mcp.list_tools()}
    assert not ({"get_history", "get_event_summary"} & names)

"""Capability discovery: decode a controller's grammar into what it can do."""

from mygekko_mcp.model.capabilities import (
    TYPED_TOOLS,
    describe_item,
    summarize_capabilities,
    unhandled_writable_systems,
)
from mygekko_mcp.model.commands import _BUILDERS
from mygekko_mcp.model.inventory import build_inventory


def test_typed_tools_cover_every_builder():
    # Discovery must name a tool for every system that has a dedicated builder,
    # otherwise a typed system would be mislabelled as generic.
    assert set(TYPED_TOOLS) == set(_BUILDERS)


def test_describe_item_decodes_commands(example_var):
    inv = build_inventory(example_var)
    light = inv.system_map()["lights"].all_entries[0]
    desc = describe_item(light)
    assert desc["typed_tool"] == "control_light"
    by_token = {c["token"]: c for c in desc["commands"]}
    assert by_token["1"]["type"] == "literal"
    dim = by_token["D100"]
    assert dim["type"] == "value" and dim["send"] == "D<value>"


def test_summarize_marks_control_kind(example_var):
    inv = build_inventory(example_var)
    cap = summarize_capabilities(inv)
    kinds = {o["system"]: o["control"] for o in cap["systems"]}
    assert kinds["lights"] == "typed"
    # a writable system without a typed builder is generic-controllable
    assert kinds.get("saunas") == "generic"
    # a read-only system is neither
    assert kinds.get("stoves") == "read-only"
    assert cap["summary"]["systems"] == len(cap["systems"])
    assert "lights" in cap["controllable"]
    assert "stoves" not in cap["controllable"]


def test_summarize_single_system(example_var):
    inv = build_inventory(example_var)
    cap = summarize_capabilities(inv, system="vents")
    assert [o["system"] for o in cap["systems"]] == ["vents"]


def test_unhandled_lists_generic_only_systems(example_var):
    inv = build_inventory(example_var)
    unhandled = unhandled_writable_systems(inv)
    assert "saunas" in unhandled  # writable, no typed builder
    assert "lights" not in unhandled  # typed
    assert "stoves" not in unhandled  # read-only

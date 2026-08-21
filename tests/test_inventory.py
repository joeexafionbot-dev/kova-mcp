"""Inventory builder tests against the official wiki example /var tree."""

from mygekko_mcp.model.inventory import build_inventory


def test_build_inventory_finds_systems(example_var):
    inv = build_inventory(example_var)
    keys = set(inv.system_map())
    # A representative spread of gewerke must be present.
    assert {"lights", "blinds", "roomtemps", "loads", "alarmsystem"} <= keys
    # globals is handled separately, not as a SystemGroup.
    assert "globals" not in keys


def test_lights_item_parsed(example_var):
    inv = build_inventory(example_var)
    lights = inv.system_map()["lights"]
    item0 = next(i for i in lights.items if i.resource_id == "item0")
    assert item0.name == "light 1"
    assert item0.page == "1"
    assert item0.path == "lights/item0"
    assert item0.writable is True
    assert next(f.name for f in item0.read_fields) == "currentState"


def test_groups_separated_from_items(example_var):
    inv = build_inventory(example_var)
    lights = inv.system_map()["lights"]
    assert any(g.resource_id == "group0" for g in lights.groups)
    assert all(i.resource_id.startswith("item") for i in lights.items)


def test_read_only_systems_have_no_commands(example_var):
    inv = build_inventory(example_var)
    energy = inv.system_map().get("energymanager")
    assert energy is not None
    assert all(not it.writable for it in energy.all_entries)


def test_globals_flattened(example_var):
    inv = build_inventory(example_var)
    assert any(k.startswith("meteo/") for k in inv.globals_vars)
    temp = inv.globals_vars.get("meteo/temperature")
    assert temp is not None
    assert temp["unit"] == "°C"


def test_counts_and_writables(example_var):
    inv = build_inventory(example_var)
    counts = inv.counts()
    assert counts.get("lights", 0) >= 1
    assert len(inv.writable_items()) >= 1

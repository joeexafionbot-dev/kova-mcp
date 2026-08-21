"""Command-builder tests against the real fixture grammars for lights & blinds."""

import pytest

from mygekko_mcp.model.commands import CommandError, build_command, build_generic_command
from mygekko_mcp.model.inventory import InventoryItem, build_inventory
from mygekko_mcp.model.semantics import parse_scmd_format


def _item(example_var, system, resource_id="item0"):
    inv = build_inventory(example_var)
    return next(i for i in inv.system_map()[system].all_entries if i.resource_id == resource_id)


def _mk(system: str, scmd: str) -> InventoryItem:
    """Build an item straight from a scmd grammar (for systems the fixture
    ships as read-only, e.g. heatingcircuits)."""
    return InventoryItem(
        system=system,
        resource_id="item0",
        kind="item",
        name=system,
        commands=parse_scmd_format(scmd),
    )


def test_light_on_off_toggle(example_var):
    light = _item(example_var, "lights")
    assert build_command(light, "on").token == "1"
    assert build_command(light, "off").token == "0"
    assert build_command(light, "toggle").token == "T"


def test_light_dim(example_var):
    light = _item(example_var, "lights")
    cmd = build_command(light, "dim", level=50)
    assert cmd.token == "D50"
    assert "50%" in cmd.description
    # fractional level keeps one decimal
    assert build_command(light, "dim", level=33.5).token == "D33.5"


def test_light_dim_out_of_range(example_var):
    light = _item(example_var, "lights")
    with pytest.raises(CommandError):
        build_command(light, "dim", level=150)


def test_light_unknown_action(example_var):
    light = _item(example_var, "lights")
    with pytest.raises(CommandError):
        build_command(light, "explode")


def test_blind_movements(example_var):
    blind = _item(example_var, "blinds")
    assert build_command(blind, "up").token == "1"
    assert build_command(blind, "down").token == "-1"
    assert build_command(blind, "stop").token == "0"
    assert build_command(blind, "hold_down").token == "-2"


def test_blind_position_and_angle(example_var):
    blind = _item(example_var, "blinds")
    assert build_command(blind, "position", position=60).token == "P60"
    assert build_command(blind, "angle", angle=45).token == "S45"


def test_load_on_off_toggle(example_var):
    load = _item(example_var, "loads")  # grammar 0|1|2|T
    # "on" prefers OnPermanent (2) when advertised
    assert build_command(load, "on").token == "2"
    assert build_command(load, "on_impulse").token == "1"
    assert build_command(load, "off").token == "0"
    assert build_command(load, "toggle").token == "T"


def test_load_unknown_action(example_var):
    load = _item(example_var, "loads")
    with pytest.raises(CommandError):
        build_command(load, "dim")


def test_roomtemp_setpoint(example_var):
    room = _item(example_var, "roomtemps")  # ...|K2.4|S22.5
    cmd = build_command(room, "setpoint", setpoint=21.5)
    assert cmd.token == "S21.5"
    assert build_command(room, "setpoint", setpoint=22).token == "S22"


def test_roomtemp_setpoint_out_of_band(example_var):
    room = _item(example_var, "roomtemps")
    with pytest.raises(CommandError):
        build_command(room, "setpoint", setpoint=99)


def test_roomtemp_mode_is_literal_token(example_var):
    room = _item(example_var, "roomtemps")
    assert build_command(room, "mode", mode="comfort").token == "M8"
    assert build_command(room, "mode", mode="off").token == "M1"
    with pytest.raises(CommandError):
        build_command(room, "mode", mode="turbo")


def test_roomtemp_adjust(example_var):
    room = _item(example_var, "roomtemps")
    assert build_command(room, "adjust", delta=-1.5).token == "K-1.5"


def test_hotwater_on_off_setpoint(example_var):
    hw = _item(example_var, "hotwater_systems")  # 0|1|T100
    assert build_command(hw, "on").token == "1"
    assert build_command(hw, "off").token == "0"
    assert build_command(hw, "setpoint", setpoint=55).token == "T55"
    with pytest.raises(CommandError):
        build_command(hw, "setpoint", setpoint=5)  # below the hotwater band


def test_heating_on_off_auto_setpoint():
    hc = _mk("heatingcircuits", "0|1|A|T100 (Off|On|Auto|Temperature[°C])")
    assert build_command(hc, "on").token == "1"
    assert build_command(hc, "off").token == "0"
    assert build_command(hc, "auto").token == "A"
    assert build_command(hc, "setpoint", setpoint=45).token == "T45"
    with pytest.raises(CommandError):
        build_command(hc, "setpoint", setpoint=90)  # above heating band


VENT_SCMD = (
    "-1|1|2|3|4|T|BY0|BY1|BY2|C0|C1|D0|D1|Mx "
    "(Off|Level1|Level2|Level3|Level4|Toggle|BypassAuto|BypassManual|BypassSummer|"
    "Cool.Off|Cool.On|Dehumid.Off|Dehumid.On|OperatingMode[0..x])"
)


def test_vent_off_toggle_and_levels(example_var):
    vent = _item(example_var, "vents")
    assert build_command(vent, "off").token == "-1"
    assert build_command(vent, "toggle").token == "T"
    assert build_command(vent, "level", level=3).token == "3"
    with pytest.raises(CommandError):
        build_command(vent, "level", level=7)
    with pytest.raises(CommandError):
        build_command(vent, "level")  # missing level


def test_vent_bypass_and_switches(example_var):
    vent = _item(example_var, "vents")
    assert build_command(vent, "bypass", mode="summer").token == "BY2"
    assert build_command(vent, "cooling_on").token == "C1"
    assert build_command(vent, "cooling_off").token == "C0"
    assert build_command(vent, "dehumidify_on").token == "D1"
    with pytest.raises(CommandError):
        build_command(vent, "bypass", mode="turbo")


def test_vent_bypass_literals_are_not_value_params():
    # Guards the parser fix end-to-end: BY0/BY1/BY2 must be literals, so the
    # builder emits them verbatim rather than treating "BY" as a value prefix.
    vent = _mk("vents", VENT_SCMD)
    assert build_command(vent, "bypass", mode="auto").token == "BY0"


def test_pool_mode_setpoint_and_cleanfilter(example_var):
    pool = _item(example_var, "pools")  # M0|M1|M2|C1|C2|C3|T100
    assert build_command(pool, "mode", mode="off").token == "M0"
    assert build_command(pool, "mode", mode="bathing").token == "M2"
    assert build_command(pool, "mode", mode="standby").token == "M1"  # alias -> restMode
    assert build_command(pool, "setpoint", setpoint=28).token == "T28"
    assert build_command(pool, "clean_filter", level=2).token == "C2"
    with pytest.raises(CommandError):
        build_command(pool, "clean_filter", level=5)
    with pytest.raises(CommandError):
        build_command(pool, "setpoint", setpoint=80)  # above pool band
    with pytest.raises(CommandError):
        build_command(pool, "mode", mode="party")


def test_action_start_stop_toggle(example_var):
    act = _item(example_var, "actions")  # -1|1|T
    assert build_command(act, "start").token == "1"
    assert build_command(act, "stop").token == "-1"
    assert build_command(act, "toggle").token == "T"
    with pytest.raises(CommandError):
        build_command(act, "explode")


def test_unsupported_system_points_to_generic(example_var):
    # saunas has no dedicated builder -> explicit error steering to control_device.
    sauna = _item(example_var, "saunas")
    with pytest.raises(CommandError) as exc:
        build_command(sauna, "on")
    assert "control_device" in str(exc.value)


# --- generic, grammar-driven control (controller-agnostic) ------------------
def test_generic_literal_token_by_exact_and_label():
    sauna = _mk("saunas", "0|1|2 (Off|On/Finnish|Bio)")
    assert build_generic_command(sauna, "1").token == "1"
    assert build_generic_command(sauna, "0").token == "0"
    assert build_generic_command(sauna, "Bio").token == "2"  # matched by label


def test_generic_value_command_by_prefix_and_example():
    multi = _mk(
        "multirooms",
        "PLAY|STOP|T|N+1|C1|V24.5 (Play|Stop|Toggle|ChangeSong|ChangePlaylist|ChangeVolume)",
    )
    assert build_generic_command(multi, "PLAY").token == "PLAY"
    assert build_generic_command(multi, "N+1").token == "N+1"  # literal with '+'
    assert build_generic_command(multi, "V", value=30).token == "V30"
    assert build_generic_command(multi, "ChangeVolume", value=12.5).token == "V12.5"


def test_generic_value_requires_value():
    hw = _mk("hotwater_systems", "0|1|T100 (Off|On|Temperature[°C])")
    with pytest.raises(CommandError):
        build_generic_command(hw, "T")  # value missing


def test_generic_placeholder_x_command():
    emob = _mk(
        "emobils",
        "1|-1|CS4.6|Ax "
        "(startCharge|stopCharge|chargePowerSetpoint[kW]|"
        "changePlugBehaviour[0=noAction,1=fullCharge,2=partialCharge])",
    )
    assert build_generic_command(emob, "1").token == "1"
    assert build_generic_command(emob, "A", value=2).token == "A2"  # Ax -> A + value
    assert build_generic_command(emob, "CS", value=4.6).token == "CS4.6"


def test_generic_unknown_command_lists_available():
    sauna = _mk("saunas", "0|1|2 (Off|On/Finnish|Bio)")
    with pytest.raises(CommandError) as exc:
        build_generic_command(sauna, "explode")
    assert "Available" in str(exc.value)


def test_generic_works_for_a_typed_system_too():
    # generic control is a valid fallback even where a typed builder exists
    light = _mk("lights", "1|0|D100|T (On,Off|Dim%|Toggle)")
    assert build_generic_command(light, "D", value=40).token == "D40"
    assert build_generic_command(light, "1").token == "1"


def test_built_command_path(example_var):
    light = _item(example_var, "lights")
    assert build_command(light, "on").path == "lights/item0"

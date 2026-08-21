"""Unit tests for the value-grammar parsers, checked against real myGEKKO formats."""

from mygekko_mcp.model.semantics import (
    interpret_value,
    parse_scmd_format,
    parse_sumstate_format,
)

LIGHT_SUMSTATE = (
    "currentState enum[0=off,1=on](); dimLevel float[0.00,100.0](%); "
    "rgbColor int[24bit->R:23-16|G:15-8|B:7-0](); tunableWhiteLevel float[0.00,100.0](%); "
    "elementInfo enum[0=ok,1=manualOff,2=manualOn,3=locked,4=alarm](); "
)
LIGHT_SCMD = "1|0|D100|T|C44782|TW100 (On,Off|0-100[%]|1|24BitRGB|0-100[%])"
BLIND_SCMD = "-2|-1|0|1|2|T|P55.4|S32.4 (Hold_down|Down|Stop|Up|Hold_up|Toggle|Position|Angle)"
ROOMTEMP_SUMSTATE_WORKINGMODE = (
    "workingMode #standard:enum[1=off,8=comfort,16=reduced,64=manual,256=standby]"
    "#knx:enum[0=auto,1=comfort,2=standby,3=economy,4=buildingProtection]()"
)


def test_parse_light_sumstate_fields():
    fields = parse_sumstate_format(LIGHT_SUMSTATE)
    names = [f.name for f in fields]
    assert names == [
        "currentState",
        "dimLevel",
        "rgbColor",
        "tunableWhiteLevel",
        "elementInfo",
    ]
    assert fields[0].kind == "enum"
    assert fields[0].enum_map == {0: "off", 1: "on"}
    assert fields[1].kind == "float"
    assert fields[1].unit == "%"
    assert fields[1].high == 100.0


def test_interpret_light_value_string():
    # Doc example: "0;60;0;" -> off, 60%, not locked
    fields = parse_sumstate_format(LIGHT_SUMSTATE)
    decoded = interpret_value(fields, "1;60;0;80;0")
    assert decoded["currentState"]["value"] == "on"
    assert decoded["dimLevel"]["value"] == 60.0
    assert decoded["dimLevel"]["unit"] == "%"
    assert decoded["elementInfo"]["value"] == "ok"


def test_interpret_value_array_form():
    fields = parse_sumstate_format(LIGHT_SUMSTATE)
    decoded = interpret_value(fields, [0, 60, " ", 0])
    assert decoded["currentState"]["value"] == "off"
    assert decoded["dimLevel"]["value"] == 60.0


def test_variant_field_flagged():
    fields = parse_sumstate_format(ROOMTEMP_SUMSTATE_WORKINGMODE)
    wm = fields[0]
    assert wm.name == "workingMode"
    assert wm.is_variant is True
    # first variant (standard) parsed for a best-effort enum map
    assert wm.enum_map.get(8) == "comfort"


def test_parse_light_scmd_commands():
    cmds = parse_scmd_format(LIGHT_SCMD)
    tokens = [c.token for c in cmds]
    assert tokens == ["1", "0", "D100", "T", "C44782", "TW100"]
    dim = next(c for c in cmds if c.token == "D100")
    assert dim.prefix == "D"
    assert dim.takes_arg is True
    on = next(c for c in cmds if c.token == "1")
    assert on.takes_arg is False


def test_parse_blind_scmd_position_and_angle():
    cmds = parse_scmd_format(BLIND_SCMD)
    stop = next(c for c in cmds if c.token == "0")
    assert stop.takes_arg is False and stop.label == "Stop"
    pos = next(c for c in cmds if c.token.startswith("P"))
    assert pos.prefix == "P" and pos.takes_arg is True and pos.label == "Position"


ROOMTEMP_SCMD = (
    "M1|M2|M8|M16|M64|M256|K2.4|S22.5 "
    "(ModeOff|ModeOn|ModeComfort|ModeReduced|ModeManual|ModeStandby|"
    "TempAdjustment[°C]|Setpoint[°C])"
)
POOL_SCMD = "M0|M1|M2|C1|C2|C3|T100 (off|restMode|bathing|f1|f2|f3|setTemperature)"


def test_repeated_prefix_tokens_are_literals():
    # roomtemps M1..M256 are enum modes, NOT "M + value" (unlike D100).
    cmds = {c.token: c for c in parse_scmd_format(ROOMTEMP_SCMD)}
    for mode in ("M1", "M8", "M256"):
        assert cmds[mode].takes_arg is False
        assert cmds[mode].prefix == mode  # literal, prefix is the whole token
    # unique-prefix value setters stay parameterised
    assert cmds["S22.5"].prefix == "S" and cmds["S22.5"].takes_arg is True
    assert cmds["K2.4"].prefix == "K" and cmds["K2.4"].takes_arg is True


def test_pool_mode_and_channel_literals_with_value_setter():
    cmds = {c.token: c for c in parse_scmd_format(POOL_SCMD)}
    for lit in ("M0", "M1", "M2", "C1", "C2", "C3"):
        assert cmds[lit].takes_arg is False
    assert cmds["T100"].prefix == "T" and cmds["T100"].takes_arg is True


def test_placeholder_x_tokens_take_a_value():
    # "Mx"/"Ax" use a literal 'x' as the 0..x placeholder -> prefix + value.
    cmds = {c.token: c for c in parse_scmd_format("1|-1|CS4.6|Ax (start|stop|power|plugBehaviour)")}
    assert cmds["Ax"].prefix == "A" and cmds["Ax"].takes_arg is True
    assert cmds["CS4.6"].prefix == "CS" and cmds["CS4.6"].takes_arg is True
    assert cmds["1"].takes_arg is False


def test_scmd_none_returns_empty():
    assert parse_scmd_format(None) == []
    assert parse_scmd_format("None") == []

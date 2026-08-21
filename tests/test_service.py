"""GekkoService tests: read shaping + the write gate / dry-run / audit path."""

from _fakes import FakeBackend

from mygekko_mcp.config import Settings
from mygekko_mcp.model.inventory import build_inventory
from mygekko_mcp.model.semantics import interpret_value
from mygekko_mcp.security import AuditLog, WritePolicy
from mygekko_mcp.service import GekkoService


def _service(
    example_var, tmp_path, *, allow_write: bool, write_result: str = "OK"
) -> tuple[GekkoService, FakeBackend]:
    inv = build_inventory(example_var)
    light = inv.system_map()["lights"].all_entries[0]
    states = {light.path: interpret_value(light.read_fields, "1;60;0;80;0")}
    backend = FakeBackend(inv, states, write_result=write_result)
    settings = Settings(
        username="u@example.com", key="K", gekkoid="AAAA-BBBB-CCCC-DDDD", allow_write=allow_write
    )
    svc = GekkoService(backend, WritePolicy(settings), AuditLog(tmp_path / "audit.jsonl"))
    return svc, backend


async def test_list_devices(example_var, tmp_path):
    svc, _ = _service(example_var, tmp_path, allow_write=False)
    out = await svc.list_devices("lights")
    assert "lights" in out["systems"]
    assert out["systems"]["lights"][0]["path"] == "lights/item0"


async def test_get_state_decoded(example_var, tmp_path):
    svc, _ = _service(example_var, tmp_path, allow_write=False)
    st = await svc.get_state("lights/item0")
    assert st["state"]["currentState"] == "on"
    assert st["state"]["dimLevel"] == 60.0


async def test_control_blocked_when_writes_disabled(example_var, tmp_path):
    svc, backend = _service(example_var, tmp_path, allow_write=False)
    res = await svc.control("lights/item0", "on")
    assert not res.ok and not res.executed
    assert "disabled" in res.message
    assert backend.writes == []  # nothing sent


async def test_control_executes_when_enabled(example_var, tmp_path):
    svc, backend = _service(example_var, tmp_path, allow_write=True)
    res = await svc.control("lights/item0", "dim", level=50)
    assert res.ok and res.executed and res.token == "D50"
    assert backend.writes == [("lights", "item0", "D50")]


async def test_control_ok_when_cloud_returns_empty_object(example_var, tmp_path):
    # The cloud Query API answers scmd/set with "{}" (not "OK") on success.
    svc, backend = _service(example_var, tmp_path, allow_write=True, write_result="{}")
    res = await svc.control("lights/item0", "on")
    assert res.ok and res.executed and res.token == "1"
    assert backend.writes == [("lights", "item0", "1")]


async def test_control_dry_run_does_not_execute(example_var, tmp_path):
    svc, backend = _service(example_var, tmp_path, allow_write=True)
    res = await svc.control("lights/item0", "on", dry_run=True)
    assert res.ok and not res.executed and res.token == "1"
    assert backend.writes == []
    assert "Dry-run" in res.message


async def test_control_invalid_action_rejected(example_var, tmp_path):
    svc, backend = _service(example_var, tmp_path, allow_write=True)
    res = await svc.control("lights/item0", "dim")  # missing level
    assert not res.ok and backend.writes == []


async def test_control_generic_sends_advertised_token(example_var, tmp_path):
    # saunas has no typed builder -> generic control must still work.
    svc, backend = _service(example_var, tmp_path, allow_write=True)
    res = await svc.control_generic("saunas/item0", "1")
    assert res.ok and res.executed and res.token == "1"
    assert backend.writes and backend.writes[-1][0] == "saunas"


async def test_control_generic_respects_write_gate(example_var, tmp_path):
    svc, backend = _service(example_var, tmp_path, allow_write=False)
    res = await svc.control_generic("saunas/item0", "1")
    assert not res.ok and not res.executed
    assert backend.writes == []


async def test_control_generic_rejects_unadvertised(example_var, tmp_path):
    svc, backend = _service(example_var, tmp_path, allow_write=True)
    res = await svc.control_generic("saunas/item0", "ZZZ")
    assert not res.ok and backend.writes == []
    assert "not an advertised command" in res.message


async def test_describe_capabilities(example_var, tmp_path):
    svc, _ = _service(example_var, tmp_path, allow_write=False)
    cap = await svc.describe_capabilities()
    assert cap["summary"]["systems"] > 0
    assert "lights" in cap["controllable"]


async def test_audit_written_on_write(example_var, tmp_path):
    svc, _ = _service(example_var, tmp_path, allow_write=True)
    await svc.control("lights/item0", "on")
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "lights/item0" in audit and '"command": "1"' in audit

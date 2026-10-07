"""issue #487: a driver copied from the packaged reference drivers
(`shal_arena/reference_drivers/`) is labelled `driver_origin:
"reference-copy"` -- on that instrument's tile, in the run state and in the
`--json` reply of `check`/`measure`/`call` -- and the page says "copied from
the reference driver" for it instead of "The driver the agent wrote"."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from shal import registry

from shal_arena import runner
from shal_arena.cli import _parse_driver_args, main
from shal_arena.loader import once_per_path
from shal_arena.origin import REFERENCE_COPY_THRESHOLD, REFERENCE_DRIVERS, driver_origin
from shal_arena.store import RunStore
from shal_arena.ui.page import _SCRIPT

from .conftest import FIXTURES, PASSING_DRIVER, SAMPLE_TASK
from .test_ui_followup import _BASE_PAYLOAD
from .test_ui_timeline_no_exchange import _DOM_STUB

OWN_DMM_DRIVER = FIXTURES / "drivers" / "own_dmm_driver.py"

#: the reference copy below has no `override=True` (it is verbatim), so each
#: test starts with an empty slot for its compatible -- same reason as
#: test_ui_play.py's own fixture.
_COMPATIBLES = ("arena,bench-psu1", "arena,bench-dmm1")

#: `reference_drivers/dmm_driver.py`, renamed throughout (class, op, local
#: variable), comments and docstrings gone, whitespace changed.
_RENAMED_DMM_COPY = '''\
from __future__ import annotations
from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport
class MyOwnMeter(Driver):
    compatible   =   "arena,bench-dmm1"
    kind = MessageTransport
    llm_ready = True
    @idempotent
    @op("Read the measured DC voltage now.", unit="volt", side_effect="none")
    def get_volts(self) -> float:
        answer_back = self.bus.exchange(self.addr,
                                        {"scpi": "MEAS:VOLT:DC?", "query": True})
        return float(answer_back["reply"])
registry.register(MyOwnMeter)
'''


@pytest.fixture(autouse=True)
def _clean_slots(monkeypatch):
    saved = {c: list(registry._entries.get(c, [])) for c in _COMPATIBLES}
    for c in saved:
        registry._entries[c] = []
    # one class per file across check + measure, the way `bench` and the
    # Play server already import a driver (`loader.once_per_path`)
    monkeypatch.setattr(runner, "_import_driver_file", once_per_path(runner._import_driver_file))
    try:
        yield
    finally:
        for c, candidates in saved.items():
            registry._entries[c] = candidates


def _cli_json(capsys, *args: str) -> dict:
    capsys.readouterr()
    main([*args, "--json"])
    return json.loads(capsys.readouterr().out)


def _play(tmp_path: Path, capsys, dmm_driver: Path) -> tuple[str, dict, dict]:
    state_dir = str(tmp_path / "state")
    run_id = _cli_json(capsys, "run", str(SAMPLE_TASK), "--state-dir", state_dir)["run_id"]
    checked = _cli_json(capsys, "check", run_id, "dmm0", str(dmm_driver),
                        "--state-dir", state_dir)
    _cli_json(capsys, "drive", run_id, "psu0", "5.0", "--state-dir", state_dir)
    measured = _cli_json(capsys, "measure", run_id, "dmm0", str(dmm_driver),
                         "--state-dir", state_dir)
    return run_id, checked, measured


def _assert_marked(tmp_path: Path, run_id: str, origin: str) -> None:
    state = RunStore(tmp_path / "state").load(run_id)
    assert state.tiles["dmm0"].driver_origin == origin
    assert state.driver_origins["dmm0"]["driver_origin"] == origin
    doc = json.loads((tmp_path / "state" / f"{run_id}.json").read_text(encoding="utf-8"))
    assert doc["tiles"]["dmm0"]["driver_origin"] == origin
    assert doc["driver_origins"]["dmm0"]["driver_origin"] == origin


def test_a_verbatim_copy_of_the_reference_dmm_driver_is_a_reference_copy(
        tmp_path: Path, capsys) -> None:
    copy = tmp_path / "driver.py"
    shutil.copyfile(REFERENCE_DRIVERS["dmm"], copy)
    run_id, checked, measured = _play(tmp_path, capsys, copy)

    for reply in (checked, measured):
        assert reply["ok"] is True, reply
        assert reply["driver_origin"] == "reference-copy"
        assert reply["similarity"] == 1.0
    _assert_marked(tmp_path, run_id, "reference-copy")
    assert RunStore(tmp_path / "state").load(run_id).tiles["dmm0"].similarity == 1.0


def test_a_renamed_copy_is_still_a_reference_copy(tmp_path: Path, capsys) -> None:
    copy = tmp_path / "my_meter.py"
    copy.write_text(_RENAMED_DMM_COPY, encoding="utf-8")
    run_id, checked, measured = _play(tmp_path, capsys, copy)

    for reply in (checked, measured):
        assert reply["ok"] is True, reply
        assert reply["driver_origin"] == "reference-copy"
        assert reply["similarity"] >= REFERENCE_COPY_THRESHOLD
    _assert_marked(tmp_path, run_id, "reference-copy")


def test_a_driver_written_differently_for_the_same_case_is_the_agents(
        tmp_path: Path, capsys) -> None:
    run_id, checked, measured = _play(tmp_path, capsys, OWN_DMM_DRIVER)
    assert checked["passed"] is True, checked

    for reply in (checked, measured):
        assert reply["ok"] is True, reply
        assert reply["driver_origin"] == "agent"
        assert reply["similarity"] < REFERENCE_COPY_THRESHOLD
    _assert_marked(tmp_path, run_id, "agent")


def test_a_reference_copy_marks_that_instrument_only(tmp_path: Path, capsys) -> None:
    copy = tmp_path / "driver.py"
    shutil.copyfile(REFERENCE_DRIVERS["dmm"], copy)
    run_id, _checked, _measured = _play(tmp_path, capsys, copy)
    psu = _cli_json(capsys, "check", run_id, "psu0", str(PASSING_DRIVER),
                    "--state-dir", str(tmp_path / "state"))
    state = RunStore(tmp_path / "state").load(run_id)
    assert state.tiles["dmm0"].driver_origin == "reference-copy"
    assert state.tiles["psu0"].driver_origin == psu["driver_origin"]
    assert state.driver_origins["psu0"]["driver_origin"] == psu["driver_origin"]


def test_call_carries_driver_origin(tmp_path: Path, capsys) -> None:
    copy = tmp_path / "driver.py"
    shutil.copyfile(REFERENCE_DRIVERS["dmm"], copy)
    state_dir = str(tmp_path / "state")
    run_id = _cli_json(capsys, "run", str(SAMPLE_TASK), "--state-dir", state_dir)["run_id"]
    called = _cli_json(capsys, "call", run_id, "dmm0", str(copy), "measure_voltage",
                       "--state-dir", state_dir)
    assert called["driver_origin"] == "reference-copy"
    assert called["similarity"] == 1.0
    assert RunStore(state_dir).load(run_id).driver_origins["dmm0"]["driver_origin"] \
        == "reference-copy"


def test_driver_origin_never_executes_the_file(tmp_path: Path) -> None:
    hostile = tmp_path / "driver.py"
    hostile.write_text("raise SystemExit('imported')\n", encoding="utf-8")
    assert driver_origin(hostile, "dmm")["driver_origin"] == "agent"


def _driver_summaries(payload: dict) -> list[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    script = (
        _DOM_STUB + _SCRIPT
        + "\nconst payload = " + json.dumps(payload) + ";"
        + '\nrender(Object.assign({}, payload, {mode: "live"}));'
        + '\nconst sec = document.getElementById("driver-code-section");'
        + "\nconsole.log(JSON.stringify(sec.children.filter(c => c.children.length === 2)"
        + ".map(c => c.children[0].textContent)));"
    )
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "render.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_page_says_copied_for_a_copy_and_wrote_for_an_agent_driver(
        tmp_path: Path) -> None:
    copy = tmp_path / "driver.py"
    shutil.copyfile(REFERENCE_DRIVERS["dmm"], copy)
    drivers = _parse_driver_args([f"dmm={copy}", f"meter={OWN_DMM_DRIVER}"])
    assert drivers["dmm"]["driver_origin"] == "reference-copy"
    assert drivers["meter"]["driver_origin"] == "agent"

    payload = dict(_BASE_PAYLOAD)
    payload["drivers"] = drivers
    copied, written = _driver_summaries(payload)
    assert "copied from the reference driver" in copied
    assert "The driver the agent wrote" not in copied
    assert written.startswith("The driver the agent wrote for the meter")
    assert "copied from the reference driver" not in written

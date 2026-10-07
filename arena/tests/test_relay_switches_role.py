"""issue #473: in `relay-rail`, `relay0` has its own role -- `switches:
card.vin` -- not `drives: card.vin`. The run JSON and the page tell the PSU
(the source that sets VIN) and the relay (which only turns VIN on/off) apart.

Only the key, the JSON field and the role text change: the relay's own ops
(`call relay0 ... set_relay`), its gate and its effect on the card do not."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from shal_arena import fault as fault_mod
from shal_arena.cli import main
from shal_arena.errors import CheckCouldNotRun, TaskFormatError
from shal_arena.loader import load_task
from shal_arena.runner import call_op, drive_input, start_run, take_measurement
from shal_arena.store import RunStore
from shal_arena.ui.data import run_payload
from shal_arena.ui.page import _SCRIPT

from .conftest import PASSING_DMM_DRIVER, PASSING_RELAY_DRIVER, PASSING_TEMP_DRIVER, RELAY_RAIL_TASK

_CARD = load_task(RELAY_RAIL_TASK).card


def _seed_for(fault_id: str, limit: int = 500) -> int:
    for seed in range(limit):
        if fault_mod.realized_fault(_CARD, seed).fault_id == fault_id:
            return seed
    raise AssertionError(f"no seed under {limit} realizes fault {fault_id!r}")


def _cli_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    code = main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# DoD 1: the run JSON
# --------------------------------------------------------------------------- #

def test_run_json_lists_relay0_as_switches_and_psu0_as_drives(tmp_path: Path) -> None:
    run = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)
    by_addr = {i["address"]: i for i in run["instruments"]}

    assert by_addr["relay0"]["switches"] == "card.vin"
    assert "drives" not in by_addr["relay0"]
    assert "probe" not in by_addr["relay0"]
    assert by_addr["psu0"]["drives"] == "card.vin"
    assert "switches" not in by_addr["psu0"]

    payload = run_payload(run["run_id"], state_dir=tmp_path)
    roles = {i["address"]: i["role"] for i in payload["instruments"]}
    assert roles["relay0"] == "switches card.vin"
    assert roles["psu0"] == "drives card.vin"


def test_cli_run_json_lists_relay0_as_switches(tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _cli_json(["run", "relay-rail", "--state-dir", str(tmp_path)], capsys)
    assert code == 0
    by_addr = {i["address"]: i for i in out["instruments"]}
    assert by_addr["relay0"]["switches"] == "card.vin"
    assert "drives" not in by_addr["relay0"]
    assert by_addr["psu0"]["drives"] == "card.vin"


# --------------------------------------------------------------------------- #
# DoD 2: the page's plain role text
# --------------------------------------------------------------------------- #

def test_page_role_text_is_switches_vin_on_off_for_relay0(tmp_path: Path) -> None:
    """No JS engine here (same as test_exchange_log's role test): the data
    the page's `instrumentRoleText` reads is checked, then the rule itself
    in `_SCRIPT`. `switches: card.vin` -> name "vin" -> "switches VIN on/off";
    `drives: card.vin` -> "powers VIN"."""
    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)["run_id"]
    payload = run_payload(run_id, state_dir=tmp_path)
    by_addr = {i["address"]: i for i in payload["instruments"]}
    assert by_addr["relay0"]["switches"] == "card.vin"
    assert by_addr["relay0"]["drives"] is None
    assert by_addr["psu0"]["drives"] == "card.vin"
    assert by_addr["psu0"]["switches"] is None

    start = _SCRIPT.index("function instrumentRoleText(")
    body = _SCRIPT[start:_SCRIPT.index("\n}\n", start)]
    assert "switches ${name.toUpperCase()} on/off" in body
    assert "powers ${name.toUpperCase()}" in body
    # `drives` is checked before `switches`, and they are separate branches
    assert body.index("inst.drives") < body.index("inst.switches")


# --------------------------------------------------------------------------- #
# DoD 3: exactly one of drives/probe/switches; `switches` names a card input
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("pair", [("drives", "switches"), ("probe", "switches"),
                                  ("drives", "probe")])
def test_two_wiring_keys_on_one_instrument_is_refused(pair: tuple[str, str],
                                                      minimal_task: Path) -> None:
    doc = yaml.safe_load(minimal_task.read_text())
    inst = doc["instruments"][0]
    inst.pop("drives")
    for key in pair:
        inst[key] = "card.vin"
    minimal_task.write_text(yaml.safe_dump(doc))
    with pytest.raises(TaskFormatError) as e:
        load_task(minimal_task)
    for key in ("drives", "probe", "switches"):
        assert key in str(e.value)


def test_switches_must_name_a_card_input(minimal_task: Path) -> None:
    doc = yaml.safe_load(minimal_task.read_text())
    inst = doc["instruments"][0]
    inst.pop("drives")
    inst["switches"] = "card.does_not_exist"
    minimal_task.write_text(yaml.safe_dump(doc))
    with pytest.raises(TaskFormatError) as e:
        load_task(minimal_task)
    assert "switches" in str(e.value) and "not a card input" in str(e.value)

    inst["switches"] = "card.vin"
    minimal_task.write_text(yaml.safe_dump(doc))
    assert load_task(minimal_task).task.instruments[0].switches == "card.vin"


# --------------------------------------------------------------------------- #
# DoD 4: `drive` on relay0 is refused naming `call`; `call ... set_relay`
# still works as before
# --------------------------------------------------------------------------- #

def test_drive_on_relay0_is_refused_and_names_call(tmp_path: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)["run_id"]

    with pytest.raises(CheckCouldNotRun) as e:
        drive_input(run_id, "relay0", 12.0, state_dir=tmp_path)
    assert "call" in e.value.fix

    code, out = _cli_json(["drive", run_id, "relay0", "12.0", "--state-dir", str(tmp_path)],
                          capsys)
    assert code != 0
    assert out["ok"] is False
    assert "shal-arena call" in out["error"]["fix"]
    assert RunStore(tmp_path).load(run_id).card_applied == {}  # nothing applied


def test_call_set_relay_off_still_works(tmp_path: Path,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    run_id = start_run(str(RELAY_RAIL_TASK), seed=_seed_for("ok"),
                       state_dir=tmp_path)["run_id"]
    code, out = _cli_json(["call", run_id, "relay0", str(PASSING_RELAY_DRIVER), "set_relay",
                           "0", "false", "--state-dir", str(tmp_path)], capsys)
    assert code == 0
    assert out["ok"] is True and out["side_effect"] == "write"
    assert RunStore(tmp_path).load(run_id).card_power_on is False

    read = call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "read_relay", ["0"],
                   state_dir=tmp_path)
    assert read["result"] is False


def test_relay_off_rail_reads_zero_and_temp_reads_ambient(tmp_path: Path) -> None:
    """CTO OK comment on #473 (#426 item 5): the power path must not have
    read `drives` to decide what is powered. With relay0 switched off, the
    rail reads 0 V and temp0 reads ambient -- the same as before the rename.
    The `overheat` seed is used so the temperature would read hot if the
    relay's off state were not applied."""
    run_id = start_run(str(RELAY_RAIL_TASK), seed=_seed_for("overheat"),
                       state_dir=tmp_path)["run_id"]
    call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "false"],
           state_dir=tmp_path)

    rail = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    assert rail["reading"] == pytest.approx(0.0, abs=1e-9)
    temp = take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, state_dir=tmp_path)
    assert temp["reading"] == pytest.approx(25.0, abs=1.0)

"""issue #508: the demo starts from any of 5 stages, from the CLI.

`shal-arena demo --from STAGE --json` (CTO ruling on #508). Each stage is one
preset file, `shal_arena/presets/<stage>.yaml`: the setup YAML the rack page
writes (`build_setup_yaml`) plus a top-level `stage:` key. One test per
Done-when line of the issue."""
from __future__ import annotations

import contextlib
import io
import json
from importlib import resources as importlib_resources
from pathlib import Path

import pytest
import shal
import yaml

from shal_arena import cli
from shal_arena.cases import CASES
from shal_arena.replay.rack import build_setup_yaml

try:
    from shal_arena import stages
except ImportError:  # before the implementation lands: each test fails on its own
    stages = None

STAGE_NAMES = ["empty", "picked", "racked", "wired", "ran"]


def _raw_preset(stage: str) -> dict:
    ref = importlib_resources.files("shal_arena") / "presets" / f"{stage}.yaml"
    return yaml.safe_load(ref.read_text(encoding="utf-8"))


def _demo(argv: list[str]) -> tuple[int, dict, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(["demo", *argv])
    return rc, json.loads(out.getvalue()), err.getvalue()


def _contains(big, small) -> bool:
    """True when every key and value in ``small`` is also in ``big``."""
    if isinstance(small, dict):
        return isinstance(big, dict) and all(
            k in big and _contains(big[k], v) for k, v in small.items())
    return big == small


# DoD 1: one file per stage; each is the rack page's setup YAML plus `stage:`.


def test_one_preset_file_per_stage_in_order():
    assert list(stages.STAGES) == STAGE_NAMES
    for stage in STAGE_NAMES:
        assert _raw_preset(stage)["stage"] == stage


def test_empty_has_only_stage():
    assert _raw_preset("empty") == {"stage": "empty"}


def test_each_preset_is_the_rack_page_setup_yaml_plus_stage():
    by_compatible = {c.compatible: name for name, c in CASES.items()}
    for stage in STAGE_NAMES[1:]:
        doc = _raw_preset(stage)
        meta = {"stage": stage, **({"run": doc["run"]} if stage == "ran" else {})}
        topology = {k: v for k, v in doc.items() if k not in meta}
        cases = [by_compatible[child["driver"]]
                 for bench in topology["root"].values()
                 for child in bench["children"].values()]
        assert topology == yaml.safe_load(build_setup_yaml(cases)), stage


def test_only_ran_has_a_fixed_seed():
    run = _raw_preset("ran")["run"]
    assert set(run) == {"seed"} and isinstance(run["seed"], int)
    for stage in STAGE_NAMES[:-1]:
        assert "run" not in _raw_preset(stage), stage


# DoD 2: every preset loads with the existing loader (no new schema).


def test_every_preset_loads():
    for stage in STAGE_NAMES:
        preset = stages.load_preset(stage)
        assert preset.stage == stage
        if stage == "empty":
            assert preset.topology is None
            continue
        with stages.bench(preset) as hal:
            assert isinstance(hal, shal.Hal)
            assert hal.tool_schemas(), stage


def test_the_ran_preset_carries_its_seed():
    assert stages.load_preset("ran").seed == _raw_preset("ran")["run"]["seed"]


# DoD 3: each stage contains everything in the stage before it.


def test_each_stage_contains_everything_in_the_stage_before_it():
    for before, after in zip(STAGE_NAMES, STAGE_NAMES[1:], strict=False):
        prev = {k: v for k, v in _raw_preset(before).items() if k != "stage"}
        assert _contains(_raw_preset(after), prev), f"{after} drops part of {before}"


# DoD 4: `shal-arena demo --from X --json` reports its first step as X.


@pytest.mark.parametrize("stage", STAGE_NAMES)
def test_demo_from_reports_its_first_step_as_the_stage(stage):
    rc, doc, _ = _demo(["--from", stage, "--json"])
    assert doc["steps"][0]["step"] == stage
    assert doc["from"] == stage
    assert doc["side_effect"] == "none"
    assert rc == 0, doc


# DoD 5: an unknown stage gives an error that lists the 5 valid names.


def test_unknown_stage_lists_the_five_valid_names_and_the_fix():
    rc, doc, err = _demo(["--from", "assembled", "--json"])
    assert rc != 0
    assert doc["ok"] is False
    error = doc["error"]
    for name in STAGE_NAMES:
        assert name in error["message"]
    assert "assembled" in error["message"]
    assert "shal-arena demo --from" in error["fix"]
    assert "assembled" in err


def test_unknown_stage_in_load_preset_raises_an_arena_error():
    from shal_arena.errors import ArenaError

    with pytest.raises(ArenaError) as e:
        stages.load_preset("assembled")
    assert all(name in e.value.message for name in STAGE_NAMES)


# DoD 6: CI runs every stage to the end.


@pytest.mark.parametrize("stage", STAGE_NAMES)
def test_every_stage_runs_to_the_end(stage, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, doc, _ = _demo(["--from", stage, "--json"])
    assert rc == 0 and doc["ok"] is True, doc
    ids = [s["step"] for s in doc["steps"]]
    assert ids == STAGE_NAMES[STAGE_NAMES.index(stage):]
    for s in doc["steps"]:
        assert s["ok"] is True, s
        assert "crashed" not in s["result"], s
        assert s["sentence"].strip(), s
    ran = doc["steps"][-1]["result"]
    assert ran["seed"] == _raw_preset("ran")["run"]["seed"]
    assert ran["gate"]["sent"] is False  # the gated op stopped, nothing sent
    assert list(Path(tmp_path).iterdir()) == []  # side_effect none: wrote nothing


def test_ran_replays_the_same_every_time():
    _, first, _ = _demo(["--from", "ran", "--json"])
    _, second, _ = _demo(["--from", "ran", "--json"])
    assert first["steps"][0]["result"] == second["steps"][0]["result"]


def test_demo_without_from_is_unchanged():
    """`--from` is opt-in: the plain `demo --json` story keeps its own steps."""
    from shal_arena import demo

    rc, doc, _ = _demo(["--json"])
    assert [s["step"] for s in doc["steps"]] == [s[0] for s in demo._STEPS]
    assert rc == 0

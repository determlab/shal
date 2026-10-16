"""issue #500: the arena CLI refuses abbreviated long flags (`allow_abbrev=False`
on the main parser and every subparser), so `--no` can never silently mean
`--note`. Also pins how the `ui` page's commands window (`_cli_log`) finds a
logged call's subcommand: argv's first element that is not a flag."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from shal_arena.cli import _build_parser
from shal_arena.store import RunStore
from shal_arena.ui.data import _cli_log


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)


def test_main_parser_refuses_an_abbreviated_flag() -> None:
    # `--he` used to resolve to `--help` and exit 0.
    proc = _run_cli("--he")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert _run_cli("--help").returncode == 0


def test_a_subcommand_refuses_no_as_an_abbreviation_of_note(tmp_path: Path) -> None:
    proc = _run_cli("answer", "r1", "ok", "--state-dir", str(tmp_path), "--no", "x")
    assert proc.returncode == 2
    assert "unrecognized arguments: --no" in proc.stderr
    # refused at parse time: nothing ran, nothing was written
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


@pytest.mark.parametrize("flag", ["--no", "--not", "--no=x"])
def test_every_prefix_of_note_is_refused(flag: str) -> None:
    args = ["measure", "r1", "dmm0", "m.yaml", flag] + ([] if "=" in flag else ["x"])
    with pytest.raises(SystemExit) as exc:
        _build_parser().parse_args(args)
    assert exc.value.code == 2


def test_note_itself_still_works() -> None:
    args = _build_parser().parse_args(["answer", "r1", "ok", "--note", "rail looks ok"])
    assert args.note == "rail looks ok"
    args = _build_parser().parse_args(["measure", "r1", "dmm0", "m.yaml", "--note=checked"])
    assert args.note == "checked"


def test_cli_log_takes_the_first_non_flag_element_as_the_subcommand(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    path = store.cli_log_path("r1")
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"argv": ["--json", "measure", "r1", "dmm0"], "exit_code": 0,
             "json": {"ok": True}, "text": None, "note": None}
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    rows = _cli_log(store, "r1")["rows"]
    assert len(rows) == 1
    # a flag first does not hide the subcommand: `measure` that exited 0
    assert rows[0]["has_reading"] is True


@pytest.mark.parametrize("flag", ["--no", "--not", "--no=x"])
def test_a_prefix_of_note_is_refused_with_a_note_suggestion(flag: str) -> None:
    """issue #498 (5): the refusal names the flag the player meant."""
    args = ["answer", "r1", "ok", flag] + ([] if "=" in flag else ["x"])
    proc = _run_cli(*args)
    assert proc.returncode == 2
    assert "did you mean --note?" in proc.stderr
    assert f"{flag.split('=')[0]} is not accepted as an abbreviation" in proc.stderr


def test_an_unrelated_unknown_flag_gets_no_note_suggestion() -> None:
    proc = _run_cli("answer", "r1", "ok", "--bogus")
    assert proc.returncode == 2
    assert "unrecognized arguments: --bogus" in proc.stderr
    assert "--note" not in proc.stderr

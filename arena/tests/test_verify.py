"""``shal-arena verify`` (issue #390, leaderboard part 1): replay a closed
run from the weekly seed and say whether its score file holds up.

``arena/tests/fixtures/verify/{score.json,arena-record.json,simlog.jsonl}``
is a REAL run under game 0.4.1: ``shal-arena run
arena/src/shal_arena/tasks/easy.yaml --seed 20261006``, ``drive psu0 5.0``,
``measure dmm0`` (the realized fault is ``open``: the DMM reads about 0 V),
then ``answer open`` (correct). No hand-written fixture replaces it. One edit
only (CTO review on #393, privacy): the runner wrote ``card_path`` as an
absolute local path; it is rewritten repo-relative and ``record_sha256``
recomputed over the edited bytes. ``arena/challenges/2026-41.yaml`` names
the same task and seed, so the packaged sample verifies against the real,
current-week challenge file.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from shal_arena.verify import verify

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "verify"
WEEK = "2026-41"
RECORD = "arena-record.json"


def _copy_fixtures(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("score.json", RECORD, "simlog.jsonl"):
        shutil.copy(FIXTURES / name, dest / name)


def test_real_sample_verifies(tmp_path: Path) -> None:
    _copy_fixtures(tmp_path)
    result = verify(tmp_path / "score.json", week=WEEK)
    assert result == {"ok": True, "result": "verified", "reason": "ok",
                      "week": WEEK, "side_effect": "none"}


def test_real_sample_verifies_with_default_week_from_todays_date(monkeypatch) -> None:
    # Agent path (issue #390): the plain invocation, no --week, must verify
    # the packaged sample -- it checks against the CURRENT ISO week, and the
    # fixture was made against this week's own challenge file. "Today" is
    # pinned to that week so the test does not start failing next week.
    import shal_arena.verify as verify_mod

    monkeypatch.setattr(verify_mod, "_current_week", lambda: WEEK)
    result = verify(FIXTURES / "score.json")
    assert result["result"] == "verified"
    assert result["week"] == WEEK


def test_wrong_seed_is_refused(tmp_path: Path) -> None:
    _copy_fixtures(tmp_path)
    score = json.loads((tmp_path / "score.json").read_text())
    score["seed"] = 1
    (tmp_path / "score.json").write_text(json.dumps(score))

    result = verify(tmp_path / "score.json", week=WEEK)
    assert result["ok"] is False
    assert result["result"] == "refused"
    assert result["reason"].startswith("wrong_seed")
    # Agent path: the error names the weekly seed file to use.
    assert f"arena/challenges/{WEEK}.yaml" in result["reason"].replace("\\", "/")


def test_no_measurements_is_disqualified(tmp_path: Path) -> None:
    _copy_fixtures(tmp_path)
    (tmp_path / "simlog.jsonl").write_text("")

    result = verify(tmp_path / "score.json", week=WEEK)
    assert result["ok"] is False
    assert result["result"] == "disqualified"
    assert result["reason"].startswith("no_measurements")


def test_edited_answer_without_recomputing_the_hash_is_disqualified(tmp_path: Path) -> None:
    _copy_fixtures(tmp_path)
    record = json.loads((tmp_path / RECORD).read_text())
    record["given"] = "ok"
    record["correct"] = False
    (tmp_path / RECORD).write_text(json.dumps(record))  # old record_sha256 now stale

    result = verify(tmp_path / "score.json", week=WEEK)
    assert result["ok"] is False
    assert result["result"] == "disqualified"
    assert result["reason"].startswith("record_sha256_mismatch")


def test_a_self_consistent_forged_record_is_still_caught(tmp_path: Path) -> None:
    # The real attack this must survive: edit BOTH given/fault_id AND
    # recompute record_sha256 over the edited bytes, so the hash alone
    # would not catch it. The fault_id this function trusts is recomputed
    # independently from the seed, never read from the record -- so a
    # fault_id that disagrees with that computation is still caught.
    _copy_fixtures(tmp_path)
    record = json.loads((tmp_path / RECORD).read_text())
    record["fault_id"] = "low_voltage"
    record["given"] = "low_voltage"
    record["correct"] = True
    record_path = tmp_path / RECORD
    record_path.write_text(json.dumps(record, indent=2))
    score = json.loads((tmp_path / "score.json").read_text())
    score["record_sha256"] = hashlib.sha256(record_path.read_bytes()).hexdigest()
    score["fault_type"] = "low_voltage"
    (tmp_path / "score.json").write_text(json.dumps(score))

    result = verify(tmp_path / "score.json", week=WEEK)
    assert result["ok"] is False
    assert result["result"] == "disqualified"
    assert result["reason"].startswith("fault_mismatch")


def test_missing_record_is_refused(tmp_path: Path) -> None:
    shutil.copy(FIXTURES / "score.json", tmp_path / "score.json")
    result = verify(tmp_path / "score.json", week=WEEK)
    assert result["ok"] is False
    assert result["result"] == "refused"
    assert result["reason"].startswith("missing_record")


def test_unknown_week_is_refused(tmp_path: Path) -> None:
    _copy_fixtures(tmp_path)
    result = verify(tmp_path / "score.json", week="1999-01")
    assert result["ok"] is False
    assert result["result"] == "refused"
    assert "no challenge file" in result["reason"]


def test_verified_exits_0_and_others_exit_nonzero():
    # The CLI maps result -> exit code; this test pins the Done-when
    # contract ("Exit 0 only on verified. ... Each exits non-zero.") at the
    # level the CLI itself reads from.
    from shal_arena.cli import _VERIFY_EXIT

    assert _VERIFY_EXIT["verified"] == 0
    assert _VERIFY_EXIT["disqualified"] != 0
    assert _VERIFY_EXIT["refused"] != 0


@pytest.mark.parametrize("name", ["score.json", RECORD, "simlog.jsonl"])
def test_sample_fixtures_exist(name: str) -> None:
    assert (FIXTURES / name).is_file()


def test_current_week_challenge_file_exists() -> None:
    challenges_dir = Path(__file__).resolve().parent.parent / "challenges"
    assert (challenges_dir / f"{WEEK}.yaml").is_file()


@pytest.mark.parametrize("bad_line", ["{not json", "[1, 2]", "\"measure\""])
def test_a_malformed_simlog_line_is_refused_not_raised(tmp_path: Path, bad_line: str) -> None:
    # CTO review on #393: one bad sim-log line raised JSONDecodeError, which
    # broke "never raises for a bad submission".
    _copy_fixtures(tmp_path)
    good = (tmp_path / "simlog.jsonl").read_text(encoding="utf-8")
    (tmp_path / "simlog.jsonl").write_text(good + bad_line + "\n", encoding="utf-8")

    result = verify(tmp_path / "score.json", week=WEEK)
    assert result["ok"] is False
    assert result["result"] == "refused"
    assert result["reason"].startswith("simlog_invalid_json")
    assert "exactly as shal-arena wrote it" in result["reason"]


def test_a_non_utf8_simlog_is_refused_not_raised(tmp_path: Path) -> None:
    _copy_fixtures(tmp_path)
    (tmp_path / "simlog.jsonl").write_bytes(b"\xff\xfe\n")
    result = verify(tmp_path / "score.json", week=WEEK)
    assert result["result"] == "refused"
    assert result["reason"].startswith("simlog_invalid_json")


def test_a_score_from_another_game_version_is_refused(tmp_path: Path) -> None:
    _copy_fixtures(tmp_path)
    score = json.loads((tmp_path / "score.json").read_text())
    score["game_version"] = "0.4.0"
    (tmp_path / "score.json").write_text(json.dumps(score))

    result = verify(tmp_path / "score.json", week=WEEK)
    assert result["result"] == "refused"
    assert result["reason"].startswith("wrong_game_version")


def test_a_pre_435_record_json_name_still_verifies(tmp_path: Path) -> None:
    # issue #435 CTO review: verify must still read old captures, whose
    # record is the bare record.json name.
    _copy_fixtures(tmp_path)
    (tmp_path / RECORD).rename(tmp_path / "record.json")
    assert verify(tmp_path / "score.json", week=WEEK)["result"] == "verified"


def test_sample_record_names_no_local_path() -> None:
    # CTO review on #393 (privacy): the public sample must carry no local
    # username or absolute path -- only repo-relative ones.
    record = json.loads((FIXTURES / RECORD).read_text(encoding="utf-8"))
    for field in ("task_path", "card_path"):
        value = record[field]
        assert not Path(value).is_absolute() and ":" not in value and "\\" not in value, value
        assert value.startswith("arena/src/shal_arena/"), value

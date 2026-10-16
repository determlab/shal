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
import yaml

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


GOLDEN_040 = Path(__file__).resolve().parent / "fixtures" / "runs" / "main-0.4.0-open"
GOLDEN_STEM = "run-20261007T080236Z-f98b4df0"


def _golden_040_under_challenge(tmp_path: Path, monkeypatch, game_versions) -> Path:
    """#480's golden 0.4.0 run (easy, seed 0, ``open`` answered right), checked
    against a seed-0 challenge that allows ``game_versions``."""
    import shal_arena.verify as verify_mod

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for suffix in ("score.json", "arena-record.json", "simlog.jsonl"):
        shutil.copy(GOLDEN_040 / f"{GOLDEN_STEM}.{suffix}", run_dir / f"{GOLDEN_STEM}.{suffix}")
    challenges = tmp_path / "challenges"
    challenges.mkdir()
    (challenges / "2026-99.yaml").write_text(
        f"week: '2026-99'\ntask_id: easy\nseed: 0\ngame_versions: {json.dumps(game_versions)}\n",
        encoding="utf-8")
    monkeypatch.setattr(verify_mod, "challenges_dir", lambda: challenges)
    return run_dir / f"{GOLDEN_STEM}.score.json"


def test_a_0_4_0_run_verifies_under_its_own_rules(tmp_path: Path, monkeypatch) -> None:
    score_path = _golden_040_under_challenge(tmp_path, monkeypatch, ["0.4.0", "0.4.1"])
    assert json.loads(score_path.read_text())["error_fail_correct"] == 1
    result = verify(score_path, week="2026-99")
    assert result["result"] == "verified", result


def test_a_0_4_0_run_relabelled_0_4_1_is_disqualified(tmp_path: Path, monkeypatch) -> None:
    # 0.4.1 does not count `open` in error_fail_correct, so the old score's 1
    # no longer recomputes.
    score_path = _golden_040_under_challenge(tmp_path, monkeypatch, ["0.4.0", "0.4.1"])
    score = json.loads(score_path.read_text())
    score["game_version"] = "0.4.1"
    score_path.write_text(json.dumps(score))
    result = verify(score_path, week="2026-99")
    assert result["result"] == "disqualified"
    assert result["reason"].startswith("score_mismatch: error_fail_correct")


def test_a_version_missing_from_the_weeks_list_is_refused(tmp_path: Path, monkeypatch) -> None:
    score_path = _golden_040_under_challenge(tmp_path, monkeypatch, ["0.4.1"])
    result = verify(score_path, week="2026-99")
    assert result["result"] == "refused"
    assert result["reason"].startswith("wrong_game_version")
    assert "2026-99.yaml" in result["reason"]


def test_an_unknown_game_version_is_refused(tmp_path: Path) -> None:
    _copy_fixtures(tmp_path)
    score = json.loads((tmp_path / "score.json").read_text())
    score["game_version"] = "9.9.9"
    (tmp_path / "score.json").write_text(json.dumps(score))

    result = verify(tmp_path / "score.json", week=WEEK)
    assert result["result"] == "refused"
    assert result["reason"].startswith("unknown_game_version")
    assert "0.4.0" in result["reason"] and "0.4.1" in result["reason"]


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


GOLDEN_041_RELAY = (Path(__file__).resolve().parent / "fixtures" / "runs"
                    / "main-0.4.1-relay-rail-open")
GOLDEN_041_RELAY_STEM = "run-20261007T092620Z-2b92c03f"


def _relay_rail_challenge(tmp_path: Path, monkeypatch, seed: int, game_versions) -> None:
    import shal_arena.verify as verify_mod

    challenges = tmp_path / "challenges"
    challenges.mkdir()
    (challenges / "2026-99.yaml").write_text(
        f"week: '2026-99'\ntask_id: relay-rail\nseed: {seed}\n"
        f"game_versions: {json.dumps(game_versions)}\n", encoding="utf-8")
    monkeypatch.setattr(verify_mod, "challenges_dir", lambda: challenges)


def test_a_0_4_1_relay_rail_run_replays_from_the_old_fault_list(tmp_path: Path,
                                                                  monkeypatch) -> None:
    # issue #478: seed 5 realizes `open` from relay-rail's 0.4.1 list but
    # `broken_probe` from its 0.4.2 one; verify must draw from the 0.4.1 list.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for suffix in ("score.json", "arena-record.json", "simlog.jsonl"):
        shutil.copy(GOLDEN_041_RELAY / f"{GOLDEN_041_RELAY_STEM}.{suffix}",
                    run_dir / f"{GOLDEN_041_RELAY_STEM}.{suffix}")
    _relay_rail_challenge(tmp_path, monkeypatch, 5, ["0.4.1", "0.4.2"])
    result = verify(run_dir / f"{GOLDEN_041_RELAY_STEM}.score.json", week="2026-99")
    assert result["result"] == "verified", result


def test_a_0_4_2_broken_probe_run_verifies(tmp_path: Path, monkeypatch) -> None:
    # a live 0.4.2 run: relay-rail seed 5 is `broken_probe`, answered `probe`
    from shal_arena.runner import answer, drive_input, start_run, take_measurement
    from shal_arena.score import GAME_VERSION

    from .conftest import PASSING_DMM_DRIVER, RELAY_RAIL_TASK

    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=5, state_dir=state_dir)["run_id"]
    drive_input(run_id, "psu0", 12.0, state_dir=state_dir)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    out = answer(run_id, "probe", state_dir=state_dir)
    assert out["score"]["fault_type"] == "broken_probe"
    assert out["score"]["error_fail_correct"] == 1
    _relay_rail_challenge(tmp_path, monkeypatch, 5, [GAME_VERSION])

    score_path = next(state_dir.rglob(f"{run_id}.score.json"))
    result = verify(score_path, week="2026-99")
    assert result["result"] == "verified", result


def test_every_game_version_has_scoring_rules() -> None:
    from shal_arena.score import ERROR_CLASS_FAULTS_BY_VERSION, GAME_VERSION

    assert GAME_VERSION in ERROR_CLASS_FAULTS_BY_VERSION


# issue #499: the published 2026-42 challenge and a REAL 0.4.2 run against it
# (``shal-arena run easy --seed 20261013``, ``drive psu0 5.0``, ``measure dmm0``
# -- ``open``, about 0 V -- then ``answer open``). Same one edit as the 2026-41
# sample: ``task_path`` / ``card_path`` made repo-relative and
# ``record_sha256`` recomputed. It sits under a ``2026-42/`` folder, a real
# submission's own layout, so verify picks the week from the path.
WEEK_42 = "2026-42"
FIXTURES_42 = FIXTURES / WEEK_42


def test_published_2026_42_challenge_verifies_a_0_4_2_run_without_week(monkeypatch) -> None:
    import shal_arena.verify as verify_mod

    repo_challenges = Path(__file__).resolve().parent.parent / "challenges"
    monkeypatch.setattr(verify_mod, "challenges_dir", lambda: repo_challenges)
    challenge = verify_mod._load_challenge(WEEK_42)
    assert challenge is not None
    assert challenge["game_versions"] == ["0.4.2"]
    assert json.loads((FIXTURES_42 / "score.json").read_text())["game_version"] == "0.4.2"

    result = verify(FIXTURES_42 / "score.json")  # no --week
    assert result == {"ok": True, "result": "verified", "reason": "ok",
                      "week": WEEK_42, "side_effect": "none"}


def test_published_2026_42_challenge_refuses_a_0_4_1_run(monkeypatch) -> None:
    import shal_arena.verify as verify_mod

    repo_challenges = Path(__file__).resolve().parent.parent / "challenges"
    monkeypatch.setattr(verify_mod, "challenges_dir", lambda: repo_challenges)
    result = verify(FIXTURES / "score.json", week=WEEK_42)
    assert result["result"] == "refused"
    assert result["reason"].startswith("wrong_game_version")


@pytest.mark.parametrize(("text", "field"), [
    ("week: '2026-99'\ntask_id: easy\nseed: not-a-number\ngame_versions: ['0.4.2']\n",
     "seed"),
    ("week: '2026-99'\ntask_id: easy\nseed: 20261013\ngame_versions: '0.4.2'\n",
     "game_versions"),
    ("week: '2026-99'\nseed: 20261013\ngame_versions: ['0.4.2']\n", "task_id"),
    ("week: '2026-98'\ntask_id: easy\nseed: 20261013\ngame_versions: ['0.4.2']\n",
     "week"),
])
def test_a_malformed_challenge_file_is_refused_naming_the_field(
        tmp_path: Path, monkeypatch, text: str, field: str) -> None:
    import shal_arena.verify as verify_mod

    _copy_fixtures(tmp_path)
    challenges = tmp_path / "challenges"
    challenges.mkdir()
    (challenges / "2026-99.yaml").write_text(text, encoding="utf-8")
    monkeypatch.setattr(verify_mod, "challenges_dir", lambda: challenges)

    result = verify(tmp_path / "score.json", week="2026-99")
    assert result["ok"] is False
    assert result["result"] == "refused"
    assert result["reason"].startswith("bad_challenge_file")
    assert f"'{field}'" in result["reason"]
    assert "arena/challenges/2026-99.yaml" in result["reason"]


def test_a_challenge_file_that_is_not_yaml_is_refused(tmp_path: Path, monkeypatch) -> None:
    import shal_arena.verify as verify_mod

    _copy_fixtures(tmp_path)
    challenges = tmp_path / "challenges"
    challenges.mkdir()
    (challenges / "2026-99.yaml").write_text("week: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(verify_mod, "challenges_dir", lambda: challenges)

    result = verify(tmp_path / "score.json", week="2026-99")
    assert result["result"] == "refused"
    assert result["reason"].startswith("bad_challenge_file")


def test_every_published_challenge_file_has_the_weekly_seed_shape() -> None:
    import shal_arena.verify as verify_mod

    for path in sorted((Path(__file__).resolve().parent.parent / "challenges").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        verify_mod._check_challenge(doc, path.stem)

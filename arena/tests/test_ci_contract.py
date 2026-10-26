"""Issue #317: the arena's own CI -- three OS, one seed, the cold agent, two agents.

The workflow is `.github/workflows/arena-ci.yml` (GitHub reads workflows only
from that folder); everything it runs lives in `arena/ci/`. One test group per
line of the ticket's "Done when":

1. the workflow has the three-OS matrix and the `arena/` path filter;
2. the workflow can only go green when all three OS are green (the run itself
   is on GitHub -- no test here can prove a run happened);
3. the score file for one seed is byte-equal across runs and holds nothing
   that names the machine, and the compare step fails on any difference;
4. the cold-agent job prints the measured time and fails over the limit;
5. two agents (Claude Code and Codex stand-ins) finish a task through the CLI
   with `--json` and through MCP.

And the scope line "CI never runs code from a stranger": a task PR is read as
data and replayed from its seed by this branch's own code.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_CI = _ROOT / "arena" / "ci"
_WORKFLOW = _ROOT / ".github" / "workflows" / "arena-ci.yml"
_ARENA_README = _ROOT / "arena" / "README.md"
_AGENTS = _ROOT / "AGENTS.md"
_PACKAGED = _ROOT / "arena" / "src" / "shal_arena"

if str(_CI) not in sys.path:
    sys.path.insert(0, str(_CI))

THREE_OS = {"ubuntu-latest", "windows-latest", "macos-latest"}
# the jobs whose result is the ticket's claim on each OS
MATRIX_JOBS = ("seed-scores", "cold-agent", "two-agents")


def _workflow() -> dict:
    assert _WORKFLOW.is_file(), f"missing {_WORKFLOW.relative_to(_ROOT)}"
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True (YAML 1.1)
    doc["on"] = doc.pop(True, doc.get("on"))
    return doc


# --- DoD 1: three-OS matrix and the arena/ path filter -------------------------

def test_workflow_has_the_arena_path_filter_on_pull_request_and_push() -> None:
    on = _workflow()["on"]
    for event in ("pull_request", "push"):
        assert "arena/**" in on[event]["paths"], f"{event} has no arena/** path filter"


@pytest.mark.parametrize("job", MATRIX_JOBS)
def test_workflow_job_runs_on_windows_macos_and_linux(job: str) -> None:
    jobs = _workflow()["jobs"]
    assert job in jobs, f"no `{job}` job in arena-ci.yml"
    assert set(jobs[job]["strategy"]["matrix"]["os"]) == THREE_OS
    assert jobs[job]["runs-on"] == "${{ matrix.os }}"


# --- DoD 2: green means green on all three OS ----------------------------------

def test_no_job_lets_one_os_fail_quietly() -> None:
    """e2e-clean-machine.yml lets macOS fail without failing the run; this
    workflow may not, or "green on three OS" would not mean what it says."""
    for name, job in _workflow()["jobs"].items():
        assert "continue-on-error" not in job, f"{name} hides a failing cell"
        strategy = job.get("strategy")
        if strategy:
            assert strategy.get("fail-fast") is False, f"{name}: one OS failing cancels the others"


# --- DoD 3: one seed, byte-equal score files -----------------------------------

def test_score_file_for_the_same_seeds_is_byte_equal_across_two_runs(tmp_path: Path) -> None:
    import seed_scores

    first = seed_scores.score_file(tmp_path / "a")
    second = seed_scores.score_file(tmp_path / "b")
    assert first == second
    text = first.decode("utf-8")
    # nothing in it may name the machine: no state dir, no install path
    assert str(tmp_path) not in text and "site-packages" not in text
    assert "\r" not in text


def test_score_file_keeps_every_score_field_but_the_clock_and_the_run_identity(
        tmp_path: Path) -> None:
    import seed_scores

    from shal_arena.score import SCORE_SCHEMA

    doc = json.loads(seed_scores.score_file(tmp_path))
    assert set(seed_scores.DROPPED_FIELDS) == {"duration_s", "record_sha256"}
    kept = set(SCORE_SCHEMA["required"]) - set(seed_scores.DROPPED_FIELDS)
    assert doc["runs"], "no runs in the score file"
    for run in doc["runs"]:
        assert set(run["score"]) == kept
        assert run["readings"], f"{run['task']} seed {run['seed']}: no reading recorded"
    assert {r["task"] for r in doc["runs"]} >= {"rail-3v3", "relay-rail", "power-28v"}


def test_score_file_never_plays_a_tasks_own_default_seed(tmp_path: Path) -> None:
    """The artifact is public; a task's own seed must stay a puzzle."""
    import seed_scores

    from shal_arena.loader import load_task

    for run in json.loads(seed_scores.score_file(tmp_path))["runs"]:
        own = load_task(str(_PACKAGED / "tasks" / f"{run['task']}.yaml")).task.seed
        assert run["seed"] != own


def test_compare_passes_on_equal_files_and_names_the_first_difference(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import seed_scores

    a, b, c = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"
    a.write_bytes(b'{\n  "x": 1.5\n}\n')
    b.write_bytes(b'{\n  "x": 1.5\n}\n')
    c.write_bytes(b'{\n  "x": 1.5000000000000002\n}\n')
    assert seed_scores.main(["compare", str(a), str(b)]) == 0
    assert seed_scores.main(["compare", str(a), str(b), str(c)]) == 1
    err = capsys.readouterr().out
    assert "c.json" in err and "1.5000000000000002" in err


def test_compare_refuses_fewer_than_three_files_when_asked_for_three_os(
        tmp_path: Path) -> None:
    import seed_scores

    a = tmp_path / "a.json"
    a.write_bytes(b"{}\n")
    assert seed_scores.main(["compare", "--expect", "3", str(a), str(a)]) == 1


# --- DoD 4: cold agent prints the time and fails over the limit ----------------

def test_cold_agent_prints_the_measured_time_and_passes_under_the_limit(
        capsys: pytest.CaptureFixture[str]) -> None:
    import cold_agent

    assert cold_agent.timed(lambda: None, limit_s=60) == 0
    out = capsys.readouterr().out
    assert "cold-agent: first success in" in out and "limit 60" in out


def test_cold_agent_fails_over_the_limit(capsys: pytest.CaptureFixture[str]) -> None:
    import cold_agent

    assert cold_agent.timed(lambda: time.sleep(0.05), limit_s=0.01) == 1
    out = capsys.readouterr().out
    assert "cold-agent: first success in" in out and "over the limit" in out


def test_cold_agent_fails_when_first_success_never_comes(
        capsys: pytest.CaptureFixture[str]) -> None:
    import cold_agent

    def broken() -> None:
        raise cold_agent.StepFailed("shal-arena answer exited 1")

    assert cold_agent.timed(broken, limit_s=60) == 1
    assert "shal-arena answer exited 1" in capsys.readouterr().out


def test_cold_agent_takes_its_install_line_from_agents_md_and_installs_this_commit(
        tmp_path: Path) -> None:
    import cold_agent

    (tmp_path / "pyshal-0.4.0-py3-none-any.whl").write_bytes(b"")
    (tmp_path / "shal_arena-0.4.0-py3-none-any.whl").write_bytes(b"")
    line = cold_agent.install_line(_AGENTS.read_text(encoding="utf-8"))
    assert line.startswith("pip install ")
    args = cold_agent.install_args(line, tmp_path)
    assert [Path(a).name for a in args] == ["pyshal-0.4.0-py3-none-any.whl",
                                            "shal_arena-0.4.0-py3-none-any.whl"]
    with pytest.raises(cold_agent.StepFailed):
        cold_agent.install_args("pip install something-else", tmp_path)


def test_every_command_the_cold_agent_runs_is_printed_in_the_docs() -> None:
    import cold_agent

    docs = _ARENA_README.read_text(encoding="utf-8")
    for sub in cold_agent.SUBCOMMANDS:
        assert f"shal-arena {sub}" in docs, f"arena/README.md never shows `shal-arena {sub}`"


# --- DoD 5: two agents, CLI --json and MCP -------------------------------------

@pytest.mark.parametrize("agent", ["claude-code", "codex"])
def test_agent_completes_a_task_through_the_cli_json(agent: str, tmp_path: Path) -> None:
    import two_agents

    result = two_agents.play(agent, "cli", tmp_path)
    assert result["ok"] is True
    assert result["disqualified"] is False
    assert Path(result["score_path"]).is_file()


@pytest.mark.parametrize("agent", ["claude-code", "codex"])
def test_agent_completes_a_task_through_mcp(agent: str, tmp_path: Path) -> None:
    pytest.importorskip("mcp", reason='MCP check needs pip install "pyshal[mcp]"')
    import two_agents

    result = two_agents.play(agent, "mcp", tmp_path)
    assert result["ok"] is True
    assert result["disqualified"] is False
    assert Path(result["score_path"]).is_file()


def test_each_agent_starts_the_mcp_server_from_its_own_config_format(tmp_path: Path) -> None:
    import two_agents

    claude = two_agents.server_from_config("claude-code",
                                           two_agents.write_config("claude-code", tmp_path))
    codex = two_agents.server_from_config("codex", two_agents.write_config("codex", tmp_path))
    assert claude == codex
    assert claude[0] == sys.executable


def test_cli_and_mcp_give_the_same_score_for_the_same_seed(tmp_path: Path) -> None:
    pytest.importorskip("mcp", reason='MCP check needs pip install "pyshal[mcp]"')
    import two_agents

    cli = two_agents.play("codex", "cli", tmp_path / "cli")
    mcp = two_agents.play("claude-code", "mcp", tmp_path / "mcp")
    drop = {"duration_s", "record_sha256"}
    assert ({k: v for k, v in cli["score"].items() if k not in drop}
            == {k: v for k, v in mcp["score"].items() if k not in drop})


# --- scope: a task PR is data, replayed from its seed --------------------------

_TASK_DIR = "arena/src/shal_arena/tasks/"


def _task_bytes(name: str = "rail-3v3") -> bytes:
    return (_PACKAGED / "tasks" / f"{name}.yaml").read_bytes()


def test_task_replay_replays_a_valid_task_from_its_seed(tmp_path: Path) -> None:
    import task_replay

    new = _task_bytes().replace(b"id: rail-3v3", b"id: rail-3v3-copy")
    results = task_replay.replay({_TASK_DIR + "rail-3v3-copy.yaml": new}, tmp_path)
    assert [r["task_id"] for r in results] == ["rail-3v3-copy"]
    assert results[0]["replayed"] is True and results[0]["turns"] >= 1
    # the log is public: no fault name, no reading
    assert "fault_type" not in results[0] and "readings" not in results[0]


@pytest.mark.parametrize("path", [
    "arena/src/shal_arena/tasks/evil.py",
    "arena/src/shal_arena/runner.py",
    "arena/ci/seed_scores.py",
    "arena/src/shal_arena/tasks/../runner.yaml",
])
def test_task_replay_refuses_anything_that_is_not_task_or_card_yaml(
        path: str, tmp_path: Path) -> None:
    import task_replay

    with pytest.raises(task_replay.Refused):
        task_replay.replay({path: b"arena_task: 1\n"}, tmp_path)


def test_task_replay_refuses_yaml_that_asks_to_build_a_python_object(tmp_path: Path) -> None:
    import task_replay

    evil = b"arena_task: 1\nid: !!python/object/apply:os.system ['echo pwned']\n"
    with pytest.raises(task_replay.Refused):
        task_replay.replay({_TASK_DIR + "evil.yaml": evil}, tmp_path)


def test_task_replay_job_never_checks_out_or_runs_the_pr_head() -> None:
    """`pull_request_target` runs the base branch's workflow and code; the PR's
    files are only read with `git show` (data), never checked out."""
    wf = _workflow()
    assert "pull_request_target" in wf["on"]
    job = wf["jobs"]["task-replay"]
    assert job["permissions"] == {"contents": "read"}
    for step in job["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout"):
            assert "ref" not in (step.get("with") or {}), "the replay job checks out a PR ref"
        # the PR's commit is fetched as data only, never checked out or switched to
        run = str(step.get("run", ""))
        assert "git checkout" not in run and "git switch" not in run

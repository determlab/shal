"""Samples (#206): `shal docs --samples`, `--sample <name>`, `--sample <name> --to DIR`,
and the runner the `samples` CI job uses (dev/samples/run_samples.py).

Samples are for a person; the ADK references are for a cold agent. The two lists
never mix. The real run — a clean venv, the built wheel — is the `samples` CI job.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from shal import cli

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "samples"))

import run_samples  # noqa: E402
from run_samples import BadSample, load_expect, verdict  # noqa: E402

SAMPLES_DIR = Path(str(files("shal") / "samples"))
REFERENCES = {"tmp102", "mcp23017", "rigol_dp832", "sonos", "order_service", "sqlite"}


def _on_disk() -> set[str]:
    return {p.name for p in SAMPLES_DIR.iterdir() if p.is_dir() and (p / "run.py").is_file()}


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# -- listing ----------------------------------------------------------------------

def test_samples_lists_hello_one_line_each_and_no_reference(capsys):
    assert cli.main(["docs", "--samples"]) == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("  ")]
    assert {ln.split()[0] for ln in lines} == _on_disk() and "hello" in _on_disk()
    assert not any(f" {ref} " in out for ref in REFERENCES)
    assert "shal docs --sample <name> --to <DIR>" in out


def test_list_shows_no_sample(capsys):
    assert cli.main(["docs", "--list"]) == 0
    out = capsys.readouterr().out
    assert "  hello " not in out and "sample" not in out.lower()
    assert cli.main(["docs", "--list", "--json"]) == 0
    assert "hello" not in {r["name"] for r in _json(capsys)["references"]}


def test_samples_json_is_the_same_list(capsys):
    assert cli.main(["docs", "--samples", "--json"]) == 0
    doc = _json(capsys)
    assert set(doc) == {"ok", "samples"} and doc["ok"] is True
    assert {s["name"] for s in doc["samples"]} == _on_disk()   # listed from the folder
    assert not {s["name"] for s in doc["samples"]} & REFERENCES
    hello = next(s for s in doc["samples"] if s["name"] == "hello")
    assert set(hello) == {"name", "summary", "folder", "files", "print_with", "write_with"}
    assert hello["files"] == ["run.py", "topology.yaml"]
    assert hello["summary"].startswith("Hello:")
    assert Path(hello["folder"]) == SAMPLES_DIR / "hello"
    assert hello["print_with"] == "shal docs --sample hello"
    assert hello["write_with"] == "shal docs --sample hello --to <DIR>"


def test_a_new_folder_is_a_sample_without_editing_code(tmp_path, monkeypatch, capsys):
    root = tmp_path / "samples"
    for name in ("zeta", "alpha"):
        (root / name).mkdir(parents=True)
        (root / name / "run.py").write_text(f'"""{name} does a thing."""\n', encoding="utf-8")
    (root / "notes").mkdir()                          # no run.py: not a sample
    monkeypatch.setattr("importlib.resources.files", lambda pkg: tmp_path)
    assert list(cli._samples()) == ["alpha", "zeta"]


def test_json_needs_list_or_samples(capsys):
    assert cli.main(["docs", "--sample", "hello", "--json"]) == 2
    assert "--json works only with --list or --samples" in capsys.readouterr().err


# -- printing one -------------------------------------------------------------------

def test_sample_prints_its_files(capsys):
    assert cli.main(["docs", "--sample", "hello"]) == 0
    out = capsys.readouterr().out
    heads = [ln.split()[2] for ln in out.splitlines() if ln.startswith("# ==== ")]
    assert heads == ["run.py", "topology.yaml"]
    assert (SAMPLES_DIR / "hello" / "run.py").read_text(encoding="utf-8").strip() in out
    assert "shal docs --sample hello --to <DIR>" in out


def test_unknown_sample_exits_2(capsys):
    assert cli.main(["docs", "--sample", "nope"]) == 2
    err = capsys.readouterr().err
    assert "no sample named 'nope'" in err and "hello" in err


def test_sample_and_example_are_separate(capsys):
    assert cli.main(["docs", "--sample", "tmp102"]) == 2      # a reference is not a sample
    assert cli.main(["docs", "--example", "hello"]) == 2      # a sample is not a reference


def test_expect_json_is_neither_printed_nor_written(tmp_path, monkeypatch, capsys):
    s = tmp_path / "x"
    s.mkdir()
    (s / "run.py").write_text('"""X."""\nraise SystemExit(2)\n', encoding="utf-8")
    (s / "expect.json").write_text('{"exit": 2}', encoding="utf-8")
    monkeypatch.setattr(cli, "_samples", lambda: {"x": s})
    assert cli.main(["docs", "--sample", "x"]) == 0
    assert "expect.json" not in capsys.readouterr().out
    assert cli.main(["docs", "--sample", "x", "--to", str(tmp_path / "out")]) == 0
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["run.py"]


# -- writing one: --to ----------------------------------------------------------------

def test_to_writes_the_files_and_prints_one_command(tmp_path, capsys):
    dest = tmp_path / "new" / "hello"                  # parents are made
    assert cli.main(["docs", "--sample", "hello", "--to", str(dest)]) == 0
    cap = capsys.readouterr()
    assert sorted(p.name for p in dest.iterdir()) == ["run.py", "topology.yaml"]
    for f in ("run.py", "topology.yaml"):
        assert (dest / f).read_bytes() == (SAMPLES_DIR / "hello" / f).read_bytes()
    assert cap.out.count("\n") == 1                    # the command, alone on stdout
    assert cap.out.strip() in (f"python {dest / 'run.py'}", f'python "{dest / "run.py"}"')
    assert "wrote sample 'hello'" in cap.err


def test_to_an_existing_empty_folder_is_fine(tmp_path, capsys):
    assert cli.main(["docs", "--sample", "hello", "--to", str(tmp_path)]) == 0
    assert (tmp_path / "run.py").is_file()


def test_to_refuses_a_non_empty_folder_and_says_why(tmp_path, capsys):
    (tmp_path / "mine.txt").write_text("keep me", encoding="utf-8")
    assert cli.main(["docs", "--sample", "hello", "--to", str(tmp_path)]) == 1
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "the folder is not empty" in cap.err and "never overwrites" in cap.err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mine.txt"]   # nothing written


def test_to_refuses_a_file(tmp_path, capsys):
    f = tmp_path / "afile"
    f.write_text("", encoding="utf-8")
    assert cli.main(["docs", "--sample", "hello", "--to", str(f)]) == 1
    assert "it is a file" in capsys.readouterr().err


def test_to_needs_sample(tmp_path, capsys):
    assert cli.main(["docs", "--samples", "--to", str(tmp_path)]) == 2
    assert "--to works only with --sample" in capsys.readouterr().err


def test_the_printed_command_runs_from_another_folder(tmp_path, capsys):
    dest = tmp_path / "hello"
    assert cli.main(["docs", "--sample", "hello", "--to", str(dest)]) == 0
    cmd = capsys.readouterr().out.strip()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # `python` in the printed command must be this venv's, as it is on a person's PATH
    env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep
           + os.environ.get("PATH", "")}
    r = subprocess.run(cmd, shell=True, cwd=elsewhere, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("ambient_temp: ") and "celsius" in r.stdout


# -- the CI runner --------------------------------------------------------------------

def test_expect_defaults_to_exit_0(tmp_path):
    assert load_expect(tmp_path) == {"exit": 0}


def test_expect_reads_the_file(tmp_path):
    (tmp_path / "expect.json").write_text(
        '{"exit": 2, "stderr_has": ["refused"], "stderr_lacks": ["Traceback"]}',
        encoding="utf-8")
    assert load_expect(tmp_path) == {"exit": 2, "stderr_has": ["refused"],
                                     "stderr_lacks": ["Traceback"]}


@pytest.mark.parametrize("text, match", [
    ('{"exti": 2}', "unknown key"),
    ('{"exit": "2"}', "must be an integer"),
    ('{"stderr_has": "refused"}', "list of strings"),
    ("[2]", "JSON object"),
    ("{", "not JSON"),
])
def test_a_bad_expect_file_fails(tmp_path, text, match):
    (tmp_path / "expect.json").write_text(text, encoding="utf-8")
    with pytest.raises(BadSample, match=match):
        load_expect(tmp_path)


def test_verdict_passes_exit_0_by_default():
    assert verdict({"exit": 0}, 0, "ok", "") == []


def test_verdict_fails_a_non_zero_exit():
    assert verdict({"exit": 0}, 1, "", "boom") == ["exit 1, expected 0"]


def test_verdict_passes_the_declared_exit_and_output():
    expect = {"exit": 2, "stderr_has": ["limit"], "stderr_lacks": ["Traceback"]}
    assert verdict(expect, 2, "", "refused: over the limit\n") == []


def test_verdict_names_each_miss():
    expect = {"exit": 2, "stdout_has": ["x"], "stdout_lacks": ["y"],
              "stderr_has": ["limit"], "stderr_lacks": ["Traceback"]}
    got = verdict(expect, 0, "y", "Traceback (most recent call last)")
    assert got == ["exit 0, expected 2", "stdout lacks 'x'", "stdout has 'y'",
                   "stderr lacks 'limit'", "stderr has 'Traceback'"]


def test_run_one_gives_the_sample_no_terminal(tmp_path, monkeypatch, capsys):
    # On Windows a DEVNULL stdin is the NUL device, and NUL is a character device:
    # isatty() is True, so ConsoleApprover.has_person() is True and a gated op
    # prompts and hits EOF. The runner must hand the sample an empty PIPE, where no
    # OS sees a terminal (the limits sample, #207, relies on "stdin is not a terminal").
    src = tmp_path / "src" / "tty"
    src.mkdir(parents=True)
    (src / "run.py").write_text(
        '"""Report the terminal."""\nimport sys\nfrom shal.approval import ConsoleApprover\n'
        'print(sys.stdin.isatty(), ConsoleApprover().has_person())\n', encoding="utf-8")
    (src / "expect.json").write_text('{"stdout_has": ["False False"]}', encoding="utf-8")
    real_run = run_samples.subprocess.run

    def fake_run(args, **kw):   # stands in for `shal docs --sample tty --to DEST` only
        if isinstance(args, list) and args[1:3] == ["docs", "--sample"]:
            dest = Path(args[-1])
            dest.mkdir(parents=True)
            (dest / "run.py").write_bytes((src / "run.py").read_bytes())
            return subprocess.CompletedProcess(args, 0, f"python {dest / 'run.py'}\n", "")
        return real_run(args, **kw)

    monkeypatch.setattr(run_samples.subprocess, "run", fake_run)
    env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep
           + os.environ.get("PATH", "")}
    sample = {"name": "tty", "folder": str(src), "files": ["run.py"]}
    assert run_samples.run_one(sample, "shal", tmp_path / "scratch", env) == []


def test_runner_runs_every_installed_sample(tmp_path, capsys):
    # the installed shal next to this Python (the dev venv); CI runs it on the wheel
    assert run_samples.main(["--scratch", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "ok    hello" in out and "all " in out
    assert (tmp_path / "samples" / "hello" / "run.py").is_file()

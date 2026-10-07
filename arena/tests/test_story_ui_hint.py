"""issue #408: `run_story.py` prints `shal-arena ui --run <id>` -- the real
run id, before the first step -- so whoever is watching can open the live
WATCH page while the rest of the story plays. `--json` keeps stdout as one
JSON document (CTO review on #383, `tests/test_story_script.py`); the hint
goes to stderr and into the JSON's own `ui_hint` field instead."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "examples" / "demos" / "story" / "run_story.py"

_HINT_RE = re.compile(r"^shal-arena ui --run (\S+)$")


def _run_story(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--pause", "0", *extra],
                          capture_output=True, text=True, timeout=180)


def test_plain_mode_prints_the_ui_hint_with_a_real_id_before_the_first_step():
    proc = _run_story()
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()

    # line 0 is the module's own fixed first line; the hint is the very
    # next line, before any step's own narration
    hint_line = lines[1]
    match = _HINT_RE.match(hint_line)
    assert match is not None, hint_line
    run_id = match.group(1)
    assert run_id.startswith("run-")

    first_step_line = next(ln for ln in lines if ln.startswith("Setting the bench"))
    assert lines.index(hint_line) < lines.index(first_step_line)

    # a real, already-open run -- shal-arena ui --run <id> would bind to it
    from shal_arena.ui.data import run_payload

    state_dir_line = next(ln for ln in lines if ln.startswith("Run state"))
    state_dir = Path(state_dir_line.split(" is under ", 1)[1]) / "easy"
    payload = run_payload(run_id, state_dir=state_dir)
    assert payload["run_id"] == run_id


def test_plain_mode_includes_the_port_flag_when_given():
    proc = _run_story("--port", "9123")
    assert proc.returncode == 0, proc.stderr
    hint_line = proc.stdout.splitlines()[1]
    assert hint_line.endswith(" --port 9123")


def test_json_mode_stdout_is_still_one_json_document_with_a_ui_hint_field():
    proc = _run_story("--json")
    assert proc.returncode == 0, proc.stderr
    # CTO review on #383's own regression test: parse the WHOLE of stdout,
    # nothing stripped off first -- the ui hint must never be mixed in.
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True
    match = _HINT_RE.match(doc["ui_hint"])
    assert match is not None, doc["ui_hint"]
    assert match.group(1).startswith("run-")
    # also on stderr, for a person tailing the log under --json
    assert doc["ui_hint"] in proc.stderr

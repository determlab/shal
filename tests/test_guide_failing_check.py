"""The failing `shal check` report in AGENT_GUIDE.md is real (shal#154, ADK R9).

The guide shows one command, the JSON it prints for a broken driver, and a one-line
fix. This test runs that command on the broken example in `tests/fixtures/adk_check/`
and fails if the report differs from the guide's; then it applies the guide's fix
line to a copy and requires a clean check. The example is not in the reference set
and not registered: only this test uses it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "fixtures" / "adk_check" / "my_driver.py"
_COMMAND = "shal check my_driver:MyThing --json"
_BROKEN_DEF = "def get_volume(self) -> int:"


def _guide() -> str:
    return (files("shal") / "AGENT_GUIDE.md").read_text(encoding="utf-8")


def _after_command(guide: str) -> str:
    """The guide from the command's own ```bash block onward (the stable marker)."""
    marker = f"```bash\n{_COMMAND}\n```\n"
    text = guide.replace("\r\n", "\n")
    assert text.count(marker) == 1, f"the guide must show `{_COMMAND}` exactly once"
    return text.split(marker, 1)[1]


def _guide_report() -> dict:
    block = re.search(r"```json\n(.*?)\n```", _after_command(_guide()), re.DOTALL)
    assert block, "no ```json report follows the command in the guide"
    return json.loads(block.group(1))


def _guide_fix() -> str:
    block = re.search(r"```python\n(.*?)\n```", _after_command(_guide()), re.DOTALL)
    assert block, "no ```python fix follows the report in the guide"
    lines = block.group(1).splitlines()
    assert len(lines) == 1, f"the fix must be one line, the guide shows {lines}"
    return lines[0].strip()


def _shal_check(cwd: Path) -> subprocess.CompletedProcess:
    """The guide's command, run in a fresh process in the driver's folder."""
    argv = _COMMAND.split()
    assert argv[0] == "shal"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "shal.cli", *argv[1:]], cwd=cwd,
                          env=env, capture_output=True, text=True, encoding="utf-8",
                          timeout=60)


@pytest.fixture
def driver_dir(tmp_path) -> Path:
    shutil.copy(_FIXTURE, tmp_path / "my_driver.py")
    return tmp_path


def test_the_guide_shows_the_report_shal_check_prints(driver_dir):
    r = _shal_check(driver_dir)
    assert r.returncode == 1, r.stderr
    assert json.loads(r.stdout) == _guide_report(), (
        "AGENT_GUIDE.md's failing report no longer matches `shal check`; "
        f"this is what it prints now:\n{r.stdout}")
    assert _guide_report()["problems"], "the guide's report must be a failing one"


def test_the_guides_one_line_fix_makes_the_check_clean(driver_dir):
    path = driver_dir / "my_driver.py"
    src = path.read_text(encoding="utf-8")
    assert src.count(_BROKEN_DEF) == 1
    indent = re.search(rf"^( *){re.escape(_BROKEN_DEF)}", src, re.MULTILINE).group(1)
    path.write_text(src.replace(_BROKEN_DEF, f"{_guide_fix()}\n{indent}{_BROKEN_DEF}"),
                    encoding="utf-8")
    r = _shal_check(driver_dir)
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads(r.stdout)
    assert report["ok"] is True
    assert report["problems"] == [] and report["warnings"] == []


def test_the_broken_example_is_not_registered_or_shipped():
    # a fresh interpreter: what `import shal` alone loads and lists
    code = ("import json, sys, shal; c = shal.catalog(); print(json.dumps("
            "[[e['compatible'] for e in c['drivers']], 'my_driver' in sys.modules]))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout
    ids, imported = json.loads(out)
    assert "community,my-thing" not in ids and not imported
    assert _FIXTURE.parent.name not in {p.name for p in
                                        (files("shal") / "adk" / "reference").iterdir()}

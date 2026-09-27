"""The device-driver lifecycle in SDK.md is real (shal#24).

SDK.md §1 "Lifecycle" says when a device driver is built (`cls()`), bound
(`bind(node)`), when its bus opens (the first op) and when it closes (the Hal
closes). The section carries one runnable example that asserts each step. This
test takes that example from the doc a pip-only agent reads — `shal docs --sdk`,
printed from package data — and runs it in a fresh process, so the doc cannot
drift from the loader.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import shal
from shal import cli

_HEADING = "### Lifecycle: built, bound, opened, closed\n"


def _sdk_printed(capsys) -> str:
    assert cli.main(["docs", "--sdk"]) == 0
    return capsys.readouterr().out.replace("\r\n", "\n")


def _lifecycle_section(sdk: str) -> str:
    assert sdk.count(_HEADING) == 1, "SDK.md must have the Lifecycle section once"
    rest = sdk.split(_HEADING, 1)[1]
    return re.split(r"\n#{2,3} ", rest, maxsplit=1)[0]


def test_the_lifecycle_section_states_the_contract(capsys) -> None:
    section = " ".join(_lifecycle_section(_sdk_printed(capsys)).split())
    assert "`cls()`" in section and "`bind(node)`" in section
    assert "never defines `__init__(self, node)`" in section
    assert "super().bind(node)` **first**" in section
    assert "first op" in section and "`hal.close()`" in section


def test_the_donts_warn_against_init_node_on_a_device_driver(capsys) -> None:
    sdk = _sdk_printed(capsys)
    donts = " ".join(sdk.split("## 11. Don'ts", 1)[1].split())
    assert "Don't write `def __init__(self, node)` on a device driver" in donts


def test_the_lifecycle_example_runs(capsys, tmp_path) -> None:
    section = _lifecycle_section(_sdk_printed(capsys))
    blocks = re.findall(r"```python\n(.*?)\n```", section, re.DOTALL)
    runnable = [b for b in blocks if "shal.load(" in b]
    assert len(runnable) == 1, "the Lifecycle section must carry one runnable example"
    script = tmp_path / "lifecycle.py"
    script.write_text(runnable[0] + "\nprint('lifecycle ok')\n", encoding="utf-8")
    # the shal under test, not whichever one this Python has installed
    src = str(Path(shal.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": os.pathsep.join(p for p in (src, os.environ.get("PYTHONPATH"))
                                         if p)}
    done = subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "lifecycle ok"

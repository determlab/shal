"""Shared rig for the e2e suite: one CLI device driver (registered once here, so
the two test modules don't double-claim the compatible) plus the real "remote"
tool and the topology fixtures.
"""
import sys
import textwrap
from pathlib import Path

import pytest

import shal


@shal.register
class CliThermo(shal.Driver):
    """A device reached by running a CLI tool on the parent CommandTransport —
    the cross-platform analog of i2c-cli. Tool path comes from the node config:."""

    compatible = "e2e,cli-thermo"
    kind = shal.CommandTransport

    def _tool(self) -> str:
        return self.node.spec["config"]["tool"]

    @shal.idempotent
    @shal.op("Read the temperature in Celsius now.", unit="celsius", side_effect="none")
    def read_celsius(self) -> float:
        out = self._run("read")
        return int(out.stdout.split()[0]) * 0.0625

    @shal.op("Set the over-temp threshold.", unit="celsius", side_effect="write")
    def set_threshold(self, value: str) -> str:
        self._run("write", value)   # value stays a string -> proves argv, not shell
        return "ok"

    def _run(self, op: str, *extra: str):
        argv = [sys.executable, self._tool(), op, hex(self.addr), *extra]
        out = self.bus.run(argv)
        if out.exit != 0:
            # a read is safe to retry; a write's delivery is unknown after send
            delivered = "no" if op == "read" else "unknown"
            raise shal.HopError(out.stderr.decode(errors="replace").strip() or "tool failed",
                                path=self.node.path, hop="cli-thermo",
                                delivered=delivered)
        return out


# The "remote CLI tool": prints a raw reading, records its argv, and can be told
# to fail the next N calls via a sibling faults.txt (transient-drop injection).
TOOL = """\
import pathlib, sys
op, addr = sys.argv[1], sys.argv[2]
here = pathlib.Path(__file__).parent
faults = here / "faults.txt"
if faults.exists():
    n = int(faults.read_text() or "0")
    if n > 0:
        faults.write_text(str(n - 1))
        sys.stderr.write("transient link drop\\n")
        sys.exit(1)
(here / "last_argv.txt").write_text("\\n".join(sys.argv[1:]), encoding="utf-8")
print("400" if op == "read" else "ok")     # 400 * 0.0625 == 25.0 C
"""

BOARD = """\
shal_version: 1
template:
  driver: shal,local
  address: localhost
  children:
    thermo:
      id: "${name}"
      driver: e2e,cli-thermo
      address: 0x10
      config: { tool: "${tool}" }
"""


@pytest.fixture
def faults():
    """Tell the tool to fail its next N invocations (transient drops)."""
    def _set(tmp_path: Path, n: int) -> None:
        (tmp_path / "faults.txt").write_text(str(n), encoding="utf-8")
    return _set


@pytest.fixture
def cli_rig(tmp_path):
    """A single-device topology on the real-subprocess CLI stack. -> (setup, dir)."""
    tool = tmp_path / "sensor.py"
    tool.write_text(TOOL, encoding="utf-8")
    setup = tmp_path / "setup.yaml"
    setup.write_text(textwrap.dedent(f"""\
        shal_version: 1
        root:
          host:
            driver: shal,local
            address: localhost
            children:
              thermo:
                id: thermo
                driver: e2e,cli-thermo
                address: 0x10
                config: {{ tool: "{tool.as_posix()}" }}
    """), encoding="utf-8")
    return setup, tmp_path


@pytest.fixture
def two_board_rig(tmp_path):
    """The same board template `use:`d twice with different params. -> (setup, dir)."""
    tool = tmp_path / "sensor.py"
    tool.write_text(TOOL, encoding="utf-8")
    (tmp_path / "board.yaml").write_text(BOARD, encoding="utf-8")
    setup = tmp_path / "setup.yaml"
    setup.write_text(textwrap.dedent(f"""\
        shal_version: 1
        root:
          rack_a:
            use: board.yaml
            with: {{ name: oven_temp, tool: "{tool.as_posix()}" }}
          rack_b:
            use: board.yaml
            with: {{ name: chamber_temp, tool: "{tool.as_posix()}" }}
    """), encoding="utf-8")
    return setup, tmp_path

"""shal CLI front door (issue #54): probe / tools dispatch over a local driver."""
import asyncio
import subprocess
import sys

import pytest

from shal import cli

_DRIVER = """
from shal import Driver, idempotent, op, registry

@registry.register
class CliRig(Driver):
    compatible = "local,cli-rig"
    kind = None
    llm_ready = True

    def bind(self, node):
        super().bind(node)

    @idempotent
    @op("Read the level.", side_effect="none")
    def level(self) -> int:
        return 11

    @op("Move the arm (gated).", side_effect="actuator")
    def move(self, dx: int) -> str:
        return f"moved {dx}"
"""

_YAML = ("shal_version: 1\n"
         "root:\n"
         "  dev: {id: dev, driver: 'local,cli-rig', address: a}\n")


@pytest.fixture
def setup(tmp_path):
    drv = tmp_path / "cli_rig_driver.py"          # unique module stem
    drv.write_text(_DRIVER, encoding="utf-8")
    yml = tmp_path / "t.yaml"
    yml.write_text(_YAML, encoding="utf-8")
    return str(yml), str(drv)


def test_probe_prints_a_real_read(setup, capsys):
    yml, drv = setup
    rc = cli.main(["probe", yml, "--drivers", drv])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dev__level: 11" in out


_SIM_YAML = ("shal_version: 1\nroot:\n  bus:\n    driver: shal,sim-i2c\n"
             "    address: sim0\n    children:\n      temp0:\n"
             "        id: ambient_temp\n        driver: shal,sim-sensor\n"
             "        address: 0x48\n")


def _probe_proc(tmp_path, *tool: str) -> subprocess.CompletedProcess:
    """`shal probe` on the README Quick Start sim topology, in a fresh process."""
    (tmp_path / "sim.yaml").write_text(_SIM_YAML, encoding="utf-8")
    return subprocess.run([sys.executable, "-m", "shal.cli", "probe", "sim.yaml", *tool],
                          cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
                          timeout=60)


# #166: no message may point a `shal` user at a flag `shal` does not have.
def test_probe_footer_names_real_commands(tmp_path):
    r = _probe_proc(tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[-1] == (
        "# writes — not run by `shal probe`; use `shal call` "
        "(gated ops are refused until approved): ambient_temp__set_target")
    assert "--probe" not in r.stdout + r.stderr


def test_probe_unknown_tool_names_shal_probe(tmp_path):
    r = _probe_proc(tmp_path, "nope")
    assert r.returncode == 1 and r.stdout == ""
    assert r.stderr.strip() == ("shal: no tool 'nope'. Run `shal probe <topology>` "
                                "(no tool) to list what this topology exposes.")


def test_probe_write_tool_names_shal_call(tmp_path):
    r = _probe_proc(tmp_path, "ambient_temp__set_target")
    assert r.returncode == 1 and r.stdout == ""
    assert r.stderr.strip() == (
        "shal: `shal probe` runs reads only; 'ambient_temp__set_target' changes "
        "hardware — use `shal call` (gated ops are refused until approved).")


def test_probe_named_read(setup, capsys):
    yml, drv = setup
    rc = cli.main(["probe", yml, "dev__level", "--drivers", drv])
    assert rc == 0
    assert "11" in capsys.readouterr().out.strip()


def test_tools_lists_read_and_gated(setup, capsys):
    yml, drv = setup
    rc = cli.main(["tools", yml, "--drivers", drv])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dev__level" in out and "[read" in out
    assert "dev__move" in out and "[gated" in out


def test_no_subcommand_is_an_error():
    with pytest.raises(SystemExit):
        cli.main([])


def test_docs_prints_the_in_package_guide(capsys):
    rc = cli.main(["docs"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "add a device" in out.lower() and "side_effect" in out


def test_docs_does_not_print_the_doc_standard_front_matter(capsys):
    # the shipped docs carry `type:`/`owner:`/`reviewed:` front-matter for the repo's
    # doc standard; `shal docs` prints the guide, not the bookkeeping (#119)
    rc = cli.main(["docs"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.lstrip().startswith("# ")
    assert "owner: repo-agent" not in out


def test_agent_guide_is_bundled_in_the_package():
    # importable as package data → it ships in the wheel for a pip-only agent (#55)
    from importlib.resources import files
    text = (files("shal") / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    assert "shal probe" in text


def test_guide_adk_states_authoring_order_and_software_labels():
    # #147: the ADK section gives the order (declare, check, implement) and the
    # software side-effect rule (the four labels; not sure -> gated).
    from importlib.resources import files
    text = (files("shal") / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    adk = text.split("## The ADK", 1)[1]
    steps = [adk.index(f"**{w}.**") for w in ("Declare", "Check", "Implement")]
    assert steps == sorted(steps)
    for label in ("none", "write", "config", "actuator"):
        assert f"**`{label}`**" in adk
    assert "Not sure" in adk and "gated" in adk
    assert "contract file" not in text and "stub generator" not in text


def test_guide_cloud_device_example_loads(tmp_path, monkeypatch):
    # #95: the guide's cloud-device example (config: + address, no IP) must
    # actually load — not just read well. Pull the fenced yaml block straight
    # out of the shipped guide so a future edit that breaks it fails here too.
    import re
    from importlib.resources import files

    import shal

    text = (files("shal") / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)
    block = next(b for b in blocks if "vac-serial-123" in b)
    assert "address" in block  # the whole point of the example (#95)

    @shal.register
    class MyThing(shal.Driver):
        compatible = "community,my-thing"
        kind = None

    monkeypatch.setenv("CLOUD_ACCOUNT", "someone@example.com")
    p = tmp_path / "cloud.yaml"
    p.write_text(block, encoding="utf-8")
    with shal.load(p) as hal:
        assert hal.get_device("vac").node.address == "vac-serial-123"


def test_guide_symbols_and_commands_actually_work():
    """Eval regression: the bundled guide must not teach a symbol that isn't
    exported, nor a `shal probe` arg order that argparse rejects."""
    import re
    from importlib.resources import files

    import shal
    text = (files("shal") / "AGENT_GUIDE.md").read_text(encoding="utf-8")

    # every `shal.<Name>` the guide names must really be importable from shal
    for name in set(re.findall(r"\bshal\.([A-Z][A-Za-z0-9_]+)", text)):
        assert hasattr(shal, name), f"guide references shal.{name}, which isn't exported"

    # a named read must put the tool BEFORE --drivers (trailing positional after an
    # optional is the order argparse rejects — see test_probe_named_read).
    for line in text.splitlines():
        cmd = line.split("#", 1)[0]  # drop trailing comments
        m = re.search(r"shal probe \S+\s+--drivers\s+\S+\s+(\S+)", cmd)
        assert m is None, f"guide shows a probe order argparse rejects: {line!r}"


class _FakeSelectorPolicy:
    """Stands in for `asyncio.WindowsSelectorEventLoopPolicy`, which only exists on
    Windows — so these tests assert the same thing on every platform CI runs."""


@pytest.fixture
def policy_spy(monkeypatch):
    """Record what the CLI sets as the event-loop policy, without setting one."""
    seen = []
    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy",
                        _FakeSelectorPolicy, raising=False)
    monkeypatch.setattr(asyncio, "set_event_loop_policy", seen.append)
    return seen


@pytest.mark.parametrize("cmd", ["probe", "tools"])
def test_every_command_uses_a_selector_loop_on_win32(setup, policy_spy, monkeypatch,
                                                     capsys, cmd):
    """#94: `shal mcp` set the selector policy, `shal probe` didn't — so an
    aiomqtt-style driver served over MCP and died with NotImplementedError under
    the CLI. `main` sets it once, so every subcommand is at parity."""
    yml, drv = setup
    monkeypatch.setattr(sys, "platform", "win32")
    assert cli.main([cmd, yml, "--drivers", drv]) == 0
    capsys.readouterr()
    assert len(policy_spy) == 1 and isinstance(policy_spy[0], _FakeSelectorPolicy)


def test_the_loop_policy_is_left_alone_off_win32(setup, policy_spy, monkeypatch, capsys):
    yml, drv = setup
    monkeypatch.setattr(sys, "platform", "linux")
    assert cli.main(["probe", yml, "--drivers", drv]) == 0
    capsys.readouterr()
    assert policy_spy == []


def test_import_alone_never_touches_the_loop_policy(policy_spy, monkeypatch):
    """The library must not swap a host app's event-loop policy just because it was
    imported — same rule as 'the library never configures logging'. Only a command
    (`shal.cli.main` / `shal.mcp.server.main`) may choose."""
    import importlib

    monkeypatch.setattr(sys, "platform", "win32")
    importlib.reload(importlib.import_module("shal.cli"))
    importlib.reload(importlib.import_module("shal.mcp.server"))
    assert policy_spy == []


def test_legacy_shal_mcp_probe_uses_a_selector_loop_on_win32(setup, policy_spy,
                                                             monkeypatch, capsys):
    """`shal-mcp` skips `cli.main`, so that entry point calls the same helper (#94)."""
    from shal.mcp import server
    yml, drv = setup
    monkeypatch.setattr(sys, "platform", "win32")
    assert server.main([yml, "--drivers", drv, "--probe"]) == 0
    capsys.readouterr()
    assert len(policy_spy) == 1 and isinstance(policy_spy[0], _FakeSelectorPolicy)


def test_media_player_capability_is_exported():
    # the guide and capability list name it; subclassing it must not AttributeError
    import shal
    assert hasattr(shal, "MediaPlayer")

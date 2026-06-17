"""shal CLI front door (issue #54): probe / tools dispatch over a local driver."""
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


def test_agent_guide_is_bundled_in_the_package():
    # importable as package data → it ships in the wheel for a pip-only agent (#55)
    from importlib.resources import files
    text = (files("shal") / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    assert "shal probe" in text


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


def test_media_player_capability_is_exported():
    # the guide and capability list name it; subclassing it must not AttributeError
    import shal
    assert hasattr(shal, "MediaPlayer")

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

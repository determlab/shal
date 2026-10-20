"""A sim-dmm `probe:` that names no sim-psu on its bus is a load error (#351)."""
import textwrap
from pathlib import Path

import pytest

import shal


def topo(tmp_path: Path, probe: str) -> Path:
    p = tmp_path / "t.yaml"
    p.write_text(textwrap.dedent(f"""
        shal_version: 1
        root:
          bench:
            driver: shal,sim-scpi
            address: sim0
            children:
              dmm0:
                id: dmm
                driver: shal,sim-dmm
                address: dmm0
                config:
                  probe: {probe}
              psu0:
                id: psu
                driver: shal,sim-psu
                address: psu0
    """), encoding="utf-8")
    return p


def test_valid_probe_loads(tmp_path):
    shal.load(topo(tmp_path, "psu0"))


def test_wrong_probe_is_a_load_error_naming_key_and_valid_addresses(tmp_path):
    with pytest.raises(shal.LoadError) as excinfo:
        shal.load(topo(tmp_path, "0x99"))
    msg = str(excinfo.value)
    assert "probe" in msg
    assert "153" in msg   # YAML reads 0x99 as an int
    assert "psu0" in msg


def test_probe_naming_a_non_psu_is_refused(tmp_path):
    with pytest.raises(shal.LoadError) as excinfo:
        shal.load(topo(tmp_path, "dmm0"))
    assert "probe" in str(excinfo.value) and "psu0" in str(excinfo.value)

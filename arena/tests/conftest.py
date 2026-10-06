"""Makes `pytest arena/tests/test_runner.py -q` work straight from a clone,
with no install step: put `arena/src` on `sys.path` before anything under
`shal_arena` is imported. A no-op once the package is `pip install -e`'d."""
from __future__ import annotations

import sys
from pathlib import Path

_ARENA_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_ARENA_SRC) not in sys.path:
    sys.path.insert(0, str(_ARENA_SRC))

import pytest  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASSING_DRIVER = FIXTURES / "drivers" / "passing_psu_driver.py"
FAILING_DRIVER = FIXTURES / "drivers" / "failing_psu_driver.py"
PASSING_DMM_DRIVER = FIXTURES / "drivers" / "passing_dmm_driver.py"
FAILING_DMM_DRIVER = FIXTURES / "drivers" / "failing_dmm_driver.py"
GATED_RELAY_DRIVER = FIXTURES / "drivers" / "gated_relay_driver.py"
PASSING_RELAY_DRIVER = FIXTURES / "drivers" / "passing_relay_driver.py"
PASSING_TEMP_DRIVER = FIXTURES / "drivers" / "passing_temp_driver.py"
SAMPLE_TASK = _ARENA_SRC / "shal_arena" / "tasks" / "rail-3v3.yaml"
MEDIUM_TASK = _ARENA_SRC / "shal_arena" / "tasks" / "medium.yaml"  # buck-12v-5v, 15.0 V abs max
RELAY_RAIL_TASK = _ARENA_SRC / "shal_arena" / "tasks" / "relay-rail.yaml"
_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
MINIMAL_RELAY_DRIVER = _EXAMPLES / "minimal_relay_driver.py"
MINIMAL_TEMP_DRIVER = _EXAMPLES / "minimal_temp_driver.py"

MINIMAL_CARD = """\
arena_card: 1
id: tiny
description: "test card"
inputs:
  vin: {nominal_v: 5.0}
rails:
  r1: {from: vin, nominal_v: 3.3, tol_pct: 3, test_point: tp1, min_input_v: 4.5}
damage:
  - {input: vin, above_v: 6.0, effect: destroyed, source: "test datasheet"}
faults:
  - {id: ok}
  - {id: low_voltage}
"""

MINIMAL_TASK = """\
arena_task: 1
id: tiny-task
title: "tiny"
level: easy
card: card.yaml
instruments:
  - case: scpi-psu
    address: psu0
    drives: card.vin
question:
  text: "ok or low_voltage?"
  answer: {kind: enum, values: [ok, low_voltage]}
seed: 1
"""


@pytest.fixture
def minimal_task(tmp_path: Path) -> Path:
    """A tiny, valid task.yaml + card.yaml pair under tmp_path. Tests mutate a
    copy of one or the other to exercise a single validation rule at a time."""
    (tmp_path / "card.yaml").write_text(MINIMAL_CARD, encoding="utf-8")
    task_path = tmp_path / "task.yaml"
    task_path.write_text(MINIMAL_TASK, encoding="utf-8")
    return task_path

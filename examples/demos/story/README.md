# The whole story, one script (issue #343)

A person watches the whole story run once: a virtual bench pass, an
unplugged DMM giving an error, a 30 V request blocked by the PSU's own
declared limit, the three SHAL Arena tasks (easy, medium, hard), then the
rail benchmark run ten times with the gate on and with the gate off.

## Install

Neither `pyshal` 0.4.0 nor `shal-arena` is on PyPI yet (PyPI's `pyshal` is
still 0.3.0, and `shal-arena` has never been published) — install both from
source, pinned to the same commit of this repo. This script never touches
`pytest-shal`, so that's the only other dependency:

```bash
pip install "pyshal @ git+https://github.com/determlab/shal@9b3873dd09ecfdef456b731d046b23f692347621"
pip install "shal-arena @ git+https://github.com/determlab/shal@9b3873dd09ecfdef456b731d046b23f692347621#subdirectory=arena"
```

Once both are on PyPI, this becomes `pip install pyshal shal-arena`.

## Run it

```bash
python run_story.py
```

A short pause and one plain line precede each step, then a one-word (or
short) outcome. No claim about speed or score — a step either did what it
was there to show, or it did not.

```
Simulated instruments only. Nothing here touches real hardware.
Setting the bench power supply to 3.3 volts and reading it back on the simulated multimeter.
  -> pass
Unplugging the simulated multimeter and reading it again.
  -> error
...
```

## Agent path

```bash
python run_story.py --pause 0 --json
```

prints one JSON document on stdout, right after that same fixed first line:
`{"ok": <bool>, "steps": [{"step", "line", "result"}, ...]}`, in order.

## What's here

| File | Role |
|---|---|
| `run_story.py` | the one script: no install of its own, reuses `examples/demos/virtual-bench/`'s topology through `shal`'s own Python API (not `run_bench.py`, which needs the unpublished `pytest-shal`) and SHAL Arena's `shal_arena` package |
| `story.gif` | one real run, recorded |

`tests/test_story_script.py` (repo root) runs this script with
`--pause 0 --json` and checks every step's shape.

# The whole story, one script (issue #343)

A person watches the whole story run once: a virtual bench pass, an
unplugged DMM giving an error, a 30 V request blocked by the PSU's own
declared limit, the three SHAL Arena tasks (easy, medium, hard), then the
rail benchmark run ten times with the gate on and with the gate off.

## Install

Two wheels, nothing else — this script never touches `pytest-shal`:

```bash
pip install pyshal shal-arena
```

(Until both are on PyPI, use the release-candidate build instead:
`pip install git+https://github.com/determlab/shal` and the matching
`shal-arena` checkout — see the repo root `AGENTS.md`.)

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

# The whole story, one script (issue #343)

A person watches the whole story run once: a virtual bench pass, an
unplugged DMM giving an error, a 30 V request blocked by the PSU's own
declared limit, the three SHAL Arena tasks (easy, medium, hard — each one
measured for real and answered from that reading, not a blind guess), a
result card for the hard run, then the rail benchmark run ten times with
the gate on and with the gate off.

## Install

**(main only, not in the PyPI release yet)** — neither `pyshal` 0.4.0 nor
`shal-arena` is on PyPI (PyPI's `pyshal` is still 0.3.0, and `shal-arena`
has never been published). Install both from source instead. This script
never touches `pytest-shal`, so that's the only other dependency:

```bash
pip install "pyshal @ git+https://github.com/determlab/shal" \
            "shal-arena @ git+https://github.com/determlab/shal#subdirectory=arena"
```

(`.github/workflows/rc-wheels.yml` builds the same two packages as
`rc-pyshal`/`rc-shal-arena` wheel artifacts from `main` on every push, for a
CI job that wants a pinned wheel instead of installing from source.) Once
both are on PyPI, this becomes `pip install pyshal shal-arena`.

## Run it

```bash
python run_story.py
```

A short pause and one plain line precede each step, then a one-word (or
short) outcome. No claim about speed or score — a step either did what it
was there to show, or it did not; the arena steps report `correct`,
`wrong` or `disqualified`, read from the answer itself, never a bare "it
ran".

```
Simulated instruments only. Nothing here touches real hardware.
Setting the bench power supply to 3.3 volts and reading it back on the simulated multimeter.
  -> pass
Unplugging the simulated multimeter and reading it again.
  -> error
...
Scripted player, not an AI agent. Running the easy arena task: measuring the rail and answering from it.
  -> correct (ok)
...
Writing the result card for the hard run.
  -> wrote /tmp/shal-story-xxxxxxxx/cards/run-....card.html
```

The arena tasks and the gate-on/off benchmark are played by this script's
own fixed logic (`_diagnose`, the bench policy), never by an AI agent — each
of their lines says so up front (CTO review on #383), so nobody watching
mistakes a scripted number for an agent's.

The script writes its run state (including that result card) under a
directory of its own in the OS temp location, printed at the end of a
plain-mode run — it is not deleted when the script exits, so the card is
still there to open afterward.

## Agent path

```bash
python run_story.py --pause 0 --json
```

prints exactly one JSON document on stdout, nothing else (so `ConvertFrom-Json`
or any other strict reader can parse it directly):
`{"ok": <bool>, "note": <the fixed first line>, "state_dir": <path>, "steps":
[{"step", "line", "result"}, ...]}`, steps in order.

## What's here

| File | Role |
|---|---|
| `run_story.py` | the one script: no install of its own, embeds the `examples/demos/virtual-bench/bench.yaml` device tree (that example ships in no wheel yet — issue #384) and reads SHAL Arena's tasks/cards from the installed `shal-arena` package itself (`importlib.resources`, never a checkout path), so running this proves the wheel, not the source tree |
| `story.gif` | one real run, recorded: `python run_story.py --pause 1.2`'s real stdout, captured with its real per-line timing and rendered verbatim into the GIF (durations clamped to stay watchable; no line's text was altered). Scripted player, not an AI agent: the arena and gate-on/off numbers in it come from this script's own fixed logic, not from an agent playing the challenge. |

`tests/test_story_script.py` (repo root) runs this script with
`--pause 0 --json`, checks every step's shape, and separately unit-tests
each pure `check_*` function (a step whose JSON disagrees with what it
claims must flip the script's exit code to 1 — the arena steps' own
`correct`/`disqualified` fields included).

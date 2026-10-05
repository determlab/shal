# Virtual bench (issue #305, T8)

A simulated bench a stranger can run with one command: a PSU sets 3.3 V, a
DMM measures it, [pytest-shal](https://github.com/determlab/pytest-shal)
writes one pass/fail record. No hardware, no network beyond the install
below.

## Install

Neither `pyshal` 0.4.0 nor `pytest-shal` is on PyPI yet (PyPI's `pyshal` is
still 0.3.0, and `pytest-shal` is pinned to a commit of its own repo), so this
needs two pinned-commit installs instead of one:

```bash
pip install pytest==9.1.1
pip install "pyshal @ git+https://github.com/determlab/shal@18482bd65daff0f1d69b30a17c7ed155dd308662"
pip install --no-deps "pytest-shal @ git+https://github.com/determlab/pytest-shal@e240b07"
```

Once pyshal 0.4.0 is released to PyPI, this becomes `pip install pyshal pytest-shal`.

The third line prints a resolver warning —
`pytest-shal 0.0.1 requires pyshal<0.4,>=0.3.0, but you have pyshal 0.4.0 which
is incompatible` — because pytest-shal's own pin predates pyshal's #217 (the
per-Hal approver it actually needs, which shipped in pyshal 0.4.0). `--no-deps`
skips that resolution on purpose; the plugin runs fine against this pyshal.

## Run it

```bash
python run_bench.py
```

```
{"verdict": "pass", "cause": null, "record": "rec-20261004T220034-cbfb3e", "unit": "bench", "sequence": "test_bench.py::test_bench_output_is_3v3"}
```

Exit 0. `rec-...` and the timestamp inside it change every run; `verdict` and
`cause` don't.

## Unplug the DMM

```bash
python run_bench.py --unplug dmm
```

(equivalently: `SHAL_SIM_UNPLUG=dmm python run_bench.py`)

```
{"verdict": "error", "cause": "transport", "record": "rec-20261004T220023-16e352", "unit": "bench", "sequence": "test_bench.py::test_bench_output_is_3v3"}
```

Exit 4 — the same code `shal call` uses for `Unreachable` (shal#300): a link
that never delivered, not a failed measurement. The pytest run underneath
prints "no answer from the instrument at 'dmm0'", not a `check()` failure.

## What's here

| File | Role |
|---|---|
| `bench.yaml` | the rack: `shal,sim-scpi` bus, one `shal,sim-psu` (`psu`), one `shal,sim-dmm` (`dmm`) reading it |
| `test_bench.py` | the pytest-shal test: sets 3.3 V, `check()`s the DMM reading within 2% |
| `run_bench.py` | the one command: runs the test, prints one line of JSON, exits 0/1/3/4 |

## The record, and a gap in reading it

Each run leaves `records.db` and `records/<id>.yaml` beside this README
(`.gitignore`d — they're this run's output, not part of the repo). Read them
the normal way:

```bash
shal records examples/demos/virtual-bench --json
```

That record's own `cause` field reads `null` even for the unplugged run —
this is a real gap in pytest-shal as merged
([determlab/pytest-shal@e240b07](https://github.com/determlab/pytest-shal/blob/e240b07/src/pytest_shal/plugin.py)):
its `_note()` builds the error `Step` directly instead of through
`shal.record.Step.from_error(name, exc)`, so it never carries the `HopError`
that caused it into the stored record (shal#301 landed in pyshal after that
plugin code was written). `run_bench.py` derives `cause` itself for the JSON
it prints — this bench has exactly one way to error — but the stored record
doesn't carry it yet. A one-line fix (`pytest_shal/plugin.py::_note`, the
`call`-phase `else` branch) belongs upstream in pytest-shal, not here.

## Agent path

Read only this file, run `python run_bench.py` from a clean venv (the two
installs above), read the printed JSON's `verdict` and `cause`. On
`verdict: error`, `cause: transport` means the instrument at the address
named in stderr never answered — check the simulated wiring (`bench.yaml`'s
`address:`/`config.probe:`), or drop `--unplug`/`$SHAL_SIM_UNPLUG` if this
run wasn't meant to fault-inject.

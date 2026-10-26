"""Seed score files for the arena CI (issue #317): same seed, same bytes, any OS.

    python arena/ci/seed_scores.py write --out seed-scores.json
    python arena/ci/seed_scores.py compare --expect 3 a.json b.json c.json

``write`` plays every packaged task (``shal_arena/tasks/*.yaml``) on a few
fixed seeds with one scripted player and writes one JSON file: per run, the
calls it made, every reading it got, and the run's score. ``compare`` exits 0
only when every file it is given has the same bytes as the first; otherwise
it prints the first line that differs and exits 1. The CI job writes one file
per OS and compares the three.

The scripted player uses the runner API the CLI calls (`start_run`,
`call_op`, `drive_input`, `take_measurement`, `answer`), unmodified, with the
in-package reference drivers that Play uses. Per task: close every relay that
switches the card's power, drive every input to its nominal voltage, measure
every probe point once, answer ``ok``. The answer does not matter here; the
readings and the score do. A reading is a float made by the seeded sim, so a
platform difference in the sim's arithmetic shows up as a changed digit.

What is left out, and why (``DROPPED_FIELDS``):

- ``duration_s`` is wall-clock time between `run` and `answer`.
- ``record_sha256`` hashes the run record, which holds the run id (time plus
  random hex), the absolute task path (the install folder differs per OS)
  and the closing time. It can never be equal across two runs.

Every other score field is kept. The task's own ``seed:`` is never played:
this file is a public CI artifact, and that seed is the puzzle a player
gets when they run the task plain (arena/README.md, "Agent path").
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: score fields that differ on every run by design (see the docstring)
DROPPED_FIELDS = ("duration_s", "record_sha256")
#: how many seeds per task; the first N positive ints that are not the
#: task's own seed
SEEDS_PER_TASK = 3


def _seeds(own: int) -> list[int]:
    seeds, n = [], 1
    while len(seeds) < SEEDS_PER_TASK:
        if n != own:
            seeds.append(n)
        n += 1
    return seeds


def play(task_path: str | Path, seed: int, state_dir: str | Path) -> dict[str, Any]:
    """Play one task on one seed with the scripted player. Returns the calls
    made (``steps``), the readings, and the full ``answer`` result.

    The caller must hold `cached_driver_imports()` around a batch: the
    runner re-imports a driver file on each call, and the second import of
    the same reference driver is a second class for the same compatible."""
    from shal_arena.errors import MeasurementFailed
    from shal_arena.loader import load_task
    from shal_arena.runner import answer, call_op, drive_input, start_run, take_measurement
    from shal_arena.ui.play import reference_driver_for

    loaded = load_task(str(task_path))
    task, card = loaded.task, loaded.card
    state_dir = str(state_dir)
    run_id = start_run(str(task_path), seed=seed, state_dir=state_dir)["run_id"]
    steps: list[list[Any]] = []
    readings: list[list[Any]] = []

    for inst in task.instruments:
        if inst.switches is not None:
            call_op(run_id, str(inst.address), str(reference_driver_for(inst.case)),
                    "set_relay", ["0", "true"], state_dir=state_dir)
            steps.append(["call", str(inst.address), "set_relay", 0, True])
    for inst in task.instruments:
        if inst.drives is not None:
            name = inst.drives.split(".", 1)[1]
            volts = next(i.nominal_v for i in card.inputs if i.name == name)
            drive_input(run_id, str(inst.address), volts, state_dir=state_dir)
            steps.append(["drive", str(inst.address), volts])
    for inst in task.instruments:
        points = list(inst.probe_points)
        if not points:
            continue
        driver = str(reference_driver_for(inst.case))
        for at in points if len(points) > 1 else [None]:
            try:
                value: Any = take_measurement(run_id, str(inst.address), driver, at=at,
                                              state_dir=state_dir)["reading"]
            except MeasurementFailed:
                value = "MeasurementFailed"
            steps.append(["measure", str(inst.address), at])
            readings.append([str(inst.address), at, value])

    closed = answer(run_id, "ok", state_dir=state_dir)
    return {"run_id": run_id, "steps": steps, "readings": readings, "answer": closed}


class cached_driver_imports:  # noqa: N801 -- used as a context manager
    """Import each driver file once for the life of the block, the same
    `loader.once_per_path` cache `bench` and the Play server use. The
    registry slots of the reference drivers' compatibles start empty and are
    restored on exit (as `bench` does for its default driver), so a driver
    another caller in this process registered never makes a second claim."""

    def __enter__(self) -> None:
        from shal import registry
        from shal_arena import runner
        from shal_arena.cases import resolve_case
        from shal_arena.loader import once_per_path
        from shal_arena.ui.play import REFERENCE_DRIVERS

        self._runner, self._registry = runner, registry
        self._original = runner._import_driver_file
        compatibles = {resolve_case(c).compatible for c in REFERENCE_DRIVERS}
        self._saved = {c: list(registry._entries.get(c, [])) for c in compatibles}
        for c in compatibles:
            registry._entries[c] = []
        runner._import_driver_file = once_per_path(self._original)

    def __exit__(self, *exc: object) -> None:
        self._runner._import_driver_file = self._original
        for c, entries in self._saved.items():
            self._registry._entries[c] = entries


def packaged_tasks() -> list[Path]:
    from shal_arena.loader import list_tasks

    return [Path(t["path"]) for t in sorted(list_tasks(), key=lambda t: t["name"])]


def score_file(state_dir: str | Path) -> bytes:
    """The whole score file, as the bytes `write` puts on disk."""
    from shal_arena.loader import load_task
    from shal_arena.score import GAME_VERSION

    state_dir = Path(state_dir)
    runs = []
    with cached_driver_imports():
        for task_path in packaged_tasks():
            own = load_task(str(task_path)).task.seed
            for seed in _seeds(own):
                result = play(task_path, seed, state_dir / f"{task_path.stem}-{seed}")
                score = {k: v for k, v in result["answer"]["score"].items()
                         if k not in DROPPED_FIELDS}
                runs.append({"task": task_path.stem, "seed": seed,
                             "steps": result["steps"], "readings": result["readings"],
                             "disqualified": result["answer"]["disqualified"],
                             "score": score})
    doc = {"game_version": GAME_VERSION, "dropped_fields": list(DROPPED_FIELDS), "runs": runs}
    # repr-exact floats, sorted keys, "\n" line ends: the same bytes on every OS
    return (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def compare(paths: list[Path], expect: int | None = None) -> int:
    if expect is not None and len(paths) != expect:
        print(f"seed-scores: expected {expect} score files, got {len(paths)}: "
              f"{[str(p) for p in paths]}")
        return 1
    if not paths:
        print("seed-scores: no score files to compare")
        return 1
    first = paths[0].read_bytes()
    failed = False
    for path in paths[1:]:
        other = path.read_bytes()
        if other == first:
            continue
        failed = True
        a, b = first.splitlines(), other.splitlines()
        line = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        left = a[line].decode("utf-8", "replace") if line < len(a) else "<end of file>"
        right = b[line].decode("utf-8", "replace") if line < len(b) else "<end of file>"
        print(f"seed-scores: {path} differs from {paths[0]} at line {line + 1}:\n"
              f"  {paths[0].name}: {left.strip()}\n  {path.name}: {right.strip()}")
    if failed:
        return 1
    print(f"seed-scores: {len(paths)} files byte-equal ({len(first)} bytes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seed_scores.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_write = sub.add_parser("write", help="play every packaged task, write the score file")
    p_write.add_argument("--out", required=True, type=Path)
    p_write.add_argument("--state-dir", type=Path, default=Path(".seed-scores"))
    p_compare = sub.add_parser("compare", help="exit 0 only when all files are byte-equal")
    p_compare.add_argument("files", nargs="+", type=Path)
    p_compare.add_argument("--expect", type=int, default=None,
                           help="fail unless exactly this many files are given")
    args = parser.parse_args(argv)
    if args.command == "compare":
        return compare(args.files, args.expect)
    data = score_file(args.state_dir)
    args.out.write_bytes(data)
    runs = json.loads(data)["runs"]
    print(f"seed-scores: wrote {args.out} ({len(runs)} runs, {len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

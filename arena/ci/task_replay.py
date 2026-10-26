"""Replay a task PR as data, from its seed, with this branch's code (issue #317).

    python arena/ci/task_replay.py --base BASE_SHA --head HEAD_SHA [--json]

A community task PR adds or changes ``task.yaml`` / ``card.yaml`` files. CI
must never run code from a stranger, so this job never checks out the PR:
the workflow runs it on ``pull_request_target`` (the base branch's workflow
and code), fetches the PR's commit as git objects only, and this script
reads each changed file's bytes with ``git show HEAD_SHA:path``. Then:

1. **Data only.** Each file must sit directly in
   ``arena/src/shal_arena/tasks/`` or ``arena/src/shal_arena/cards/``, end in
   ``.yaml``, parse with ``yaml.safe_load`` (which refuses ``!!python/...``
   tags) and carry ``arena_task: 1`` / ``arena_card: 1`` to match its folder.
   Anything else is refused, and nothing else in the PR is read.
2. **Validated.** The base's packaged tasks and cards are copied to a scratch
   folder, the PR's files laid over them, and each changed task -- and each
   task whose card changed -- goes through `shal_arena.loader.load_task`
   (schema, then the task/card cross-check). Its card must be in that folder.
3. **Replayed from the seed only.** Each valid task is played once at its own
   ``seed:`` by the scripted player in `seed_scores.play` (base code,
   reference drivers, no driver from the PR -- a task PR cannot ship one).

The log is public, so a result names the task, whether it replayed, and the
turn count -- never the fault the seed picks and never a reading.

Exit 0 when every changed task replays; 1 on any refusal or invalid task.
Scoring a submitted score file for a leaderboard is out of scope (#317).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import seed_scores  # noqa: E402

#: repo folder -> (scratch folder, the top-level key a file there must carry)
DATA_DIRS = {
    "arena/src/shal_arena/tasks/": ("tasks", "arena_task"),
    "arena/src/shal_arena/cards/": ("cards", "arena_card"),
}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.yaml$")


class Refused(Exception):
    """A file in the PR is not task or card data."""


def _classify(path: str) -> tuple[str, str, str]:
    """``(scratch folder, file name, required key)`` for one repo path."""
    for prefix, (folder, key) in DATA_DIRS.items():
        if path.startswith(prefix):
            name = path[len(prefix):]
            if _NAME_RE.match(name) and ".." not in name:
                return folder, name, key
    raise Refused(f"{path}: not a task or card file; a task PR may only add "
                  f"<name>.yaml directly under {' or '.join(DATA_DIRS)}")


def _check_data(path: str, data: bytes, key: str) -> None:
    try:
        doc = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        raise Refused(f"{path}: not plain YAML data ({type(e).__name__}: "
                      f"{str(e).splitlines()[0]})") from e
    if not isinstance(doc, dict) or doc.get(key) != 1:
        raise Refused(f"{path}: must be a mapping with `{key}: 1` at the top")


def replay(files: dict[str, bytes], workdir: str | Path) -> list[dict[str, Any]]:
    """Validate and replay the PR's changed data files. Raises `Refused`
    before anything is replayed if any file is not task or card data."""
    from shal_arena.errors import ArenaError
    from shal_arena.loader import load_task

    staged = []
    for path, data in sorted(files.items()):
        folder, name, key = _classify(path)
        _check_data(path, data, key)
        staged.append((folder, name, data))

    workdir = Path(workdir).resolve()
    packaged = Path(seed_scores.packaged_tasks()[0]).parent.parent
    for folder in ("tasks", "cards"):
        shutil.copytree(packaged / folder, workdir / folder, dirs_exist_ok=True)
    for folder, name, data in staged:
        (workdir / folder / name).write_bytes(data)

    changed_cards = {(workdir / "cards" / n).resolve() for f, n, _ in staged if f == "cards"}
    targets = {workdir / "tasks" / n for f, n, _ in staged if f == "tasks"}
    for task_path in sorted((workdir / "tasks").glob("*.yaml")):
        card = (yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}).get("card")
        if isinstance(card, str) and (task_path.parent / card).resolve() in changed_cards:
            targets.add(task_path)

    results = []
    with seed_scores.cached_driver_imports():
        for task_path in sorted(targets):
            rel = f"{task_path.parent.name}/{task_path.name}"
            try:
                loaded = load_task(str(task_path))
            except ArenaError as e:
                results.append({"path": rel, "valid": False, "replayed": False,
                                "error": str(e), "fix": getattr(e, "fix", "")})
                continue
            if loaded.card_path.resolve().parent != (workdir / "cards").resolve():
                results.append({"path": rel, "valid": False, "replayed": False,
                                "error": f"card {loaded.task.card!r} is outside the cards "
                                         f"folder", "fix": "use card: ../cards/<name>.yaml"})
                continue
            state_dir = workdir / "runs" / task_path.stem
            try:
                played = seed_scores.play(task_path, loaded.task.seed, state_dir)
            except ArenaError as e:
                results.append({"path": rel, "task_id": loaded.task.id, "valid": True,
                                "replayed": False, "error": str(e),
                                "fix": getattr(e, "fix", "")})
                continue
            results.append({"path": rel, "task_id": loaded.task.id, "valid": True,
                            "replayed": True, "turns": played["answer"]["score"]["turns"]})
    return results


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def changed_files(base: str, head: str) -> dict[str, bytes]:
    """Added or modified files under the data folders, read as blobs from
    ``head`` -- never checked out. Every other changed file is ignored here."""
    names = _git("diff", "--name-only", "--diff-filter=AM", f"{base}...{head}", "--",
                 *DATA_DIRS).decode("utf-8").splitlines()
    return {n: _git("show", f"{head}:{n}") for n in names if n}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="task_replay.py", description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="the PR's base commit")
    parser.add_argument("--head", required=True, help="the PR's head commit (fetched, "
                                                      "not checked out)")
    parser.add_argument("--workdir", type=Path, default=Path(".task-replay"))
    parser.add_argument("--json", action="store_true", help="print one JSON document")
    args = parser.parse_args(argv)
    files = changed_files(args.base, args.head)
    try:
        results = replay(files, args.workdir)
    except Refused as e:
        doc = {"ok": False, "error": {"type": "Refused", "message": str(e),
                                      "fix": "a task PR changes only task/card yaml files"}}
        print(json.dumps(doc, indent=2) if args.json else f"task-replay: REFUSED {e}")
        return 1
    ok = all(r["replayed"] for r in results)
    if args.json:
        print(json.dumps({"ok": ok, "files": sorted(files), "results": results}, indent=2))
    else:
        if not files:
            print("task-replay: no task or card file changed")
        for r in results:
            state = (f"replayed from its seed, {r['turns']} turns" if r["replayed"]
                     else f"FAILED: {r['error']} (fix: {r['fix']})")
            print(f"task-replay: {r['path']}: {state}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

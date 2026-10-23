"""Build the static site (issue #412): index, replay page, result card.

    python site/build.py --out build/site

Offline and config-free. The replay and the card come from one real,
finished run of the packaged `rail-3v3` task, played here with the packaged
reference drivers in a throwaway state folder, then rendered by the arena's
own exporters (`shal_arena.ui.export`, `shal_arena.replay.card`). Uses only
the stdlib plus what the repo already depends on.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

INSTALL_LINE = "uvx shal-arena demo"
TASK = "rail-3v3"
SEED = 1
AGENT = "sample agent"


def _ensure_importable() -> None:
    """Run from a clone with nothing installed: put the sources on sys.path."""
    for src in (ROOT / "src", ROOT / "arena" / "src"):
        if src.is_dir() and str(src) not in sys.path:
            sys.path.insert(0, str(src))


def render_run_pages() -> tuple[str, str]:
    """Play one run to the end; return (replay html, result card html)."""
    _ensure_importable()
    from shal_arena.loader import load_task, resolve_task
    from shal_arena.replay.card import build_result_card
    from shal_arena.runner import answer, drive_input, pick_fault, start_run, take_measurement
    from shal_arena.ui.export import build_export
    from shal_arena.ui.play import REFERENCE_DRIVERS

    task_path = str(resolve_task(TASK))
    fault = pick_fault(load_task(task_path).card, SEED)
    with tempfile.TemporaryDirectory(prefix="shal-site-") as tmp:
        run_id = start_run(task_path, seed=SEED, state_dir=tmp)["run_id"]
        drive_input(run_id, "psu0", 5.0, state_dir=tmp)
        take_measurement(run_id, "dmm0", REFERENCE_DRIVERS["dmm"], state_dir=tmp)
        answer(run_id, fault, state_dir=tmp)
        replay = build_export(run_id, state_dir=tmp, agent=AGENT)
        card = build_result_card(run_id, state_dir=tmp)
    return replay, card


def build(out: Path) -> list[Path]:
    replay, card = render_run_pages()
    out.mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    shutil.copyfile(HERE / "assets" / "site.css", out / "assets" / "site.css")
    index = (HERE / "index.html").read_text(encoding="utf-8").replace(
        "{{INSTALL_LINE}}", INSTALL_LINE)
    written = []
    for name, text in (("index.html", index), ("replay.html", replay), ("card.html", card)):
        path = out / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the static site.")
    parser.add_argument("--out", default="build/site", help="output folder (default build/site)")
    args = parser.parse_args(argv)
    for path in build(Path(args.out)):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Cold-start eval — staging runner (build -> fresh venv -> install pyshal[mcp] -> scaffold).

This script does NOTHING device-facing. It builds the wheel from the current working
tree, makes a throwaway virtualenv, installs ``pyshal[mcp]`` (and ONLY that -- never a
device extra), drops a guard ``sitecustomize.py`` into the venv that makes the bundled
``sonos,speaker`` driver and every built-in simulator UNRESOLVABLE (so the cold-start
honor rule becomes an enforced wall, not a checkbox), records the install timing +
build provenance, scaffolds a timestamped per-device run directory seeded from the
report template and a schema-conformant metrics file, and then prints exactly what the
evaluator agent should do next. It never runs the evaluation and never touches
hardware, so it is safe to run on a machine with no device present.

    python eval/cold-start/stage_run.py --device sonos
    python eval/cold-start/stage_run.py --device deebot

See eval/cold-start/README.md and eval/cold-start/AGENT_BRIEF.md.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent          # eval/cold-start
REPO_ROOT = HERE.parents[1]                      # repo root (eval/cold-start -> eval -> root)
TEMPLATE_REPORT = HERE / "templates" / "REPORT.md"
METRICS_SCHEMA = HERE / "metrics.schema.json"
GUARD_SHIM = HERE / "sitecustomize_guard.py"     # copied into the venv as sitecustomize.py
DEVICES_DIR = HERE / "devices"
DEFAULT_RUNS_DIR = HERE / "runs"

DEVICES = ("sonos", "deebot")

# Install pyshal[mcp] ONLY. Installing pyshal[sonos] would pull `soco` and hand the
# agent the bundled-driver shortcut the cold-start test exists to forbid.
INSTALL_EXTRAS = "mcp"


def _log(msg: str) -> None:
    print(f"[stage_run] {msg}")


def _now_iso() -> str:
    """Offset-aware ISO-8601 with millisecond precision (matches the schema pattern)."""
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def venv_bin_dir(venv_dir: Path) -> Path:
    """The directory holding the venv's executables — 'Scripts' on Windows, 'bin' else."""
    return venv_dir / ("Scripts" if sys.platform == "win32" else "bin")


def venv_python(venv_dir: Path) -> Path:
    exe = "python.exe" if sys.platform == "win32" else "python"
    return venv_bin_dir(venv_dir) / exe


def venv_console_script(venv_dir: Path, name: str) -> Path:
    """Path to an installed console script (e.g. 'shal-mcp') inside the venv."""
    suffix = ".exe" if sys.platform == "win32" else ""
    return venv_bin_dir(venv_dir) / f"{name}{suffix}"


def venv_site_packages(venv_dir: Path) -> Path:
    """The venv's site-packages dir (where sitecustomize.py must land to auto-import)."""
    if sys.platform == "win32":
        return venv_dir / "Lib" / "site-packages"
    # posix_prefix gives e.g. lib/python3.12/site-packages
    rel = Path(sysconfig.get_path("purelib", "posix_prefix",
                                  vars={"base": "", "platbase": ""}))
    return venv_dir / rel.relative_to(rel.anchor) if rel.is_absolute() else venv_dir / rel


def run(cmd: list[str], cwd: Path | None = None,
        capture: bool = False) -> subprocess.CompletedProcess:
    _log("$ " + " ".join(str(c) for c in cmd))
    try:
        return subprocess.run(
            [str(c) for c in cmd], cwd=str(cwd) if cwd else None, check=True,
            text=True, capture_output=capture)
    except FileNotFoundError as e:
        # e.g. a bad --python path: surface it legibly instead of a raw CreateProcess trace.
        raise SystemExit(f"[stage_run] command not found: {cmd[0]} ({e})") from None


def newest_wheel(dist_dir: Path) -> Path | None:
    wheels = sorted(dist_dir.glob("pyshal-*.whl"), key=lambda p: p.stat().st_mtime)
    return wheels[-1] if wheels else None


def _clean_build_dir() -> None:
    """Remove a stale ./build staging tree before building.

    setuptools' bdist step does a post-build rmtree of build/bdist.*/wheel; on a
    Google-Drive-synced working tree the sync daemon can hold a just-written file open
    and that rmtree fails with 'directory is not empty', leaving a stale tree that
    breaks the NEXT build too. Clearing it up front makes the build idempotent.
    """
    build_dir = REPO_ROOT / "build"
    if build_dir.exists():
        _log(f"removing stale build dir {build_dir}")
        shutil.rmtree(build_dir, ignore_errors=True)


def _build_once(python: str, outdir: Path) -> None:
    # Build into an outdir OFF the synced Drive (a temp dir) to dodge the sync-daemon
    # file-lock race on bdist cleanup; we copy the wheel back into dist/ afterwards.
    run([python, "-m", "build", "--wheel", "--outdir", str(outdir), str(REPO_ROOT)],
        cwd=REPO_ROOT)


def build_wheel(python: str, keep_existing: bool) -> Path:
    """Build the wheel from the repo with `python -m build`, return its path in dist/.

    Mirrors CONTRIBUTING.md / RELEASING.md (`python -m build`). The local wheel installs
    exactly like a real PyPI release, without publishing. Robust to the
    Google-Drive-on-Windows bdist-cleanup race: it cleans a stale build/ first, builds
    into a temp outdir, and retries once (the retry has been reliable in practice).
    """
    dist_dir = REPO_ROOT / "dist"
    if keep_existing:
        existing = newest_wheel(dist_dir)
        if existing is None:
            raise SystemExit(
                "[stage_run] --keep-existing-dist set but no pyshal-*.whl in dist/. "
                "Run without the flag to build one.")
        _log(f"reusing existing wheel: {existing.name}")
        return existing

    dist_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shal-build-") as tmp:
        tmp_out = Path(tmp)
        _log("building wheel with `python -m build` ...")
        _clean_build_dir()
        try:
            _build_once(python, tmp_out)
        except subprocess.CalledProcessError:
            # The Drive sync race is transient: clean and retry once before giving up.
            _log("first build failed (likely the Drive bdist-cleanup race); "
                 "cleaning and retrying once ...")
            _clean_build_dir()
            try:
                _build_once(python, tmp_out)
            except subprocess.CalledProcessError:
                raise SystemExit(
                    "[stage_run] wheel build failed twice. Ensure `build` is installed "
                    "(`pip install build`); if on a Google-Drive-backed checkout, pause "
                    "syncing or `rm -rf build/` and retry, or use --keep-existing-dist."
                ) from None
        built = sorted(tmp_out.glob("pyshal-*.whl"), key=lambda p: p.stat().st_mtime)
        if not built:
            raise SystemExit(
                "[stage_run] build reported success but produced no wheel.")
        src = built[-1]
        dst = dist_dir / src.name
        shutil.copyfile(src, dst)
    _log(f"built {dst.name}")
    return dst


def make_venv(python: str, venv_dir: Path) -> None:
    _log(f"creating throwaway venv at {venv_dir}")
    run([python, "-m", "venv", str(venv_dir)])
    vpy = venv_python(venv_dir)
    run([vpy, "-m", "pip", "install", "--upgrade", "pip"])


def install_guard_shim(venv_dir: Path) -> None:
    """Drop sitecustomize.py into the venv so the bundled sonos driver and all sims
    are UNRESOLVABLE. Python auto-imports sitecustomize at interpreter startup, so any
    `shal.load(...)` of `sonos,speaker` (or an `address: sim` device) raises LoadError
    'no driver installed' — the forbidden bundled-driver shortcut becomes a real wall."""
    site = venv_site_packages(venv_dir)
    site.mkdir(parents=True, exist_ok=True)
    dst = site / "sitecustomize.py"
    shutil.copyfile(GUARD_SHIM, dst)
    _log(f"installed cold-start guard shim at {dst}")


def install_pyshal(venv_dir: Path, wheel: Path) -> tuple[str, str]:
    """Install the freshly built wheel with the [mcp] extra ONLY.

    Returns (t_install_start, t_install_done) as offset-aware ISO-ms strings, measured
    around the pip install so the headline metric's denominator is runner-observed, not
    agent-guessed.
    """
    vpy = venv_python(venv_dir)
    spec = f"{wheel}[{INSTALL_EXTRAS}]"
    _log(f"installing pyshal[{INSTALL_EXTRAS}] from the local wheel (no device extras)")
    t_install_start = _now_iso()
    run([vpy, "-m", "pip", "install", spec])
    t_install_done = _now_iso()
    return t_install_start, t_install_done


def snapshot_freeze(venv_dir: Path, run_dir: Path) -> None:
    """Record `pip freeze` right after install, so a reviewer (and verify_run.py) can
    confirm only pyshal[mcp] was present — no `soco`, no device extra — at staging."""
    vpy = venv_python(venv_dir)
    proc = run([vpy, "-m", "pip", "freeze"], capture=True)
    (run_dir / "pip-freeze.install.txt").write_text(proc.stdout, encoding="utf-8")
    _log("wrote pip-freeze.install.txt (post-install package snapshot)")


def _git_sha() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
            check=True, text=True, capture_output=True)
        return proc.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def _pyshal_version_from_wheel(wheel: Path) -> str | None:
    m = re.match(r"pyshal-([^-]+)-", wheel.name)
    return m.group(1) if m else None


def _python_version(python: str) -> str | None:
    try:
        proc = subprocess.run(
            [python, "-c",
             "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
            check=True, text=True, capture_output=True)
        return proc.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def scaffold_run_dir(runs_dir: Path, device: str, wheel: Path, python: str) -> Path:
    """Create runs/<device>-<microsecond-timestamp>/ with report.md + a conformant,
    not-yet-finalized metrics.json (env provenance + t_created prefilled)."""
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")  # microseconds: collision-proof
    run_dir = runs_dir / f"{device}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)

    shutil.copyfile(TEMPLATE_REPORT, run_dir / "report.md")

    metrics = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "device": device,
        "env": {
            "pyshal_version": _pyshal_version_from_wheel(wheel),
            "wheel_filename": wheel.name,
            "git_sha": _git_sha(),
            "platform": sys.platform,
            "python_version": _python_version(python),
        },
        "finalized": False,
        "did_it_work": None,
        "reached_checkpoint": None,
        "wall_reason": None,
        "verdict": None,
        "install_ms": None,
        "install_to_read_ms": None,
        "checkpoints": {
            "t_created": _now_iso(),
            "t_install_start": None,
            "t_install_done": None,
            "t_first_read_attempt": None,
            "t_first_read_ok": None,
            "t_wall_hit": None,
        },
        "read_attempt": {
            "event": "audit",
            "op": None,
            "outcome": None,
            "delivered": None,
            "duration_ms": None,
            "txn": None,
            "device_address": None,
            "raw_response": None,
        },
    }
    _write_metrics(run_dir, metrics)
    return run_dir


def _metrics_path(run_dir: Path) -> Path:
    return run_dir / "metrics.json"


def _write_metrics(run_dir: Path, metrics: dict) -> None:
    _metrics_path(run_dir).write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8")


def record_install_timing(run_dir: Path, t_start: str, t_done: str) -> None:
    """Patch the runner-owned install checkpoints + install_ms into metrics.json.

    The runner does the install and then exits, so it -- not the agent -- is the source
    of truth for t_install_start / t_install_done. install_ms = done - start (ms)."""
    metrics = json.loads(_metrics_path(run_dir).read_text(encoding="utf-8"))
    metrics["checkpoints"]["t_install_start"] = t_start
    metrics["checkpoints"]["t_install_done"] = t_done
    t0 = datetime.datetime.fromisoformat(t_start)
    t1 = datetime.datetime.fromisoformat(t_done)
    metrics["install_ms"] = round((t1 - t0).total_seconds() * 1000.0, 1)
    metrics["reached_checkpoint"] = "installed"
    _write_metrics(run_dir, metrics)
    _log(f"recorded install timing (install_ms={metrics['install_ms']})")


def print_next_steps(device: str, run_dir: Path, venv_dir: Path) -> None:
    brief = HERE / "AGENT_BRIEF.md"
    verify = HERE / "verify_run.py"
    card = DEVICES_DIR / f"{device}.md"
    shal_mcp = venv_console_script(venv_dir, "shal-mcp")
    vpy = venv_python(venv_dir)

    print()
    print("=" * 72)
    print("  STAGING COMPLETE — hand the following to the evaluator agent")
    print("=" * 72)
    print(f"  device         : {device}")
    print(f"  run dir        : {run_dir}")
    print(f"  report (fill)  : {run_dir / 'report.md'}")
    print(f"  metrics (fill) : {run_dir / 'metrics.json'}")
    print(f"  venv python    : {vpy}")
    print(f"  shal-mcp       : {shal_mcp}")
    print("-" * 72)
    print("  Evaluator agent: do this, in order")
    print(f"   1. Read the protocol : {brief}")
    print(f"   2. Read the device   : {card}")
    print("   3. Use ONLY the venv above. Do NOT install device extras")
    print("      (no pyshal[sonos]) and do NOT import shal.drivers.* —")
    print("      treat this device as genuinely unknown. NOTE: this venv has a")
    print("      guard shim that makes `sonos,speaker` and all `address: sim`")
    print("      devices UNRESOLVABLE — the bundled shortcut is a hard wall here.")
    print("   4. Record offset-aware ISO-ms timestamps at the read/wall checkpoints")
    print("      (t_install_* were already written by this runner — do NOT touch them).")
    print("   5. Attempt a READ-ONLY op only. Never actuate.")
    print("   6. STOP at the first wall; record it honestly in report.md +")
    print("      metrics.wall_reason. Set the PASS/PARTIAL/FAIL verdict and")
    print("      finalized=true.")
    print("-" * 72)
    print("  Gate operator: AFTER the agent finishes, verify the run mechanically:")
    print(f"   {vpy} {verify} {run_dir}")
    print("=" * 72)
    print()
    print("  This runner is done. It did NOT run the eval or touch any device.")


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # avoid Windows-codepage mojibake
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="stage_run.py",
        description="Stage a cold-start eval run (build wheel, fresh venv, "
                    "install pyshal[mcp], install the bundled-driver guard shim, "
                    "scaffold a run dir). Does NOT run the eval or touch hardware.")
    ap.add_argument("--device", required=True, choices=DEVICES,
                    help="which device card / run dir to scaffold")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter to build the venv with (default: this one)")
    ap.add_argument("--keep-existing-dist", action="store_true",
                    help="skip the build; reuse the newest pyshal wheel in dist/")
    ap.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR),
                    help="where run dirs are created (default: eval/cold-start/runs)")
    args = ap.parse_args(argv)

    runs_dir = Path(args.runs_dir).resolve()

    wheel = build_wheel(args.python, args.keep_existing_dist)
    run_dir = scaffold_run_dir(runs_dir, args.device, wheel, args.python)
    venv_dir = run_dir / "venv"
    make_venv(args.python, venv_dir)
    install_guard_shim(venv_dir)
    t_start, t_done = install_pyshal(venv_dir, wheel)
    record_install_timing(run_dir, t_start, t_done)
    snapshot_freeze(venv_dir, run_dir)
    print_next_steps(args.device, run_dir, venv_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

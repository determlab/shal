"""agenticQA runner (#78) — one command, red/green, device-agnostic.

Tier 1 (default): run the control loop IN-PROCESS over one or all manuscripts. Hermetic,
deterministic, no network — this is the CI regression net (mirrors the pytest).

    python dev/eval/agenticqa/run.py --all          # every manuscript, approve + deny
    python dev/eval/agenticqa/run.py deebot          # one device, approve path
    python dev/eval/agenticqa/run.py deebot --deny    # the deny path (gated devices)

Release-acceptance: cold-install the EXACT artifact into a throwaway venv and run the
loop from THAT install, so the gate driving the device is the shipped bridge, not the
dev tree (#78 delta 1).

    python dev/eval/agenticqa/run.py --all --from-tarball dist/pyshal-0.2.0.tar.gz

Exit code is 0 only if every run passed — so this is a usable CI / pre-publish gate.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def repo_root() -> Path:
    """Repo root (dev/eval/agenticqa/run.py -> repo)."""
    return Path(__file__).resolve().parents[3]


MANUSCRIPTS = repo_root() / "dev" / "eval" / "manuscripts"


def _all_names() -> list[str]:
    return sorted(p.stem for p in MANUSCRIPTS.glob("*.yaml"))


def _run_one(name: str, *, deny: bool) -> list[dict]:
    # lazy import: the --from-tarball path stays stdlib-only, so CI can run the staged
    # release-acceptance without first installing shal/pyyaml into the outer env.
    from control_loop import load_manuscript, run_control
    m = load_manuscript(MANUSCRIPTS / f"{name}.yaml")
    out = [run_control(m, decision="approve")]
    # only gated devices have a deny-path to exercise
    if deny and m.get("deny_path") and m["expected"]["gated"]:
        out.append(run_control(m, decision="deny"))
    return out


def _report(results: list[dict], *, as_json: bool) -> int:
    ok = all(v["passed"] for v in results)
    if as_json:  # machine-readable ONLY — no trailing summary, so stdout stays valid JSON
        print(json.dumps(results, indent=2, default=str))
        return 0 if ok else 1
    for v in results:
        mark = "PASS" if v["passed"] else "FAIL"
        gate = "gated" if v["gate_exercised"] else "benign"
        print(f"  [{mark}] {v['device']:<8} {v['decision']:<7} ({gate}): "
              f"{v['state_before']!r} -> {v['state_after']!r}  {v['reason']}")
    print(f"\n  {'GREEN' if ok else 'RED'} - {sum(v['passed'] for v in results)}/"
          f"{len(results)} runs passed")
    return 0 if ok else 1


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts" if sys.platform == "win32" else "bin") / (
        "python.exe" if sys.platform == "win32" else "python")


def _staged(args: argparse.Namespace) -> int:
    """Cold-install the exact artifact, then re-run THIS script under the venv python so
    `import shal` resolves to the installed wheel — proving the shipped gate, not the tree."""
    tarball = Path(args.from_tarball).resolve()
    if not tarball.exists():
        print(f"  artifact not found: {tarball}", file=sys.stderr)
        return 2
    # Manage the venv dir ourselves: on Windows an auto-cleanup of a venv (locked
    # python.exe / pip caches) can raise PermissionError and mask a passing run, so we
    # rmtree(ignore_errors) AFTER computing the exit code.
    tmp = tempfile.mkdtemp(prefix="agenticqa-venv-")
    try:
        venv = Path(tmp) / "venv"
        print(f"  creating venv + cold-installing {tarball.name}[mcp] ...")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        vpy = _venv_python(venv)
        subprocess.run([str(vpy), "-m", "pip", "install", "-q", f"{tarball}[mcp]"], check=True)
        freeze = subprocess.run([str(vpy), "-m", "pip", "freeze"],
                                capture_output=True, text=True, check=True).stdout
        print("  installed:", next((ln for ln in freeze.splitlines()
                                    if ln.lower().startswith("pyshal")), "pyshal ?"))
        # re-exec the Tier-1 path under the venv python; control_loop + examples come from
        # the repo tree (PYTHONPATH), `shal` from the cold install (venv site-packages).
        inner = [str(vpy), str(Path(__file__).resolve())]
        inner += (["--all"] if args.all else [args.device])
        if args.deny or args.all:
            inner.append("--deny")
        if args.json:  # honor the outer flag; default stays human-readable
            inner.append("--json")
        # never let a leaked SHAL_APPROVE=auto silently weaken a release-acceptance run.
        env = {**os.environ, "SHAL_APPROVE": "gate",
               "PYTHONPATH": os.pathsep.join([str(repo_root()), str(HERE)])}
        proc = subprocess.run(inner, env=env, capture_output=True, text=True)
        sys.stdout.write(proc.stdout)
        if proc.returncode != 0 and proc.stderr:
            sys.stderr.write(proc.stderr)
        return proc.returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agenticqa", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("device", nargs="?", help="manuscript name (e.g. deebot, sonos)")
    ap.add_argument("--all", action="store_true", help="run every manuscript")
    ap.add_argument("--deny", action="store_true",
                    help="also run the deny-path on gated devices")
    ap.add_argument("--from-tarball", metavar="PATH",
                    help="cold-install this exact pyshal artifact and run from it")
    ap.add_argument("--json", action="store_true", help="emit machine-readable verdicts")
    args = ap.parse_args(argv)

    if not (args.all or args.device):  # validate BEFORE building a venv (fast, legible error)
        ap.error("give a device name or --all")
    if args.from_tarball:
        return _staged(args)
    names = _all_names() if args.all else [args.device]
    results: list[dict] = []
    for n in names:
        results.extend(_run_one(n, deny=args.deny or args.all))
    return _report(results, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())

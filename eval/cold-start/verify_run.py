"""Cold-start eval — post-run verifier (the mechanical gate, run by a HUMAN).

The evaluator agent self-reports; this script is the second pair of eyes. The gate
operator runs it AFTER the agent finishes, against the run directory. It does NOT touch
hardware. It separates two kinds of signal:

  HARD FAILURES (exit non-zero) — unambiguous, machine-checkable facts the agent
  cannot fake away:
    1. metrics.json validates against metrics.schema.json AND is finalized.
    2. The recorded pip freeze contains no device extra (no `soco` line).
    3. No forbidden token, and no `address: sim`, in any AUTHORED MACHINE ARTIFACT —
       i.e. files the agent wrote that are config/code/logs, not prose: *.jsonl
       (captured audit logs), *.yaml/*.yml (authored topologies), *.py (authored
       drivers/buses/scripts). Tokens here mean the agent really used the shortcut.
       Forbidden tokens: 'shal.drivers', 'sonos,speaker', 'pyshal[sonos]', '--device',
       '_SimSonos'; plus an 'address: sim' shape.
    4. If did_it_work=true: device_address is a real (non-sim, non-localhost) host/IP
       and raw_response is present (real-device evidence).

  ADVISORY WARNINGS (printed, do NOT fail the run) — token mentions inside report.md
  PROSE. The report template legitimately *names* the forbidden tokens to warn against
  them, so a substring match there is ambiguous (a warning vs. an admission). The
  verifier strips the template's boilerplate (blockquotes, the integrity checklist) and
  reports any remaining prose mentions for the operator to eyeball, rather than
  auto-rejecting an honest report.

Exit code 0 == the run is admissible as a gate data point; non-zero == reject it.

    python eval/cold-start/verify_run.py runs/sonos-20260616T140321123456
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "metrics.schema.json"

# token -> human reason. Substring match, case-insensitive.
FORBIDDEN_TOKENS: dict[str, str] = {
    "shal.drivers": "referenced a bundled driver module (shal.drivers.*)",
    "sonos,speaker": "used the bundled sonos compatible string (the forbidden shortcut)",
    "pyshal[sonos]": "installed a device extra (pyshal[sonos])",
    "_simsonos": "used the bundled sonos built-in simulator (_SimSonos)",
    "--device": "used curated discovery (shal-mcp --device == the bundled driver)",
    # examples/demos/deebot/ ships a non-packaged deebot driver+bus+sim; reusing its
    # compatibles is the same shortcut as the bundled sonos driver. Author your own.
    # ('ecovacs,deebot' also substring-matches the demo's 'ecovacs,deebot-v2'.)
    "ecovacs,deebot": "reused the demo deebot driver compatible (author your own)",
    "ecovacs,cloud": "reused the demo ecovacs cloud-bus compatible (author your own)",
    "playground,sim-cloud": "used the demo ecovacs cloud simulator (read the real robot)",
}

# address: sim in YAML or JSON/py-dict shapes.
SIM_ADDRESS_PATTERNS = [
    re.compile(r"address\s*:\s*[\"']?sim[\"']?", re.IGNORECASE),          # YAML: address: sim
    re.compile(r"[\"']address[\"']\s*:\s*[\"']sim[\"']", re.IGNORECASE),  # JSON / py dict
]

# Authored MACHINE artifacts: a forbidden token here is an unambiguous violation, so
# it is a HARD failure. (report.md is prose -> advisory; see _scan_report_prose.)
MACHINE_GLOBS = ("*.jsonl", "*.yaml", "*.yml", "*.py")

SIM_LIKE = {"sim", "localhost", "127.0.0.1", "::1", ""}


def _fail(problems: list[str], msg: str) -> None:
    problems.append(msg)


def _under_venv(path: Path, run_dir: Path) -> bool:
    # The venv legitimately contains shal/drivers/*.py and the sitecustomize guard, both
    # of which name the banned tokens; never scan inside it.
    return "venv" in path.relative_to(run_dir).parts


def _scan_machine_artifacts(run_dir: Path, problems: list[str]) -> None:
    seen: set[Path] = set()
    for pattern in MACHINE_GLOBS:
        for path in run_dir.rglob(pattern):
            if path in seen or _under_venv(path, run_dir):
                continue
            seen.add(path)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            low = text.lower()
            for token, reason in FORBIDDEN_TOKENS.items():
                if token in low:
                    _fail(problems, f"{path.name}: {reason} (found '{token}' in an "
                                    f"authored artifact)")
            for rx in SIM_ADDRESS_PATTERNS:
                if rx.search(text):
                    _fail(problems, f"{path.name}: authored a simulator address "
                                    f"('address: sim') instead of the real device")
                    break


def _is_template_boilerplate(line: str) -> bool:
    """Lines where the REPORT template itself names the forbidden tokens (to warn
    against them): markdown blockquotes and the integrity checklist."""
    s = line.lstrip()
    return s.startswith(">") or s.startswith("- [ ]") or s.startswith("- [x]") \
        or s.startswith("- [X]")


def _scan_report_prose(run_dir: Path, warnings: list[str]) -> None:
    report = run_dir / "report.md"
    if not report.exists():
        return
    try:
        text = report.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for i, line in enumerate(text.splitlines(), 1):
        if _is_template_boilerplate(line):
            continue
        low = line.lower()
        for token, reason in FORBIDDEN_TOKENS.items():
            if token in low:
                warnings.append(f"report.md:{i}: mentions '{token}' — {reason}? "
                                f"(prose, review by hand): {line.strip()[:80]}")
        for rx in SIM_ADDRESS_PATTERNS:
            if rx.search(line):
                warnings.append(f"report.md:{i}: mentions 'address: sim' "
                                f"(prose, review by hand): {line.strip()[:80]}")
                break


def _check_pip_freeze(run_dir: Path, problems: list[str]) -> None:
    freeze = run_dir / "pip-freeze.install.txt"
    if not freeze.exists():
        _fail(problems, "missing pip-freeze.install.txt (runner did not snapshot the "
                        "venv, or it was deleted) — cannot prove only pyshal[mcp] was "
                        "installed")
        return
    for line in freeze.read_text(encoding="utf-8").splitlines():
        name = re.split(r"[=<>!~ ]", line.strip(), maxsplit=1)[0].lower()
        if name == "soco":
            _fail(problems, "pip freeze shows 'soco' installed — a device extra "
                            "(pyshal[sonos]) leaked into the venv")


def _validate_metrics(run_dir: Path, problems: list[str]) -> dict | None:
    mpath = run_dir / "metrics.json"
    if not mpath.exists():
        _fail(problems, "missing metrics.json")
        return None
    try:
        data = json.loads(mpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _fail(problems, f"metrics.json is not valid JSON: {e}")
        return None

    try:
        import jsonschema
    except ImportError:
        _fail(problems, "jsonschema not importable — run this with the venv python "
                        "(it is a core pyshal dependency)")
        return data

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as e:
        _fail(problems, f"metrics.json fails schema: {e.message} (at "
                        f"{'/'.join(map(str, e.absolute_path)) or '<root>'})")
    return data


def _check_finalized_and_evidence(data: dict, problems: list[str]) -> None:
    if not data.get("finalized"):
        _fail(problems, "metrics.finalized is not true — the run is the scaffold seed "
                        "or was abandoned, not a completed data point")
    if data.get("did_it_work") is True:
        ra = data.get("read_attempt") or {}
        addr = (ra.get("device_address") or "").strip()
        if addr.lower() in SIM_LIKE:
            _fail(problems, f"did_it_work=true but device_address is '{addr}' "
                            f"(simulator/localhost) — not a real device read")
        elif not _looks_like_host(addr):
            _fail(problems, f"did_it_work=true but device_address '{addr}' does not "
                            f"look like a real IP/host")
        if ra.get("raw_response") in (None, ""):
            _fail(problems, "did_it_work=true but read_attempt.raw_response is empty "
                            "— no real returned data to substantiate the read")


def _looks_like_host(addr: str) -> bool:
    if not addr:
        return False
    try:
        ipaddress.ip_address(addr)
        return True
    except ValueError:
        pass
    # crude hostname check: at least one dot or a non-trivial label
    return bool(re.match(r"^[A-Za-z0-9._-]{2,}$", addr))


def verify(run_dir: Path) -> tuple[list[str], list[str]]:
    """Return (hard_problems, advisory_warnings)."""
    problems: list[str] = []
    warnings: list[str] = []
    if not run_dir.is_dir():
        return [f"run dir does not exist: {run_dir}"], warnings
    data = _validate_metrics(run_dir, problems)
    if data is not None:
        _check_finalized_and_evidence(data, problems)
    _scan_machine_artifacts(run_dir, problems)
    _check_pip_freeze(run_dir, problems)
    _scan_report_prose(run_dir, warnings)
    return problems, warnings


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # avoid Windows-codepage mojibake
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="verify_run.py",
        description="Mechanically verify a finished cold-start run (schema + integrity "
                    "+ real-device evidence). Run by the gate operator AFTER the agent.")
    ap.add_argument("run_dir", help="path to runs/<device>-<timestamp>/")
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    problems, warnings = verify(run_dir)

    print("=" * 72)
    print(f"  VERIFY {run_dir.name}")
    print("=" * 72)
    if warnings:
        print(f"  {len(warnings)} advisory warning(s) (review by hand, not auto-failing):")
        for w in warnings:
            print(f"    ? {w}")
        print("-" * 72)
    if not problems:
        print("  PASS — run is admissible as a gate data point.")
        print("  (Schema-valid, finalized, no forbidden shortcuts in authored "
              "artifacts, evidence consistent.)")
        return 0
    print(f"  REJECT — {len(problems)} problem(s):")
    for p in problems:
        print(f"    - {p}")
    print("-" * 72)
    print("  This run is NOT admissible. Re-run honestly or fix the integrity issue.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

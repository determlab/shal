"""Run the driver-creator benchmark (issue #10), one case per subprocess.

Each case's harness imports modules literally named `driver` / `sim_harness`
off sys.path; running every case in ONE pytest process would collide them. A
fresh subprocess per case gives true isolation — and mirrors how a contributor
actually validates one generated driver at a time.

    python examples/driver-creator/run_benchmark.py            # all cases
    python examples/driver-creator/run_benchmark.py sht31 deebot   # subset
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = ["sht31", "scpi-psu", "http-service", "deebot"]


def _pytest(targets: list[str]) -> bool:
    # each invocation is its own process: the harness and the generated tests each
    # force their OWN sim model into the registry, so they MUST NOT share a process
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", *targets],
                       cwd=ROOT.parents[1])
    return r.returncode == 0


def run_case(case: str) -> bool:
    harness = ROOT / case / "harness"
    generated = ROOT / case / "generated"
    status = "generated" if (generated / "driver.py").exists() else "NOT generated"
    print(f"\n=== {case}  ({status}) " + "=" * (50 - len(case) - len(status)))
    # 1) the independent harness (the real gate — generator never saw it)
    ok = _pytest([str(p) for p in harness.glob("test_*.py")])
    # 2) the generated driver's own tests (separate process — see note above)
    for gen_test in sorted(generated.glob("test_*.py")):
        ok = _pytest([str(gen_test)]) and ok
    return ok


def main() -> int:
    cases = sys.argv[1:] or CASES
    results = {c: run_case(c) for c in cases}
    print("\n" + "=" * 60)
    for c, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {c}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

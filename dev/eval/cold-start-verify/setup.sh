#!/usr/bin/env bash
# Re-runnable setup for the cold-start DoD verification. Run again after ANY fix — same
# process. Builds the candidate tar from SHAL main, makes a FRESH clean venv, cold-installs.
# Device-agnostic: this stages the package; the operator authors the device driver later.
#
#   PYTHON=py-3.12 bash setup.sh    # pick the interpreter a device lib needs (see the card)
set -e
cd "$(dirname "$0")"
SRC=shal-src
PYTHON="${PYTHON:-python}"          # override for a device lib that needs 3.11+ (e.g. Deebot)

echo "== 1. get/refresh SHAL source (main) =="
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch -q origin && git -C "$SRC" reset -q --hard origin/main
else
  rm -rf "$SRC"; gh repo clone determlab/shal "$SRC"
fi

echo "== 2. build the candidate tarball =="
( cd "$SRC" && rm -rf dist && "$PYTHON" -m pip install -q build && "$PYTHON" -m build --sdist )
TAR=$(ls "$SRC"/dist/pyshal-*.tar.gz | head -1)
echo "tarball: $TAR"

echo "== 3. FRESH clean venv (clean start) =="
rm -rf .venv && "$PYTHON" -m venv .venv
PY=.venv/Scripts/python.exe; [ -f "$PY" ] || PY=.venv/bin/python

echo "== 4. cold install from tar =="
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q "${TAR}[mcp]"
"$PY" -c "import sys, shal; print('installed pyshal', getattr(shal,'__version__','?'), 'on py', sys.version.split()[0])"
echo "== READY. clean env: .venv  ·  creds: .env  ·  now spawn operator + approver =="

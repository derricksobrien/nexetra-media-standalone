#!/usr/bin/env bash
set -euo pipefail

ARCHIVE_PATH="${1:-/tmp/nexetra-media-sync.tgz}"
TARGET_ROOT="${HOME}/nexetra-media"

if [[ ! -f "${ARCHIVE_PATH}" ]]; then
  echo "ARCHIVE_MISSING ${ARCHIVE_PATH}" >&2
  exit 2
fi

mkdir -p "${TARGET_ROOT}"
tar -xzf "${ARCHIVE_PATH}" -C "${TARGET_ROOT}"
cd "${TARGET_ROOT}"

if command -v python3 >/dev/null 2>&1; then
  PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PY_BIN="python"
else
  echo "PYTHON_NOT_FOUND" >&2
  exit 4
fi

"${PY_BIN}" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/pip install -q --upgrade setuptools wheel

if [[ -f requirements.txt ]]; then
  .venv/bin/pip install -q -r requirements.txt
fi

if [[ -f viewer/requirements.txt ]]; then
  .venv/bin/pip install -q -r viewer/requirements.txt
fi

if [[ -x .venv/bin/python ]]; then
  echo "PYTHON:READY"
else
  echo "PYTHON:MISSING" >&2
  exit 3
fi

echo "BOOTSTRAP_MAC_OK ${TARGET_ROOT}"

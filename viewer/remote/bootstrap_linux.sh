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

if ! python3 -m venv .venv 2>/tmp/nexetra-venv.err; then
  if command -v apt-get >/dev/null 2>&1; then
    sudo -n apt-get update -y >/dev/null 2>&1 || true
    sudo -n apt-get install -y python3-venv >/dev/null 2>&1 || true
  fi
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip

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

echo "BOOTSTRAP_LINUX_OK ${TARGET_ROOT}"

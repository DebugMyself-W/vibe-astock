#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LEGACY_ROOT="${ROOT_DIR}/legacy/v1"
LEGACY_RESTART="${LEGACY_ROOT}/restart.sh"

if [[ ! -f "${LEGACY_RESTART}" ]]; then
  echo "Legacy v1 restart script is missing: ${LEGACY_RESTART}" >&2
  exit 2
fi

stop_pre_merge_server() {
  local port="${VIBE_PORT:-8910}" pid cmd cwd attempt
  local -a pids=()

  if command -v lsof >/dev/null 2>&1; then
    mapfile -t pids < <(lsof -nP -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)
  elif command -v fuser >/dev/null 2>&1; then
    mapfile -t pids < <(fuser -n tcp "${port}" 2>/dev/null | tr ' ' '\n' | sed '/^$/d')
  elif command -v ss >/dev/null 2>&1; then
    mapfile -t pids < <(ss -ltnp "sport = :${port}" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p')
  fi

  for pid in "${pids[@]}"; do
    [[ -r "/proc/${pid}/cmdline" ]] || continue
    cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
    cmd="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
    if [[ "${cwd}" != "${ROOT_DIR}" || "${cmd}" != *"server.py"* ]]; then
      continue
    fi

    echo "Stopping pre-merge v1 server PID ${pid} on port ${port}..."
    kill -TERM "${pid}"
    for attempt in {1..10}; do
      kill -0 "${pid}" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "${pid}" 2>/dev/null; then
      echo "Pre-merge server did not stop within 10 seconds; sending SIGKILL."
      kill -KILL "${pid}" 2>/dev/null || true
    fi
  done
}

stop_pre_merge_server

# Windows virtual environments use Scripts/python.exe and cannot be reused by
# WSL/Linux. Bootstrap a separate POSIX environment instead of falling back to
# the externally managed system Python (PEP 668).
if [[ -z "${VIBE_PYTHON:-}" ]]; then
  if [[ -n "${VIBE_POSIX_VENV:-}" ]]; then
    POSIX_VENV="${VIBE_POSIX_VENV}"
  else
    CACHE_ROOT="${XDG_CACHE_HOME:-${HOME}/.cache}/vibe-astock"
    VENV_KEY="$(printf '%s' "${LEGACY_ROOT}" | cksum | awk '{print $1}')"
    POSIX_VENV="${CACHE_ROOT}/v1-${VENV_KEY}"
  fi
  if [[ ! -x "${POSIX_VENV}/bin/python" ]]; then
    if command -v python3 >/dev/null 2>&1; then
      BOOTSTRAP_PYTHON="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
      BOOTSTRAP_PYTHON="$(command -v python)"
    else
      echo "Python 3 was not found. Install python3 and python3-venv first." >&2
      exit 2
    fi
    echo "Creating isolated POSIX virtual environment at ${POSIX_VENV}..."
    if ! "${BOOTSTRAP_PYTHON}" -m venv "${POSIX_VENV}"; then
      echo "Could not create the virtual environment. On Ubuntu, install python3-venv and retry." >&2
      exit 2
    fi
  fi
  export VIBE_PYTHON="${POSIX_VENV}/bin/python"
fi

# Keep v1 runtime data inside the archived application unless callers
# explicitly provide their existing data locations.
export ASTOCK_DATA_HOME="${ASTOCK_DATA_HOME:-${LEGACY_ROOT}/.local/data/shortline}"
export VR_DATA_DIR="${VR_DATA_DIR:-${LEGACY_ROOT}/.local/data/market}"
export PYTHONUTF8="${PYTHONUTF8:-1}"

echo "Restarting isolated Vibe AStock v1 from ${LEGACY_ROOT} (default port: ${VIBE_PORT:-8910})."
exec bash "${LEGACY_RESTART}" "$@"

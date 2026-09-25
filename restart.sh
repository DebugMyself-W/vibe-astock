#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
FRONTEND_DIR="${ROOT_DIR}/frontend"
PORT="${VIBE_PORT:-8910}"
HOST="${VIBE_HOST:-127.0.0.1}"
RUNTIME_BASE="${XDG_RUNTIME_DIR:-/tmp}/vibe-astock-${UID}"
PID_FILE="${RUNTIME_BASE}/server-${PORT}.pid"
LOG_FILE="${RUNTIME_BASE}/server-${PORT}.log"

if [[ ! "${PORT}" =~ ^[0-9]+$ ]] || ((PORT < 1 || PORT > 65535)); then
  echo "Invalid VIBE_PORT: ${PORT}" >&2
  exit 2
fi

if [[ -n "${VIBE_PYTHON:-}" ]]; then
  PYTHON_BIN="${VIBE_PYTHON}"
elif [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "Python not found. Create .venv or set VIBE_PYTHON." >&2
  exit 2
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found in PATH." >&2
  exit 2
fi

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "frontend/node_modules is missing. Run npm install in frontend first." >&2
  exit 2
fi

ensure_rollup_native() {
  if (cd -- "${FRONTEND_DIR}" && node -e 'require("rollup")' >/dev/null 2>&1); then
    return 0
  fi

  local platform arch libc package version
  platform="$(node -p 'process.platform')"
  arch="$(node -p 'process.arch')"
  case "${platform}:${arch}" in
    linux:x64|linux:arm64)
      libc="$(node -p 'process.report.getReport().header.glibcVersionRuntime ? "gnu" : "musl"')"
      package="@rollup/rollup-linux-${arch}-${libc}"
      ;;
    darwin:x64|darwin:arm64)
      package="@rollup/rollup-darwin-${arch}"
      ;;
    win32:x64|win32:arm64)
      package="@rollup/rollup-win32-${arch}-msvc"
      ;;
    *)
      echo "Rollup native package is missing and ${platform}/${arch} is not supported by restart.sh." >&2
      return 1
      ;;
  esac
  version="$(cd -- "${FRONTEND_DIR}" && node -p 'require("./node_modules/rollup/package.json").version')"
  echo "Rollup native package is missing for ${platform}/${arch}; installing ${package}@${version}..."
  (cd -- "${FRONTEND_DIR}" && npm install --no-save --package-lock=false --include=optional "${package}@${version}")
  (cd -- "${FRONTEND_DIR}" && node -e 'require("rollup")')
}

ensure_rollup_native

# Keep the current server available until the replacement frontend has built.
echo "Building frontend..."
(
  cd -- "${FRONTEND_DIR}"
  npm run build
)

mkdir -p -- "${RUNTIME_BASE}"

if command -v lsof >/dev/null 2>&1; then
  PORT_INSPECTOR="lsof"
elif command -v fuser >/dev/null 2>&1; then
  PORT_INSPECTOR="fuser"
elif command -v ss >/dev/null 2>&1; then
  PORT_INSPECTOR="ss"
else
  echo "Cannot inspect port ${PORT}: install lsof, fuser, or ss." >&2
  exit 2
fi

process_command() {
  local pid="$1"
  [[ -r "/proc/${pid}/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline"
}

is_project_server() {
  local pid="$1" cmd cwd
  kill -0 "${pid}" 2>/dev/null || return 1
  cmd="$(process_command "${pid}" 2>/dev/null || true)"
  cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
  [[ "${cwd}" == "${ROOT_DIR}" && "${cmd}" == *"server.py"* ]]
}

listener_pids() {
  case "${PORT_INSPECTOR}" in
    lsof)
      lsof -nP -t -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true
      ;;
    fuser)
      fuser -n tcp "${PORT}" 2>/dev/null | tr ' ' '\n' | sed '/^$/d'
      ;;
    ss)
      ss -ltnp "sport = :${PORT}" 2>/dev/null \
        | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p'
      ;;
  esac
}

stop_pid() {
  local pid="$1" attempt
  echo "Stopping server PID ${pid} on port ${PORT}..."
  kill -TERM "${pid}"
  for attempt in {1..10}; do
    kill -0 "${pid}" 2>/dev/null || return 0
    sleep 1
  done
  echo "Server did not stop within 10 seconds; sending SIGKILL."
  kill -KILL "${pid}" 2>/dev/null || true
}

mapfile -t active_pids < <(listener_pids)
for pid in "${active_pids[@]}"; do
  [[ -n "${pid}" ]] || continue
  if ! is_project_server "${pid}"; then
    cmd="$(process_command "${pid}" 2>/dev/null || echo unknown)"
    echo "Port ${PORT} is occupied by a non-project process (PID ${pid}): ${cmd}" >&2
    echo "Refusing to stop it." >&2
    exit 3
  fi
  stop_pid "${pid}"
done

rm -f -- "${PID_FILE}"

echo "Starting server on ${HOST}:${PORT}..."
cd -- "${ROOT_DIR}"
nohup env VIBE_HOST="${HOST}" VIBE_PORT="${PORT}" \
  "${PYTHON_BIN}" server.py >> "${LOG_FILE}" 2>&1 &
new_pid=$!
printf '%s\n' "${new_pid}" > "${PID_FILE}"

health_host="${HOST}"
if [[ "${health_host}" == "0.0.0.0" || "${health_host}" == "::" ]]; then
  health_host="127.0.0.1"
fi
health_url="${VIBE_HEALTH_URL:-http://${health_host}:${PORT}/api/health}"

for attempt in {1..30}; do
  if ! kill -0 "${new_pid}" 2>/dev/null; then
    echo "Server exited before becoming healthy. Recent log:" >&2
    tail -n 40 -- "${LOG_FILE}" >&2 || true
    exit 1
  fi
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 2 "${health_url}" >/dev/null 2>&1; then
      echo "Server is ready: http://${health_host}:${PORT}"
      echo "Log: ${LOG_FILE}"
      exit 0
    fi
  elif (echo > "/dev/tcp/${health_host}/${PORT}") >/dev/null 2>&1; then
    echo "Server is listening: http://${health_host}:${PORT}"
    echo "Log: ${LOG_FILE}"
    exit 0
  fi
  sleep 1
done

echo "Server did not become healthy within 30 seconds. Recent log:" >&2
tail -n 40 -- "${LOG_FILE}" >&2 || true
exit 1

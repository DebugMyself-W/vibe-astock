#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LEGACY_ROOT="${ROOT_DIR}/legacy/v1"
LEGACY_RESTART="${LEGACY_ROOT}/restart.sh"

if [[ ! -f "${LEGACY_RESTART}" ]]; then
  echo "Legacy v1 restart script is missing: ${LEGACY_RESTART}" >&2
  exit 2
fi

# Keep v1 runtime data inside the archived application unless callers
# explicitly provide their existing data locations.
export ASTOCK_DATA_HOME="${ASTOCK_DATA_HOME:-${LEGACY_ROOT}/.local/data/shortline}"
export VR_DATA_DIR="${VR_DATA_DIR:-${LEGACY_ROOT}/.local/data/market}"
export PYTHONUTF8="${PYTHONUTF8:-1}"

echo "Restarting isolated Vibe AStock v1 from ${LEGACY_ROOT} (default port: ${VIBE_PORT:-8910})."
exec bash "${LEGACY_RESTART}" "$@"

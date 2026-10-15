#!/usr/bin/env bash

# Normalise proxy variables before spawning long-lived services. WSL can import
# lowercase values from Windows while shell startup files set stale uppercase
# values. Network clients disagree about which spelling wins, so child processes
# must receive one coherent proxy configuration.
normalize_vibe_proxy_env() {
  local old_http="${HTTP_PROXY:-}"
  local old_https="${HTTPS_PROXY:-}"
  local old_all="${ALL_PROXY:-}"
  local canonical="${VIBE_PROXY_URL:-${https_proxy:-${http_proxy:-${HTTPS_PROXY:-${HTTP_PROXY:-}}}}}"
  local canonical_no_proxy="${no_proxy:-${NO_PROXY:-}}"

  [[ -n "${canonical}" ]] || return 0

  if [[ -n "${VIBE_PROXY_URL:-}" ]]; then
    export ALL_PROXY="${canonical}" all_proxy="${canonical}"
  elif [[ -n "${all_proxy:-}" ]]; then
    export ALL_PROXY="${all_proxy}"
  elif [[ -n "${old_all}" ]]; then
    # If ALL_PROXY came from the same stale shell configuration as the uppercase
    # HTTP variables, move it to the selected lowercase Windows/WSL proxy too.
    if [[ "${old_all}" == "${old_http}" || "${old_all}" == "${old_https}" ]]; then
      export ALL_PROXY="${canonical}" all_proxy="${canonical}"
    else
      export all_proxy="${old_all}"
    fi
  else
    export ALL_PROXY="${canonical}" all_proxy="${canonical}"
  fi

  export HTTP_PROXY="${canonical}" http_proxy="${canonical}"
  export HTTPS_PROXY="${canonical}" https_proxy="${canonical}"
  if [[ -n "${canonical_no_proxy}" ]]; then
    export NO_PROXY="${canonical_no_proxy}" no_proxy="${canonical_no_proxy}"
  fi

  if [[ (-n "${old_http}" && "${old_http}" != "${canonical}") ||
        (-n "${old_https}" && "${old_https}" != "${canonical}") ]]; then
    echo "Proxy environment had conflicting uppercase/lowercase values; using one canonical proxy for the service." >&2
  fi
}

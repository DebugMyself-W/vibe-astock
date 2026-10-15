from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "proxy_env.sh"


def _normalized_proxy_env(extra: dict[str, str]) -> list[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
        and key != "VIBE_PROXY_URL"
    }
    env.update(extra)
    command = (
        f'source "{HELPER}"; normalize_vibe_proxy_env; '
        "printf '%s\\n' \"$HTTP_PROXY\" \"$http_proxy\" "
        "\"$HTTPS_PROXY\" \"$https_proxy\" \"$ALL_PROXY\" \"$all_proxy\" "
        "\"$NO_PROXY\" \"$no_proxy\""
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def test_lowercase_wsl_proxy_wins_over_stale_uppercase_proxy() -> None:
    current = "http://127.0.0.1:7890"
    stale = "http://172.19.48.1:7890"

    values = _normalized_proxy_env(
        {
            "HTTP_PROXY": stale,
            "HTTPS_PROXY": stale,
            "ALL_PROXY": stale,
            "NO_PROXY": "localhost,127.0.0.1",
            "http_proxy": current,
            "https_proxy": current,
            "no_proxy": "localhost,127.0.0.1,::1",
        }
    )

    assert values[:6] == [current] * 6
    assert values[6:] == ["localhost,127.0.0.1,::1"] * 2


def test_explicit_project_proxy_overrides_inherited_values() -> None:
    explicit = "http://host.docker.internal:7890"

    values = _normalized_proxy_env(
        {
            "VIBE_PROXY_URL": explicit,
            "HTTP_PROXY": "http://old.invalid:7890",
            "http_proxy": "http://another.invalid:7890",
        }
    )

    assert values[:6] == [explicit] * 6

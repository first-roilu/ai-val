"""Версия агента для паспорта прогона: версия пакета + git describe."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from reqcheck import __version__


@lru_cache(maxsize=1)
def agent_version() -> str:
    root = Path(__file__).resolve().parent.parent
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        desc = out.stdout.strip()
        if out.returncode == 0 and desc:
            return f"{__version__} ({desc})"
    except (OSError, subprocess.SubprocessError):
        pass
    return __version__

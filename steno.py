#!/bin/sh
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pywebview>=5.3",
#   "markdown>=3.5",
#   "static-ffmpeg>=2.7",
# ]
# ///
"true" '''\'
# 2>nul & goto :windows_boot
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.local/share/mise/shims:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -f /etc/alpine-release ] && [ ! -f /usr/lib/libstdc++.so.6 ] && command -v apk >/dev/null 2>&1; then
  apk add --no-cache libstdc++ >/dev/null 2>&1 || true
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (standalone Python runtime) / Installerer uv..."
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  fi
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
exec uv run --python 3.12 --script "$0" "$@"
:windows_boot
@echo off
cls
where uv >nul 2>nul || (
  echo Installerer uv ^(frittstaaende Python-motor^)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
set "PATH=%USERPROFILE%\\.local\\bin;%LOCALAPPDATA%\\Microsoft\\WinGet\\Links;%PATH%"
uv run --python 3.12 --script "%~f0" %*
exit /b %ERRORLEVEL%
'''
# Cross-platform GUI & CLI entrypoint for Steno - a modern annalist for your meetings - an automaton stenographer.
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _self_bootstrap_if_needed() -> None:
    """If launched via raw 'python3 steno.py' without uv, re-exec inside uv environment."""
    if any(flag in sys.argv for flag in ("-h", "--help", "--self-test", "--check-hw", "--status")):
        return
    try:
        import markdown  # noqa: F401
        import static_ffmpeg  # noqa: F401
        import webview  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    print("Preparing standalone Python environment via uv / Forbereder frittstående Python-miljø via uv...")
    search_paths = [
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".cargo" / "bin"),
        str(Path.home() / ".local" / "share" / "mise" / "shims"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        os.environ.get("PATH", ""),
    ]
    os.environ["PATH"] = os.pathsep.join(p for p in search_paths if p)
    uv_bin = shutil.which("uv")
    if not uv_bin:
        raise RuntimeError("Fant ikke 'uv'. Kjør './steno.py' for automatisk installasjon.")
    cmd = [uv_bin, "run", "--python", "3.12", "--script", str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.call(cmd))


_self_bootstrap_if_needed()

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mote.gui import entrypoint  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(entrypoint())

#!/bin/sh
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pywebview>=5.3; sys_platform == 'darwin' or sys_platform == 'win32' or sys_platform == 'linux'",
#   "markdown>=3.5",
#   "static-ffmpeg>=2.7; sys_platform == 'darwin' or (sys_platform == 'win32' and platform_machine == 'AMD64') or (sys_platform == 'linux' and platform_machine == 'x86_64')",
# ]
# ///
"true" '''\'
# 2>nul & goto :windows_boot
export PATH="${PREFIX:-/data/data/com.termux/files/usr}/bin:$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.local/share/mise/shims:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -n "${TERMUX_VERSION:-}" ] || [ -d "/data/data/com.termux/files/usr" ] || [ "$(uname -o 2>/dev/null)" = "Android" ]; then
  export STENO_TERMUX=1
  command -v python3 >/dev/null 2>&1 || (command -v pkg >/dev/null 2>&1 && pkg install -y python ffmpeg >/dev/null 2>&1) || true
  exec python3 "$0" "$@"
fi
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
  echo Installing uv ^(standalone Python runtime^) / Installerer uv...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
set "PATH=%USERPROFILE%\\.local\\bin;%LOCALAPPDATA%\\uv\\bin;%LOCALAPPDATA%\\Microsoft\\WinGet\\Links;%PATH%"
uv run --python 3.12 --script "%~f0" %*
exit /b %ERRORLEVEL%
'''
# Cross-platform GUI & CLI entrypoint for Steno - a modern annalist for your meetings - an automaton stenographer.
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


def _self_bootstrap_if_needed() -> None:
    """If launched via raw 'python3 steno.py' without uv, auto-install uv and re-exec (bypassed on Termux)."""
    if any(
        flag in sys.argv
        for flag in ("-h", "--help", "--self-test", "--check-hw", "--status", "--install", "--uninstall")
    ):
        return
    if os.environ.get("STENO_BOOTSTRAPPED") == "1" or os.environ.get("STENO_TERMUX") == "1":
        return
    if (
        "TERMUX_VERSION" in os.environ
        or Path("/data/data/com.termux/files/usr").is_dir()
        or "android" in platform.platform().lower()
    ):
        return
    try:
        import markdown  # noqa: F401
        import webview  # noqa: F401

        machine = platform.machine().lower()
        needs_static_ffmpeg = (
            sys.platform == "darwin"
            or (sys.platform == "win32" and machine in ("amd64", "x86_64"))
            or (sys.platform == "linux" and machine in ("x86_64", "amd64"))
        )
        if needs_static_ffmpeg:
            import static_ffmpeg  # noqa: F401
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
        print("Laster ned uv...")
        if os.name == "nt":
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    "irm https://astral.sh/uv/install.ps1 | iex",
                ],
                check=True,
            )
        else:
            sh_bin = "/bin/sh" if Path("/bin/sh").exists() else (shutil.which("sh") or "/system/bin/sh")
            with urllib.request.urlopen("https://astral.sh/uv/install.sh", timeout=30) as resp:
                install_script = resp.read()
            subprocess.run([sh_bin], input=install_script, check=True)
        os.environ["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + os.environ["PATH"]
        uv_bin = shutil.which("uv")

    if not uv_bin:
        raise RuntimeError("Klarte ikke å finne eller installere 'uv'.")

    os.environ["STENO_BOOTSTRAPPED"] = "1"
    cmd = [uv_bin, "run", "--python", "3.12", "--script", str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.call(cmd))


_self_bootstrap_if_needed()

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mote.gui import entrypoint  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(entrypoint())

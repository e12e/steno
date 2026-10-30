#!/usr/bin/env python3
"""Build a single-file, zero-dependency, cross-platform runnable distribution of mote.

Produces dist/mote_app.py which can run directly on macOS (ARM64/x86_64),
Windows 11 (x86_64/ARM64), and Linux without manual dependency installation,
via an embedded uv polyglot self-bootstrapper.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src" / "mote"
DIST_DIR = ROOT_DIR / "dist"
DIST_FILE = DIST_DIR / "mote_app.py"

POLYGLOT_HEADER = r'''#!/bin/sh
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pywebview>=5.3",
#   "markdown>=3.5",
#   "static-ffmpeg>=2.7",
# ]
# ///
"true" ''' + "'''\\" + r'''
goto :windows_boot >/dev/null 2>&1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.local/share/mise/shims:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installerer uv (frittstaaende Python-motor)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
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
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
uv run --python 3.12 --script "%~f0" %*
exit /b %ERRORLEVEL%
''' + "'''" + r'''
# Universal single-file distribution for Møteopptak, Transkripsjon & Referat (WhisperX + Ollama).
# Automatically bootstrapped for macOS (ARM64/AMD64), Windows 11, and Linux.
from __future__ import annotations

import sys
from pathlib import Path
'''

BOOTSTRAP_CODE = r'''
def _self_bootstrap_if_needed() -> None:
    """If launched via raw 'python mote_app.py' without uv, auto-install uv and re-exec."""
    try:
        import markdown  # noqa: F401
        import static_ffmpeg  # noqa: F401
        import webview  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    print("Forbereder frittstående Python-miljø via uv...")
    import os, shutil, subprocess
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
            subprocess.run(
                "curl -LsSf https://astral.sh/uv/install.sh | sh",
                shell=True,
                check=True,
            )
        os.environ["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + os.environ["PATH"]
        uv_bin = shutil.which("uv")

    if not uv_bin:
        raise RuntimeError("Klarte ikke å finne eller installere 'uv'.")

    cmd = [uv_bin, "run", "--python", "3.12", "--script", str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.call(cmd))

_self_bootstrap_if_needed()
'''

CLI_CODE = r'''
def _maybe_run_cli_from_args() -> None:
    if __name__ != "__main__":
        return
    argv = sys.argv[1:]
    if not argv:
        return
    if argv[0] == "--cli":
        argv = argv[1:]
    if argv[0] in ("-h", "--help", "--status", "--live-check") or not argv[0].startswith("--"):
        raise SystemExit(cli_main(argv))

_maybe_run_cli_from_args()
'''


def build() -> int:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    print(f"==> Bygger universell enkeltfil: {DIST_FILE}")

    tpl_app = (SRC_DIR / "templates" / "app.html").read_text(encoding="utf-8")
    tpl_transcript = (SRC_DIR / "templates" / "transcript.html").read_text(encoding="utf-8")
    tpl_notes = (SRC_DIR / "templates" / "notes.html").read_text(encoding="utf-8")

    core_code = (SRC_DIR / "core.py").read_text(encoding="utf-8")
    gui_code = (SRC_DIR / "gui.py").read_text(encoding="utf-8")

    core_clean = re.sub(r'from __future__ import annotations\s*', '', core_code)
    core_clean = re.sub(r'from mote\.template_loader import load_template\s*', '', core_clean)
    core_clean = re.sub(r'from mote\..*?\n', '', core_clean)

    gui_clean = re.sub(r'from __future__ import annotations\s*', '', gui_code)
    gui_clean = re.sub(r'from mote\..*?\n', '', gui_clean)
    gui_clean = re.sub(r'from mote_core\..*?\n', '', gui_clean)
    gui_clean = re.sub(r'import mote_core\s*', '', gui_clean)

    bundle_parts = [
        POLYGLOT_HEADER,
        BOOTSTRAP_CODE,
        f'''
# --- EMBEDDED TEMPLATES ---
_EMBEDDED_TEMPLATES = {{
    "app.html": """{tpl_app}""",
    "transcript.html": """{tpl_transcript}""",
    "notes.html": """{tpl_notes}""",
}}

def load_template(name: str, fallback: str = "") -> str:
    tpl_file = Path(__file__).resolve().parent / "templates" / name
    if tpl_file.is_file():
        try:
            return tpl_file.read_text(encoding="utf-8")
        except Exception:
            pass
    return _EMBEDDED_TEMPLATES.get(name, fallback)

HTML = load_template("app.html")
''',
        "\n# --- CORE ENGINE ---\n",
        core_clean,
        "\n# --- DESKTOP GUI & ENGINE ---\n",
        gui_clean,
        "\n# --- ENTRYPOINT DISPATCHER ---\n",
        CLI_CODE,
    ]

    out_content = "".join(bundle_parts)
    DIST_FILE.write_text(out_content, encoding="utf-8")
    DIST_FILE.chmod(0o755)

    print(f"==> Ferdig bygget! Størrelse: {DIST_FILE.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())

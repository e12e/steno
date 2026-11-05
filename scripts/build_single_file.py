#!/usr/bin/env python3
"""Build a single-file, zero-dependency, cross-platform runnable distribution of Steno.

Produces dist/steno.py which can run directly on macOS (ARM64/x86_64),
Windows 11 (x86_64/ARM64), and Linux without manual dependency installation,
via an embedded uv polyglot self-bootstrapper.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src" / "mote"
DIST_DIR = ROOT_DIR / "dist"
DIST_FILE = DIST_DIR / "steno.py"

POLYGLOT_HEADER = r'''#!/bin/sh
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pywebview>=5.3",
#   "markdown>=3.5",
#   "static-ffmpeg>=2.7",
# ]
# ///
"true" ''' + "'''\\'" + r'''
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
  echo Installing uv ^(standalone Python runtime^) / Installerer uv...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
set "PATH=%USERPROFILE%\\.local\\bin;%LOCALAPPDATA%\\Microsoft\\WinGet\\Links;%PATH%"
uv run --python 3.12 --script "%~f0" %*
exit /b %ERRORLEVEL%
''' + "'''" + r'''
# Universal single-file distribution for Steno - a modern annalist for your meetings - an automaton stenographer.
# Automatically bootstrapped for macOS (ARM64/AMD64), Windows 11, and Linux.
from __future__ import annotations

import sys
from pathlib import Path
'''

BOOTSTRAP_CODE = r'''
def _self_bootstrap_if_needed() -> None:
    """If launched via raw 'python steno.py' without uv, auto-install uv and re-exec."""
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
            import urllib.request
            with urllib.request.urlopen("https://astral.sh/uv/install.sh", timeout=30) as resp:
                install_script = resp.read()
            subprocess.run(["/bin/sh"], input=install_script, check=True)
        os.environ["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + os.environ["PATH"]
        uv_bin = shutil.which("uv")

    if not uv_bin:
        raise RuntimeError("Klarte ikke å finne eller installere 'uv'.")

    cmd = [uv_bin, "run", "--python", "3.12", "--script", str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.call(cmd))

_self_bootstrap_if_needed()
'''


def generate_single_file_content() -> str:
    """Assemble and AST-validate the single-file distribution content in memory."""
    tpl_app = (SRC_DIR / "templates" / "app.html").read_text(encoding="utf-8")
    tpl_transcript = (SRC_DIR / "templates" / "transcript.html").read_text(encoding="utf-8")
    tpl_notes = (SRC_DIR / "templates" / "notes.html").read_text(encoding="utf-8")

    core_code = (SRC_DIR / "core.py").read_text(encoding="utf-8")
    gui_code = (SRC_DIR / "gui.py").read_text(encoding="utf-8")

    core_clean = re.sub(r"from __future__ import annotations\s*", "", core_code)
    core_clean = re.sub(r"from mote\.template_loader import load_template\s*", "", core_clean)
    core_clean = re.sub(r"from mote\..*?\n", "", core_clean)

    gui_clean = re.sub(r"from __future__ import annotations\s*", "", gui_code)
    gui_clean = re.sub(
        r"SRC_DIR = Path\(__file__\)\.resolve\(\)\.parent\.parent\nif str\(SRC_DIR\) not in sys\.path:\n\s+sys\.path\.insert\(0, str\(SRC_DIR\)\)\n",
        "",
        gui_clean,
    )
    gui_clean = re.sub(r"from mote\.core import \([\s\S]*?\)\n", "", gui_clean)
    gui_clean = re.sub(r"from mote\..*?\n", "", gui_clean)

    bundle_parts = [
        POLYGLOT_HEADER,
        BOOTSTRAP_CODE,
        f"""
# --- EMBEDDED TEMPLATES ---
_EMBEDDED_TEMPLATES: dict[str, str] = {{
    "app.html": {tpl_app!r},
    "transcript.html": {tpl_transcript!r},
    "notes.html": {tpl_notes!r},
}}


def load_template(name: str, fallback: str = "") -> str:
    tpl_file = Path(__file__).resolve().parent / "templates" / name
    if tpl_file.is_file():
        try:
            return tpl_file.read_text(encoding="utf-8")
        except Exception:
            pass
    return _EMBEDDED_TEMPLATES.get(name, fallback)
""",
        "\n# --- CORE ENGINE ---\n",
        core_clean,
        "\n# --- DESKTOP GUI & ENGINE ---\n",
        gui_clean,
    ]

    out_content = "".join(bundle_parts)
    ast.parse(out_content, filename=str(DIST_FILE))
    compile(out_content, str(DIST_FILE), "exec")
    return out_content


def build() -> int:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    print(f"==> Bygger universell enkeltfil: {DIST_FILE}")
    out_content = generate_single_file_content()
    DIST_FILE.write_text(out_content, encoding="utf-8")
    DIST_FILE.chmod(0o755)
    www_rec_dir = ROOT_DIR / "www" / "rec"
    if www_rec_dir.is_dir():
        www_script = www_rec_dir / "steno.py"
        www_script.write_text(out_content, encoding="utf-8")
        www_script.chmod(0o755)
    print(f"==> Ferdig bygget og AST-verifisert! Størrelse: {DIST_FILE.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())

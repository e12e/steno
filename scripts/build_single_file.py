#!/usr/bin/env python3
"""Build a single-file, zero-dependency, cross-platform runnable distribution of Steno.

Produces dist/steno.py which can run directly on macOS (ARM64/x86_64),
Windows 11 (x86_64/ARM64), and Linux without manual dependency installation,
via an embedded uv polyglot self-bootstrapper.
"""

from __future__ import annotations

import ast
import re
import struct
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src" / "mote"
DIST_DIR = ROOT_DIR / "dist"
DIST_FILE = DIST_DIR / "steno.py"
DIST_COM_FILE = DIST_DIR / "steno.com"

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


def generate_cosmopolitan_ape_binary(py_content: str | None = None) -> bytes:
    """Build a Cosmopolitan APE (MZqFpD) + 64-bit Windows PE32+ + POSIX /bin/sh single binary (steno.com)."""
    if py_content is None:
        py_content = generate_single_file_content()

    # 1. Bytes 0x00..0x3B (60 bytes): Cosmopolitan APE MZqFpD magic with newline on line 1 (no NUL on line 1)
    dos_prefix = b"MZqFpD=''\n# Cosmopolitan APE Polyglot Header for Steno     #"
    assert len(dos_prefix) == 0x3C

    # 2. Bytes 0x3C..0x3F (4 bytes): e_lfanew = 0x0800 (2048) inside the line-2 # comment
    e_lfanew = 0x0800
    dos_e_lfanew = struct.pack("<I", e_lfanew)

    # 3. Bytes 0x40..0x7FF: POSIX /bin/sh self-extractor & runner
    posix_boot = (
        b"\n"
        b'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.local/share/mise/shims:/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
        b'STENO_APP_DIR="${STENO_INSTALL_DIR:-$HOME/.local/share/steno}"\n'
        b'mkdir -p "$STENO_APP_DIR"\n'
        b'STENO_CACHE_PY="$STENO_APP_DIR/steno.py"\n'
        b'sed -n "/^# === STENO_PAYLOAD_BEGIN ===$/,\\$p" "$0" > "$STENO_CACHE_PY.tmp" && mv "$STENO_CACHE_PY.tmp" "$STENO_CACHE_PY"\n'
        b'chmod 0755 "$STENO_CACHE_PY"\n'
        b'exec /bin/sh "$STENO_CACHE_PY" "$@"\n'
    )
    header_part = dos_prefix + dos_e_lfanew + posix_boot
    assert len(header_part) <= e_lfanew, f"POSIX header too long: {len(header_part)}"
    header_padded = header_part.ljust(e_lfanew, b" ")

    # 4. Bytes 0x800..0xFFF: 64-bit Windows PE32+ (x86_64) Header + Section Table
    pe_sig = b"PE\x00\x00"
    coff_hdr = struct.pack(
        "<HHIIIHH",
        0x8664,  # Machine: IMAGE_FILE_MACHINE_AMD64
        1,       # NumberOfSections: 1 (.text)
        0,       # TimeDateStamp
        0,       # PointerToSymbolTable
        0,       # NumberOfSymbols
        240,     # SizeOfOptionalHeader (PE32+)
        0x0022,  # Characteristics: EXECUTABLE_IMAGE | LARGE_ADDRESS_AWARE
    )
    opt_hdr_fixed = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        0x020B,          # Magic: PE32+ (64-bit)
        14, 0,           # LinkerVersion
        0x0200,          # SizeOfCode
        0,               # SizeOfInitializedData
        0,               # SizeOfUninitializedData
        0x1000,          # AddressOfEntryPoint (RVA)
        0x1000,          # BaseOfCode (RVA)
        0x0000000140000000,  # ImageBase
        0x1000,          # SectionAlignment
        0x0200,          # FileAlignment
        6, 0,            # OS Version
        0, 0,            # Image Version
        6, 0,            # Subsystem Version
        0,               # Win32VersionValue
        0x2000,          # SizeOfImage
        0x1000,          # SizeOfHeaders
        0,               # CheckSum
        3,               # Subsystem: WINDOWS_CUI
        0,               # DllCharacteristics
        0x100000,        # SizeOfStackReserve
        0x1000,          # SizeOfStackCommit
        0x100000,        # SizeOfHeapReserve
        0x1000,          # SizeOfHeapCommit
        0,               # LoaderFlags
        16,              # NumberOfRvaAndSizes
    )
    data_dirs = b"\x00" * (16 * 8)
    opt_hdr = opt_hdr_fixed + data_dirs
    assert len(opt_hdr) == 240

    sec_hdr = struct.pack(
        "<8sIIIIIIHHI",
        b".text\x00\x00\x00",
        0x0200,      # VirtualSize
        0x1000,      # VirtualAddress
        0x0200,      # SizeOfRawData
        0x1000,      # PointerToRawData (file offset 4096)
        0, 0, 0, 0,
        0x60000020,  # Characteristics: CODE | EXECUTE | READ
    )
    pe_headers_block = (pe_sig + coff_hdr + opt_hdr + sec_hdr).ljust(0x0800, b"\x00")
    # Minimal x86_64 return stub (xor eax, eax; ret) at file offset 0x1000..0x11FF
    code_section = (b"\x31\xc0\xc3").ljust(0x0200, b"\x90")

    payload_marker = b"\n#!/bin/sh\n# === STENO_PAYLOAD_BEGIN ===\n"
    payload_bytes = py_content.encode("utf-8")
    return header_padded + pe_headers_block + code_section + payload_marker + payload_bytes


def build() -> int:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    print(f"==> Bygger universell enkeltfil: {DIST_FILE}")
    out_content = generate_single_file_content()
    DIST_FILE.write_text(out_content, encoding="utf-8")
    DIST_FILE.chmod(0o755)

    com_bytes = generate_cosmopolitan_ape_binary(out_content)
    DIST_COM_FILE.write_bytes(com_bytes)
    DIST_COM_FILE.chmod(0o755)

    www_rec_dir = ROOT_DIR / "www" / "rec"
    if www_rec_dir.is_dir():
        www_script = www_rec_dir / "steno.py"
        www_script.write_text(out_content, encoding="utf-8")
        www_script.chmod(0o755)
        www_com = www_rec_dir / "steno.com"
        www_com.write_bytes(com_bytes)
        www_com.chmod(0o755)
    print(
        f"==> Ferdig bygget og AST-verifisert! "
        f"steno.py: {DIST_FILE.stat().st_size // 1024} KB, "
        f"steno.com: {DIST_COM_FILE.stat().st_size // 1024} KB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(build())

"""Hardware detection, model recommendations, runtime provisioning, and install/uninstall lifecycle."""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mote.constants import (
    HF_CACHE_DIR,
    HF_TOKEN_FILE,
    LOCAL_BIN_DIR,
    MODEL_PROFILES,
    OLLAMA_TAGS_URL,
    build_runtime_env,
    detect_cpu_arch,
    is_norwegian_language,
    is_termux,
    normalize_audio_language,
    resolve_posix_shell,
)


def _resolve_steno_install_paths(
    target_root: Path | None = None,
    platform_str: str | None = None,
) -> dict[str, Path]:
    """Return canonical installation, cache, launcher, and desktop shortcut paths for Steno."""
    plat = platform_str if platform_str is not None else sys.platform
    root = target_root.resolve() if target_root is not None else Path.home()

    if plat.startswith("win") and target_root is None:
        local_appdata = Path(os.environ.get("LOCALAPPDATA", str(root / "AppData" / "Local")))
        roaming_appdata = Path(os.environ.get("APPDATA", str(root / "AppData" / "Roaming")))
        app_dir = local_appdata / "Steno"
        mote_cache_dir = root / ".local" / "share" / "mote_transcribe"
        mote_xdg_cache_dir = root / ".cache" / "mote_transcribe"
        cache_dir = local_appdata / "Steno" / "cache"
        uv_whisperx_dir = roaming_appdata / "uv" / "tools" / "whisperx"
        hf_hub_dir = root / ".cache" / "huggingface" / "hub"
        bin_dir = root / ".local" / "bin"
        launcher_path = bin_dir / "steno.cmd"
        desktop_entry = roaming_appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Steno.cmd"
    elif (plat == "linux-android" or (target_root is None and is_termux())) and target_root is None:
        prefix = Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
        app_dir = root / ".local" / "share" / "steno"
        mote_cache_dir = root / ".local" / "share" / "mote_transcribe"
        mote_xdg_cache_dir = root / ".cache" / "mote_transcribe"
        cache_dir = root / ".cache" / "steno"
        uv_whisperx_dir = root / ".local" / "share" / "uv" / "tools" / "whisperx"
        hf_hub_dir = root / ".cache" / "huggingface" / "hub"
        bin_dir = (prefix / "bin") if (prefix / "bin").is_dir() else (root / ".local" / "bin")
        launcher_path = bin_dir / "steno"
        desktop_entry = root / ".shortcuts" / "Steno"
    else:
        app_dir = root / ".local" / "share" / "steno"
        mote_cache_dir = root / ".local" / "share" / "mote_transcribe"
        mote_xdg_cache_dir = root / ".cache" / "mote_transcribe"
        cache_dir = root / ".cache" / "steno"
        uv_whisperx_dir = root / ".local" / "share" / "uv" / "tools" / "whisperx"
        hf_hub_dir = root / ".cache" / "huggingface" / "hub"
        bin_dir = root / ".local" / "bin"
        launcher_path = bin_dir / ("steno.cmd" if plat.startswith("win") else "steno")
        if plat == "darwin":
            desktop_entry = root / "Applications" / "Steno.app"
        elif plat.startswith("win"):
            desktop_entry = root / "StartMenu" / "Steno.cmd"
        elif plat == "linux-android":
            desktop_entry = root / ".shortcuts" / "Steno"
        else:
            desktop_entry = root / ".local" / "share" / "applications" / "steno.desktop"

    return {
        "root": root,
        "app_dir": app_dir,
        "mote_cache_dir": mote_cache_dir,
        "mote_xdg_cache_dir": mote_xdg_cache_dir,
        "cache_dir": cache_dir,
        "uv_whisperx_dir": uv_whisperx_dir,
        "hf_hub_dir": hf_hub_dir,
        "bin_dir": bin_dir,
        "launcher_path": launcher_path,
        "desktop_entry": desktop_entry,
    }


def _resolve_source_anchor() -> Path:
    core_mod = sys.modules.get("mote.core")
    anchor = getattr(core_mod, "__file__", None) if core_mod is not None else None
    if anchor:
        return Path(str(anchor)).resolve()
    return Path(__file__).resolve()


def install_steno_app(
    source_script: Path | None = None,
    target_root: Path | None = None,
    platform_str: str | None = None,
) -> dict[str, Any]:
    """Install Steno into the user's local app directory with CLI launcher and desktop shortcut.

    >>> with tempfile.TemporaryDirectory() as d:
    ...     res = install_steno_app(target_root=Path(d), platform_str="linux")
    ...     res["ok"] and Path(res["launcher_path"]).is_file() and Path(res["desktop_entry"]).is_file()
    True
    >>> with tempfile.TemporaryDirectory() as d:
    ...     res_tmx = install_steno_app(target_root=Path(d), platform_str="linux-android")
    ...     res_tmx["ok"] and Path(res_tmx["launcher_path"]).is_file() and Path(res_tmx["desktop_entry"]).is_file()
    True
    """
    plat = platform_str if platform_str is not None else sys.platform
    paths = _resolve_steno_install_paths(target_root=target_root, platform_str=plat)
    app_dir = paths["app_dir"]
    bin_dir = paths["bin_dir"]
    launcher_path = paths["launcher_path"]
    desktop_entry = paths["desktop_entry"]

    app_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    target_py = app_dir / "steno.py"
    this_file = _resolve_source_anchor()
    repo_root = this_file.parent.parent.parent
    dist_steno = repo_root / "dist" / "steno.py"
    build_script = repo_root / "scripts" / "build_single_file.py"
    explicit_src = Path(source_script) if source_script is not None else None
    if explicit_src is not None and explicit_src.is_file():
        if explicit_src.resolve() != target_py.resolve():
            shutil.copyfile(explicit_src, target_py)
    elif (repo_root / "src" / "mote" / "core.py").is_file():
        if dist_steno.is_file():
            if dist_steno.resolve() != target_py.resolve():
                shutil.copyfile(dist_steno, target_py)
        elif build_script.is_file():
            spec = importlib.util.spec_from_file_location("_steno_builder", build_script)
            if spec is not None and spec.loader is not None:
                builder_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(builder_mod)
                target_py.write_text(builder_mod.generate_single_file_content(), encoding="utf-8")
            else:
                shutil.copyfile(this_file, target_py)
        else:
            shutil.copyfile(this_file, target_py)
    else:
        if this_file.resolve() != target_py.resolve():
            shutil.copyfile(this_file, target_py)
    target_py.chmod(0o755)

    if plat.startswith("win"):
        cmd_body = (
            "@echo off\r\n"
            'set "PATH=%USERPROFILE%\\.local\\bin;%LOCALAPPDATA%\\uv\\bin;%LOCALAPPDATA%\\Microsoft\\WinGet\\Links;%PATH%"\r\n'
            f'uv run --python 3.12 --script "{target_py}" %*\r\n'
        )
        launcher_path.write_text(cmd_body, encoding="utf-8")
        desktop_entry.parent.mkdir(parents=True, exist_ok=True)
        desktop_entry.write_text(cmd_body, encoding="utf-8")
    else:
        sh_bin = resolve_posix_shell() if plat == "linux-android" or is_termux() else "/bin/sh"
        sh_body = (
            f"#!{sh_bin}\n"
            'SH_BIN="/bin/sh"\n'
            'if [ -n "${PREFIX:-}" ] && [ -x "$PREFIX/bin/sh" ]; then SH_BIN="$PREFIX/bin/sh"\n'
            'elif [ -x "/data/data/com.termux/files/usr/bin/sh" ]; then SH_BIN="/data/data/com.termux/files/usr/bin/sh"\n'
            'elif [ ! -x "$SH_BIN" ] && command -v sh >/dev/null 2>&1; then SH_BIN="$(command -v sh)"; fi\n'
            f'exec "$SH_BIN" "{target_py}" "$@"\n'
        )
        launcher_path.write_text(sh_body, encoding="utf-8")
        launcher_path.chmod(0o755)

        if plat == "darwin":
            macos_dir = desktop_entry / "Contents" / "MacOS"
            macos_dir.mkdir(parents=True, exist_ok=True)
            info_plist = desktop_entry / "Contents" / "Info.plist"
            info_plist.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                "  <key>CFBundleName</key><string>Steno</string>\n"
                "  <key>CFBundleDisplayName</key><string>Steno</string>\n"
                "  <key>CFBundleIdentifier</key><string>net.hypertekst.steno</string>\n"
                "  <key>CFBundleVersion</key><string>0.3.1</string>\n"
                "  <key>CFBundlePackageType</key><string>APPL</string>\n"
                "  <key>CFBundleExecutable</key><string>Steno</string>\n"
                "</dict></plist>\n",
                encoding="utf-8",
            )
            app_exec = macos_dir / "Steno"
            app_exec.write_text(sh_body, encoding="utf-8")
            app_exec.chmod(0o755)
        elif plat == "linux-android":
            desktop_entry.parent.mkdir(parents=True, exist_ok=True)
            desktop_entry.write_text(sh_body, encoding="utf-8")
            desktop_entry.chmod(0o755)
        else:
            desktop_entry.parent.mkdir(parents=True, exist_ok=True)
            desktop_entry.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Steno\n"
                "Comment=Steno - a modern annalist for your meetings - an automaton stenographer\n"
                f'Exec={sh_bin} "{target_py}"\n'
                "Terminal=false\n"
                "Categories=AudioVideo;Audio;Office;Utility;\n",
                encoding="utf-8",
            )
            desktop_entry.chmod(0o755)

    return {
        "ok": True,
        "app_dir": str(app_dir),
        "installed_script": str(target_py),
        "launcher_path": str(launcher_path),
        "desktop_entry": str(desktop_entry),
        "message_en": f"Installed Steno to {app_dir} (launcher: {launcher_path}).",
        "message_no": f"Installerte Steno i {app_dir} (snarvei: {launcher_path}).",
    }


def uninstall_steno_app(
    target_root: Path | None = None,
    platform_str: str | None = None,
) -> dict[str, Any]:
    """Remove Steno cached runtime directories, model caches, CLI launchers, and desktop shortcuts.

    Never deletes user meeting recordings, transcripts, or .mote_checkpoints folders.

    >>> with tempfile.TemporaryDirectory() as d:
    ...     _ = install_steno_app(target_root=Path(d), platform_str="linux")
    ...     out = uninstall_steno_app(target_root=Path(d), platform_str="linux")
    ...     out["ok"] and len(out["removed_paths"]) >= 2
    True
    """
    plat = platform_str if platform_str is not None else sys.platform
    paths = _resolve_steno_install_paths(target_root=target_root, platform_str=plat)
    removed: list[str] = []

    for dir_key in ("app_dir", "mote_cache_dir", "mote_xdg_cache_dir", "cache_dir", "uv_whisperx_dir"):
        dpath = paths[dir_key]
        if dpath.exists():
            shutil.rmtree(dpath, ignore_errors=True)
            removed.append(str(dpath))

    hf_hub_dir = paths["hf_hub_dir"]
    if hf_hub_dir.is_dir():
        steno_hf_prefixes = (
            "models--NbAiLab--nb-whisper-",
            "models--NbAiLab--nb-wav2vec2-",
            "models--pyannote--",
            "models--Systran--faster-whisper-",
            "models--mobiuslabsgmbh--",
            "models--UlrikKoren--",
        )
        for child in sorted(hf_hub_dir.iterdir()):
            if child.name.startswith(steno_hf_prefixes) and child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                removed.append(str(child))

    desktop_entry = paths["desktop_entry"]
    if desktop_entry.is_dir():
        shutil.rmtree(desktop_entry, ignore_errors=True)
        removed.append(str(desktop_entry))
    elif desktop_entry.exists():
        try:
            desktop_entry.unlink()
            removed.append(str(desktop_entry))
        except OSError:
            pass

    bin_dir = paths["bin_dir"]
    for launcher_name in ("steno", "steno.com", "steno.cmd", "steno.py", "whisperx", "whisperx.exe"):
        cand = bin_dir / launcher_name
        if cand.exists() or cand.is_symlink():
            try:
                cand.unlink()
                removed.append(str(cand))
            except OSError:
                pass

    if target_root is None:
        tmp_root = Path(tempfile.gettempdir())
        mictest_tmp = tmp_root / "steno_mictest_latest.wav"
        if mictest_tmp.is_file():
            try:
                mictest_tmp.unlink()
                removed.append(str(mictest_tmp))
            except OSError:
                pass
        uploads_tmp = tmp_root / "steno_browser_uploads"
        if uploads_tmp.is_dir():
            shutil.rmtree(uploads_tmp, ignore_errors=True)
            removed.append(str(uploads_tmp))

    return {
        "ok": True,
        "removed_paths": removed,
        "message_en": (
            f"Uninstalled Steno and removed {len(removed)} cached location(s). "
            "Your meeting audio and transcript files were not touched."
        ),
        "message_no": (
            f"Avinstallerte Steno og fjernet {len(removed)} mellomlagrede plassering(er). "
            "Dine møteopptak og referater ble ikke rørt."
        ),
    }


def recommend_models_for_language(
    language: str = "no",
    ram_gb: float = 16.0,
    profile: str = "recommended",
    prefs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Recommend (whisper_model, ollama_model) for the given audio language, RAM size, and profile.

    >>> rec_no = recommend_models_for_language("no", ram_gb=16.0, profile="recommended")
    >>> (rec_no["whisper_model"], rec_no["ollama_model"])
    ('NbAiLab/nb-whisper-small', 'gemma4:e4b')
    >>> rec_en = recommend_models_for_language("en", ram_gb=16.0, profile="recommended")
    >>> (rec_en["whisper_model"], rec_en["ollama_model"])
    ('large-v3-turbo', 'gemma4:e4b')
    >>> rec_en_8g = recommend_models_for_language("en", ram_gb=8.0, profile="light")
    >>> (rec_en_8g["whisper_model"], rec_en_8g["ollama_model"])
    ('small', 'llama3.2:latest')
    """
    norm_lang = normalize_audio_language(language)
    eff_prof = (
        profile
        if profile in ("recommended", "light", "standard", "fast", "quality", "heavy", "custom")
        else "recommended"
    )
    if ram_gb < 11.5 and eff_prof == "recommended":
        eff_prof = "light"

    if prefs and isinstance(prefs.get("language_models"), Mapping):
        saved = prefs["language_models"].get(norm_lang)
        if isinstance(saved, Mapping) and saved.get("whisper_model") and saved.get("ollama_model"):
            if profile == "custom" or saved.get("profile") == eff_prof:
                sw = str(saved["whisper_model"])
                so = str(saved["ollama_model"])
                return {
                    "language": norm_lang,
                    "profile": str(saved.get("profile", eff_prof)),
                    "whisper_model": sw,
                    "ollama_model": so,
                    "default_whisper": sw,
                    "default_ollama": so,
                    "whisper_choices": (
                        [
                            "NbAiLab/nb-whisper-large",
                            "NbAiLab/nb-whisper-medium",
                            "NbAiLab/nb-whisper-small",
                            "UlrikKoren/nb-whisper-large-distil-turbo-beta-ct2",
                        ]
                        if is_norwegian_language(norm_lang)
                        else ["large-v3-turbo", "large-v3", "medium", "small", "base"]
                    ),
                }

    if is_norwegian_language(norm_lang):
        choices = [
            "NbAiLab/nb-whisper-large",
            "NbAiLab/nb-whisper-medium",
            "NbAiLab/nb-whisper-small",
            "UlrikKoren/nb-whisper-large-distil-turbo-beta-ct2",
        ]
        if eff_prof == "light":
            w_mod, o_mod = "NbAiLab/nb-whisper-small", "llama3.2:latest"
        elif eff_prof == "fast":
            w_mod, o_mod = "UlrikKoren/nb-whisper-large-distil-turbo-beta-ct2", "gemma4:e4b"
        elif eff_prof in ("quality", "heavy"):
            w_mod, o_mod = "NbAiLab/nb-whisper-large", "gemma4:e4b"
        elif eff_prof == "standard":
            w_mod, o_mod = "NbAiLab/nb-whisper-medium", "gemma4:e4b"
        else:
            w_mod, o_mod = "NbAiLab/nb-whisper-small", "gemma4:e4b"
    else:
        choices = ["large-v3-turbo", "large-v3", "medium", "small", "base"]
        if eff_prof == "light":
            w_mod, o_mod = "small", "llama3.2:latest"
        elif eff_prof in ("quality", "heavy"):
            w_mod, o_mod = "large-v3", "gemma4:e4b"
        elif eff_prof == "standard":
            w_mod, o_mod = ("large-v3-turbo" if ram_gb >= 11.5 else "small"), "gemma4:e4b"
        else:
            w_mod, o_mod = ("large-v3-turbo" if ram_gb >= 11.5 else "small"), "gemma4:e4b"

    return {
        "language": norm_lang,
        "profile": eff_prof,
        "whisper_model": w_mod,
        "ollama_model": o_mod,
        "default_whisper": w_mod,
        "default_ollama": o_mod,
        "whisper_choices": choices,
    }


def detect_hardware_resources(target_dir: Path | None = None, language: str = "no") -> dict[str, Any]:
    """Detect local RAM, CPU brand/cores, GPU VRAM, free disk space, and recommended model profile.

    >>> hw = detect_hardware_resources()
    >>> hw["ram_gb"] > 0 and hw["cpu_cores"] >= 1 and hw["recommended_profile"] in MODEL_PROFILES
    True
    """
    ram_bytes = 0
    cpu_brand = platform.processor() or platform.machine() or "Ukjent CPU"
    cpu_cores = os.cpu_count() or 1
    arch = detect_cpu_arch()
    is_apple_silicon = sys.platform == "darwin" and arch == "arm64"
    termux_active = is_termux()

    if sys.platform == "darwin":
        try:
            out_mem = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"], text=True, stderr=subprocess.DEVNULL
            ).strip()
            ram_bytes = int(out_mem)
        except Exception:
            pass
        try:
            out_cpu = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            if out_cpu:
                cpu_brand = out_cpu
        except Exception:
            pass
    elif os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat_ex = MEMORYSTATUSEX()
            stat_ex.dwLength = ctypes.sizeof(stat_ex)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat_ex))  # type: ignore[attr-defined]
            ram_bytes = int(stat_ex.ullTotalPhys)
        except Exception:
            pass
    else:
        try:
            meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
            m_mem = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
            if m_mem:
                ram_bytes = int(m_mem.group(1)) * 1024
        except Exception:
            pass
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
            m_cpu = (
                re.search(r"model name\s*:\s*(.+)", cpuinfo, flags=re.IGNORECASE)
                or re.search(r"Hardware\s*:\s*(.+)", cpuinfo, flags=re.IGNORECASE)
                or re.search(r"Processor\s*:\s*(.+)", cpuinfo, flags=re.IGNORECASE)
            )
            if m_cpu:
                cpu_brand = m_cpu.group(1).strip()
        except Exception:
            pass
        if termux_active and shutil.which("getprop"):
            try:
                soc = subprocess.check_output(
                    ["getprop", "ro.soc.model"], text=True, stderr=subprocess.DEVNULL
                ).strip()
                if soc:
                    cpu_brand = soc
            except Exception:
                pass

    if ram_bytes <= 0:
        ram_bytes = 8 * (1024**3)
    ram_gb = round(ram_bytes / float(1024**3), 1)

    gpu_vram_gb = 0.0
    gpu_name = ""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            nv_out = subprocess.check_output(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).strip()
            if nv_out:
                first_line = nv_out.splitlines()[0]
                parts = [p.strip() for p in first_line.split(",")]
                if len(parts) >= 2:
                    gpu_name = parts[0]
                    gpu_vram_gb = round(float(parts[1]) / 1024.0, 1)
        except Exception:
            pass

    check_path = target_dir if (target_dir and target_dir.exists()) else Path.home()
    try:
        disk_free_gb = round(shutil.disk_usage(check_path).free / float(1024**3), 1)
    except Exception:
        disk_free_gb = 20.0

    warnings: list[str] = []
    if ram_gb < 7.5:
        warnings.append(
            f"KRITISK: Maskinen har kun {ram_gb:.1f} GB RAM (minimum er 8 GB RAM). "
            "Lokale AI-modeller vil sannsynligvis gå tom for minne eller fryse maskinen."
        )
    elif ram_gb < 12.0:
        warnings.append(
            f"Begrenset minne ({ram_gb:.0f} GB RAM): Bruk profilen «Lettvekt» (nb-whisper-small + llama3.2:latest) "
            "og lukk nettlesere/tunge programmer under kjøring."
        )

    if disk_free_gb < 6.0:
        warnings.append(
            f"KRITISK: Kun {disk_free_gb:.1f} GB ledig diskplass. Du bør ha minst 10–15 GB ledig for AI-modeller og lydfiler."
        )
    elif disk_free_gb < 14.0:
        warnings.append(
            f"Lite ledig diskplass ({disk_free_gb:.1f} GB): Velg lettvektsmodeller hvis modellene ikke allerede er lastet ned."
        )

    if ram_gb < 11.5:
        rec_profile_key = "light"
        tier = "insufficient" if ram_gb < 7.5 else "light"
    else:
        rec_profile_key = "recommended"
        tier = "high" if (ram_gb >= 22.0 or gpu_vram_gb >= 10.0) else "standard"

    rec_prof = MODEL_PROFILES[rec_profile_key]
    lang_rec = recommend_models_for_language(language, ram_gb=ram_gb, profile=rec_profile_key)
    hw_summary = f"{cpu_brand} ({cpu_cores} kjerner), {ram_gb:.0f} GB RAM"
    if gpu_name:
        hw_summary += f", GPU: {gpu_name} ({gpu_vram_gb:.0f} GB VRAM)"
    hw_summary += f", {disk_free_gb:.0f} GB ledig disk"

    return {
        "ram_gb": ram_gb,
        "cpu_brand": cpu_brand,
        "cpu_cores": cpu_cores,
        "arch": arch,
        "is_apple_silicon": is_apple_silicon,
        "is_termux": termux_active,
        "gpu_name": gpu_name,
        "gpu_vram_gb": gpu_vram_gb,
        "disk_free_gb": disk_free_gb,
        "tier": tier,
        "hardware_summary": hw_summary,
        "recommended_profile": rec_profile_key,
        "recommended_whisper": lang_rec["whisper_model"],
        "recommended_ollama": lang_rec["ollama_model"],
        "recommended_label": rec_prof["label"],
        "profiles": MODEL_PROFILES,
        "warnings": warnings,
    }


def evaluate_model_resource_fit(
    whisper_model: str,
    ollama_model: str,
    hw: dict[str, Any] | None = None,
    language: str = "no",
) -> dict[str, Any]:
    """Estimate RAM and disk needs for (whisper_model, ollama_model) and warn if hardware is insufficient.

    >>> hw_8gb = {"ram_gb": 8.0, "disk_free_gb": 50.0, "hardware_summary": "Test 8GB", "recommended_whisper": "NbAiLab/nb-whisper-small", "recommended_ollama": "qwen3:4b"}
    >>> fit_ok = evaluate_model_resource_fit("NbAiLab/nb-whisper-small", "qwen3:4b", hw_8gb)
    >>> fit_ok["level"]
    'ok'
    >>> fit_bad = evaluate_model_resource_fit("NbAiLab/nb-whisper-large", "gemma4:26b", hw_8gb)
    >>> fit_bad["level"]
    'critical'
    """
    if hw is None:
        hw = detect_hardware_resources(language=language)

    w_low = whisper_model.lower()
    if any(k in w_low for k in ("tiny", "base", "small")):
        est_asr_ram = 1.2
        est_align_ram = 1.5
        est_asr_disk = 2.0
    elif any(k in w_low for k in ("distil", "turbo", "medium")):
        est_asr_ram = 2.2
        est_align_ram = 4.6
        est_asr_disk = 5.2
    else:
        est_asr_ram = 3.4
        est_align_ram = 4.8
        est_asr_disk = 7.0

    m_low = ollama_model.lower()
    if any(k in m_low for k in ("26b", "27b", "30b", "32b", "devstral", "24b")):
        est_llm_ram = 18.0
        est_llm_disk = 15.0
    elif any(k in m_low for k in ("11b", "12b", "14b", "normistral")):
        est_llm_ram = 9.5
        est_llm_disk = 7.5
    elif any(k in m_low for k in ("e4b", "7b", "8b", "gemma4:latest")):
        est_llm_ram = 6.5
        est_llm_disk = 5.5
    else:
        est_llm_ram = 4.0
        est_llm_disk = 3.0

    est_peak_ram = round(max(est_asr_ram, est_align_ram, est_llm_ram), 1)
    est_total_disk = round(est_asr_disk + est_llm_disk, 1)
    ram_gb = float(hw.get("ram_gb", 16.0))
    disk_free_gb = float(hw.get("disk_free_gb", 20.0))
    lang_rec = recommend_models_for_language(language, ram_gb=ram_gb)
    rec_w = str(hw.get("recommended_whisper") or lang_rec["whisper_model"])
    rec_o = str(hw.get("recommended_ollama") or lang_rec["ollama_model"])

    issues: list[str] = []
    level = "ok"

    norm_lang = normalize_audio_language(language)
    if not is_norwegian_language(norm_lang) and "nb-whisper" in w_low:
        level = "warning"
        rec_w = lang_rec["whisper_model"]
        issues.append(
            f"Merk: «{whisper_model}» er finjustert kun for norsk. For talespråk «{norm_lang}» anbefales flerspråklig «{rec_w}»."
        )

    if ram_gb < 7.5:
        level = "critical"
        issues.append(
            f"Maskinen har kun {ram_gb:.1f} GB RAM (minimum er 8 GB RAM). Kjøring vil sannsynligvis feile."
        )
    elif est_peak_ram > ram_gb * 0.88:
        level = "critical"
        issues.append(
            f"Valgt modellkombinasjon krever ~{est_peak_ram:.1f} GB RAM på topp, men maskinen har kun {ram_gb:.0f} GB RAM. "
            f"Klikk «Bruk anbefalt» ({rec_w} + {rec_o}) for å unngå minnekrasj."
        )
    elif est_peak_ram > ram_gb * 0.72:
        if level == "ok":
            level = "warning"
        issues.append(
            f"Valgte modeller bruker ~{est_peak_ram:.1f} GB av {ram_gb:.0f} GB RAM. Lukk nettleser og tunge apper før start, "
            "eller velg en lettere profil."
        )

    if disk_free_gb < 5.0:
        level = "critical"
        issues.append(f"Kun {disk_free_gb:.1f} GB ledig diskplass (kritisk lavt).")

    if issues:
        msg = " | ".join(issues)
    else:
        msg = (
            f"Maskinvare OK: {hw.get('hardware_summary')} — "
            f"Valgte modeller bruker maks ~{est_peak_ram:.1f} GB RAM sekvensielt."
        )

    return {
        "ok": level != "critical",
        "level": level,
        "status": level,
        "est_peak_ram_gb": est_peak_ram,
        "est_disk_gb": est_total_disk,
        "ram_gb": ram_gb,
        "disk_free_gb": disk_free_gb,
        "message": msg,
        "recommended_whisper": rec_w,
        "recommended_ollama": rec_o,
    }


def _download_file(url: str, dest: Path, timeout: int = 120, chunk_size: int = 65536) -> None:
    """Download a remote URL to disk in streaming chunks with an explicit socket timeout."""
    with urllib.request.urlopen(url, timeout=timeout) as resp, dest.open("wb") as out_fp:
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            out_fp.write(chunk)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract a tar archive safely using filter='data' with path-validation fallback."""
    dest_resolved = dest.resolve()
    try:
        tf.extractall(dest, filter="data")
        return
    except TypeError:
        pass
    for member in tf.getmembers():
        if member.issym() or member.islnk() or member.isdev():
            raise ValueError(f"Unsafe tar member type: {member.name}")
        member_path = (dest / member.name).resolve()
        if member_path != dest_resolved and dest_resolved not in member_path.parents:
            raise ValueError(f"Unsafe tar member path: {member.name}")
    tf.extractall(dest)


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    """Validate zip member paths against absolute paths and '..' traversal before extracting."""
    dest_resolved = dest.resolve()
    for info in zf.infolist():
        name = info.filename
        if name.startswith(("/", "\\")) or Path(name).is_absolute():
            raise ValueError(f"Unsafe zip member path (absolute): {name}")
        parts = Path(name.replace("\\", "/")).parts
        if ".." in parts:
            raise ValueError(f"Unsafe zip member path (traversal): {name}")
        target_path = (dest / name).resolve()
        if target_path != dest_resolved and dest_resolved not in target_path.parents:
            raise ValueError(f"Unsafe zip member path (escapes destination): {name}")
    zf.extractall(dest)


def ensure_ffmpeg(env: dict[str, str], log_fn: Callable[[str], None]) -> str:
    found = shutil.which("ffmpeg", path=env["PATH"])
    if found:
        return found
    log_fn("==> Laster ned innebygd ffmpeg...")
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths(weak=True)
    except Exception:
        pass
    env["PATH"] = os.environ.get("PATH", "") + os.pathsep + env["PATH"]
    found = shutil.which("ffmpeg", path=env["PATH"])
    if found:
        return found

    if is_termux(env):
        pkg_bin = shutil.which("pkg", path=env["PATH"]) or shutil.which("apt-get", path=env["PATH"])
        if pkg_bin:
            log_fn("==> Installerer ffmpeg via Termux pakkebehandler...")
            subprocess.run([pkg_bin, "install", "-y", "ffmpeg"], env=env, check=False)
            found = shutil.which("ffmpeg", path=env["PATH"])
            if found:
                return found

    if sys.platform.startswith("linux") and not is_termux(env):
        LOCAL_BIN_DIR.mkdir(parents=True, exist_ok=True)
        arch = detect_cpu_arch(env=env)
        jvs_arch = "arm64" if arch == "arm64" else "amd64"
        url = f"https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{jvs_arch}-static.tar.xz"
        try:
            with tempfile.TemporaryDirectory(prefix="ffmpeg_dl_") as tmp:
                tmp_path = Path(tmp)
                archive = tmp_path / "ffmpeg.tar.xz"
                _download_file(url, archive)
                with tarfile.open(archive, "r:xz") as tf:
                    _safe_extract_tar(tf, tmp_path)
                for cand in tmp_path.rglob("ffmpeg"):
                    if cand.is_file():
                        target = LOCAL_BIN_DIR / "ffmpeg"
                        shutil.copy2(cand, target)
                        target.chmod(target.stat().st_mode | stat.S_IEXEC)
                        return str(target)
        except Exception:
            pass

    if os.name == "nt":
        winget_bin = shutil.which("winget", path=env["PATH"])
        if winget_bin:
            subprocess.run(
                [
                    winget_bin,
                    "install",
                    "--id",
                    "Gyan.FFmpeg",
                    "-e",
                    "--silent",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ],
                env=env,
                check=False,
            )
            found = shutil.which("ffmpeg", path=env["PATH"])
            if found:
                return found

    raise RuntimeError("Fant ikke ffmpeg.")


def ensure_whisperx(
    env: dict[str, str],
    log_fn: Callable[[str], None],
    progress_fn: Callable[[int, str], None] | None = None,
) -> str:
    found = shutil.which("whisperx", path=env["PATH"])
    if found:
        return found
    if progress_fn:
        progress_fn(8, "Installerer WhisperX automatisk (kun første gang)...")

    if is_termux(env):
        uv_bin = shutil.which("uv", path=env["PATH"])
        log_fn("==> WhisperX mangler på Termux. Installerer med systemets Python...")
        if uv_bin:
            subprocess.run(
                [uv_bin, "tool", "install", "--python", sys.executable, "whisperx"],
                env=env,
                check=True,
            )
        else:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user", "whisperx"],
                env=env,
                check=True,
            )
    else:
        uv_bin = shutil.which("uv", path=env["PATH"])
        if not uv_bin:
            raise RuntimeError("Fant ikke 'uv' for automatisk installasjon av whisperx.")
        log_fn("==> WhisperX mangler. Installerer automatisk via 'uv tool install whisperx'...")
        proc = subprocess.run(
            [uv_bin, "tool", "install", "--python", "3.12", "whisperx"],
            env=env,
            check=False,
        )
        if proc.returncode != 0 and os.name == "nt" and detect_cpu_arch(env=env) == "arm64":
            log_fn("==> Prøver Windows x86_64 Python 3.12 (Prism-kompatible hjul) for WhisperX på ARM64...")
            subprocess.run(
                [uv_bin, "tool", "install", "--python", "cpython-3.12-windows-x86_64-none", "whisperx"],
                env=env,
                check=True,
            )
        elif proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args)

    found = shutil.which("whisperx", path=env["PATH"])
    if not found:
        cand = Path.home() / ".local" / "bin" / ("whisperx.exe" if os.name == "nt" else "whisperx")
        if cand.is_file():
            return str(cand)
        raise RuntimeError("WhisperX ble installert, men ble ikke funnet i PATH.")
    return found


def get_whisperx_python(whisperx_bin: str) -> str:
    wx_path = Path(whisperx_bin).resolve()
    for name in ("python.exe", "python3", "python"):
        cand = wx_path.parent / name
        if cand.is_file():
            return str(cand)
    try:
        first_line = Path(whisperx_bin).read_text(encoding="utf-8", errors="ignore").splitlines()[0]
        if first_line.startswith("#!"):
            shebang_bin = first_line[2:].strip().split()[0]
            if Path(shebang_bin).is_file():
                return shebang_bin
    except Exception:
        pass
    return sys.executable


def is_diarization_cached() -> bool:
    hub_dir = HF_CACHE_DIR / "hub"
    if not hub_dir.is_dir():
        return False
    return any(
        any(p.glob("snapshots/*/*"))
        for p in hub_dir.glob("models--pyannote--speaker-diarization*")
    )


def get_initial_hf_token(interactive_prompt: bool = False) -> str:
    if token := os.environ.get("HF_TOKEN", "").strip():
        return token
    if HF_TOKEN_FILE.is_file():
        try:
            if token := HF_TOKEN_FILE.read_text(encoding="utf-8").strip():
                return token
        except OSError:
            pass
    if interactive_prompt and not is_diarization_cached() and sys.stdin.isatty():
        token = getpass.getpass("Skriv inn Hugging Face Read-token (hf_...): ").strip()
        if token:
            save_hf_token(token)
            return token
    return ""


def save_hf_token(token: str) -> None:
    token = token.strip()
    if not token:
        return
    HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HF_TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        HF_TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def fetch_ollama_models() -> list[str] | None:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=1.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return [str(m.get("name", "")) for m in data.get("models", [])]
    except Exception:
        pass
    return None


def ensure_ollama_binary(
    env: dict[str, str],
    log_fn: Callable[[str], None],
    progress_fn: Callable[[int, str], None] | None = None,
) -> str:
    found = shutil.which("ollama", path=env["PATH"])
    if found:
        return found
    if sys.platform == "darwin" and Path("/Applications/Ollama.app/Contents/Resources/ollama").is_file():
        return "/Applications/Ollama.app/Contents/Resources/ollama"

    if is_termux(env):
        pkg_bin = shutil.which("pkg", path=env["PATH"]) or shutil.which("apt-get", path=env["PATH"])
        if pkg_bin:
            log_fn("==> Installerer innebygd Ollama via Termux pakkebehandler...")
            subprocess.run([pkg_bin, "install", "-y", "ollama"], env=env, check=False)
            found = shutil.which("ollama", path=env["PATH"])
            if found:
                return found

    LOCAL_BIN_DIR.mkdir(parents=True, exist_ok=True)
    if progress_fn:
        progress_fn(74, "Laster ned frittstående Ollama-motor...")
    log_fn("==> Ollama ble ikke funnet. Laster ned frittstående binærfil...")

    arch = detect_cpu_arch(env=env)

    with tempfile.TemporaryDirectory(prefix="ollama_dl_") as tmp:
        tmp_path = Path(tmp)
        if sys.platform == "darwin":
            url = "https://ollama.com/download/Ollama-darwin.zip"
            archive = tmp_path / "Ollama-darwin.zip"
            _download_file(url, archive)
            with zipfile.ZipFile(archive, "r") as zf:
                _safe_extract_zip(zf, tmp_path)
            src = tmp_path / "Ollama.app" / "Contents" / "Resources" / "ollama"
            target = LOCAL_BIN_DIR / "ollama"
            shutil.copy2(src, target)
            target.chmod(target.stat().st_mode | stat.S_IEXEC)
            return str(target)
        if sys.platform.startswith("linux"):
            url = f"https://ollama.com/download/ollama-linux-{arch}.tgz"
            archive = tmp_path / "ollama.tgz"
            _download_file(url, archive)
            with tarfile.open(archive, "r:gz") as tf:
                _safe_extract_tar(tf, tmp_path)
            src = tmp_path / "bin" / "ollama"
            if not src.is_file():
                src = next((c for c in tmp_path.rglob("ollama") if c.is_file()), src)
            target = LOCAL_BIN_DIR / "ollama"
            shutil.copy2(src, target)
            target.chmod(target.stat().st_mode | stat.S_IEXEC)
            return str(target)
        if os.name == "nt":
            url = f"https://ollama.com/download/ollama-windows-{arch}.zip"
            archive = tmp_path / "ollama.zip"
            _download_file(url, archive)
            with zipfile.ZipFile(archive, "r") as zf:
                _safe_extract_zip(zf, LOCAL_BIN_DIR)
            for candidate in LOCAL_BIN_DIR.rglob("ollama.exe"):
                return str(candidate)
    raise RuntimeError("Klarte ikke å installere Ollama automatisk.")


@contextmanager
def managed_ollama_server(
    log_fn: Callable[[str], None],
    progress_fn: Callable[[int, str], None] | None = None,
    env: dict[str, str] | None = None,
) -> Iterator[str]:
    run_env = env or build_runtime_env()
    ollama_bin = ensure_ollama_binary(run_env, log_fn, progress_fn)

    if fetch_ollama_models() is not None:
        log_fn("Ollama-server kjører allerede. Gjenbruker eksisterende instans.")
        yield ollama_bin
        return

    log_fn("Starter midlertidig Ollama-server i bakgrunnen...")
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        [ollama_bin, "serve"],
        env=run_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    try:
        for _ in range(30):
            if fetch_ollama_models() is not None:
                log_fn("Midlertidig Ollama-server er klar.")
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Midlertidig Ollama-server svarte ikke innen 15 sekunder.")
        yield ollama_bin
    finally:
        log_fn("Stopper midlertidig Ollama-server...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

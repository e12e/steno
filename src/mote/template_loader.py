"""Load HTML/CSS/JS templates and subprocess runner scripts from src/mote/."""

from __future__ import annotations

from pathlib import Path


def _get_package_dir() -> Path:
    return Path(__file__).resolve().parent


def _get_templates_dir() -> Path:
    return _get_package_dir() / "templates"


def _get_runners_dir() -> Path:
    return _get_package_dir() / "runners"


def load_template(name: str, fallback: str = "") -> str:
    """Load an asset from src/mote/templates/ if present, otherwise return fallback."""
    try:
        tpl_path = _get_templates_dir() / name
        if tpl_path.is_file():
            return tpl_path.read_text(encoding="utf-8")
    except Exception:
        pass
    return fallback


def load_runner(name: str, fallback: str = "") -> str:
    """Load a standalone Python runner script from src/mote/runners/ if present."""
    try:
        runner_path = _get_runners_dir() / name
        if runner_path.is_file():
            return runner_path.read_text(encoding="utf-8")
    except Exception:
        pass
    return fallback


def assemble_app_html(fallback: str = "") -> str:
    """Load app.html and inline sibling app.css / app.js when placeholders are present."""
    raw = load_template("app.html", fallback)
    if "/* __STENO_APP_CSS__ */" in raw:
        raw = raw.replace("/* __STENO_APP_CSS__ */", load_template("app.css", ""), 1)
    if "/* __STENO_APP_JS__ */" in raw:
        raw = raw.replace("/* __STENO_APP_JS__ */", load_template("app.js", ""), 1)
    return raw

"""Module for loading HTML templates from src/mote/templates/."""

from __future__ import annotations

from pathlib import Path


def _get_templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def load_template(name: str, fallback: str = "") -> str:
    """Load an HTML template from src/mote/templates/ if present, otherwise return fallback."""
    try:
        tpl_path = _get_templates_dir() / name
        if tpl_path.is_file():
            return tpl_path.read_text(encoding="utf-8")
    except Exception:
        pass
    return fallback

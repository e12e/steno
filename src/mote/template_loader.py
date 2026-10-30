"""Module for loading HTML templates with fallback to built-in string defaults."""

from __future__ import annotations

from pathlib import Path


def _get_templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def load_template(name: str, fallback: str) -> str:
    """Load a template from the templates directory if present, otherwise return fallback."""
    try:
        tpl_path = _get_templates_dir() / name
        if tpl_path.is_file():
            return tpl_path.read_text(encoding="utf-8")
    except Exception:
        pass
    return fallback

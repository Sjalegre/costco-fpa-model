"""Project paths and configuration loading."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "model.yml"
DATA_DIR = ROOT / "data"
REPORT_DIR = DATA_DIR / "reports"
EXPORT_DIR = DATA_DIR / "exports"
# Committed on purpose: GitHub Pages serves from here, so the model's report
# has a URL a reader can open without cloning anything.
DOCS_DIR = ROOT / "docs"


@functools.cache
def load_config(path: Path | None = None) -> dict[str, Any]:
    with (path or CONFIG_PATH).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def ensure_dirs() -> None:
    for directory in (DATA_DIR, REPORT_DIR, EXPORT_DIR):
        directory.mkdir(parents=True, exist_ok=True)

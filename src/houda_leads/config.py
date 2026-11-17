"""讀 config.toml。"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: Path | None = None) -> dict:
    with (path or ROOT / "config.toml").open("rb") as f:
        return tomllib.load(f)

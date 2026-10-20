"""個資處理（失敗模式 7）。送進 LLM 之前一律 redact()。"""
from __future__ import annotations

from .extract import EMAIL, TW_PHONE


def redact(text: str) -> str:
    return TW_PHONE.sub("[PHONE]", EMAIL.sub("[EMAIL]", text))

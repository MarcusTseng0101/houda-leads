"""通路衝突：既有客戶、既有代理商、競品品牌、對方網站有沒有提到我們。純函式。

只有公開名單（厚達基於機密不提供內部名單），所以結果是「給業務確認」，不是定論。
"""
from __future__ import annotations

import re

from .classify import evidence_at
from .extract import company_core
from .rules import NamedEntry

EN_SUFFIX = re.compile(r"[,\s]*(co\.?,?\s*ltd\.?|ltd\.?|inc\.?|corp\.?|corporation|limited)\s*$", re.I)


def _is_ascii(s: str) -> bool:
    return bool(re.fullmatch(r"[\x20-\x7e]+", s))


def norm_name(s: str) -> str:
    return EN_SUFFIX.sub("", company_core(s)).strip().lower()


def find_alias(text: str, alias: str) -> int:
    """英文：單字邊界、不分大小寫；中文：至少 2 個字才比對（1 個字太容易誤中）。"""
    if _is_ascii(alias):
        m = re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, re.I)
        return m.start() if m else -1
    return text.find(alias) if len(alias) >= 2 else -1


def match_entry(company: str, entries: list[NamedEntry]) -> tuple[NamedEntry, str] | None:
    n = norm_name(company)
    for e in entries:
        if n and n == norm_name(e.name):
            return e, f"公司名相同：{e.name}"
        for a in e.aliases:
            if find_alias(company, a) >= 0:
                return e, f"公司名包含別名「{a}」"
    return None


def mentions(text: str, names: list[str], window: int) -> list[str]:
    out = []
    for a in names:
        pos = find_alias(text, a)
        if pos >= 0:
            out.append(evidence_at(text, pos, len(a), window))
    return out


def brands_in(text: str, brands: list[NamedEntry]) -> list[str]:
    return [b.name for b in brands if any(find_alias(text, a) >= 0 for a in b.aliases)]


def channel_signal(text: str, cfg: dict, brands: list[NamedEntry]) -> bool:
    """出現「代理／經銷」這類字，而且提到至少一個競品品牌，才當成通路商。"""
    words = [w for w in cfg["roles"]["channel_signal_words"] if find_alias(text, w) >= 0]
    return bool(words) and bool(brands_in(text, brands))

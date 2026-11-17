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


def subcontract_signal(text: str, cfg: dict) -> list[str]:
    """加工代工廠：「CNC 加工」「代工」「OEM」這類字出現至少 min_hits 個不同的，網站上的 CNC／自動化
    多半是自己工廠的設備，不是在賣這種設備（2026-11-03 台灣 500 家試跑：喬大彈簧、萬向精密、巨翔精密）。"""
    c = cfg.get("subcontract")
    if not c:
        return []
    hits = [w for w in c["signal_words"] if find_alias(text, w) >= 0]
    return hits if len(hits) >= int(c["min_hits"]) else []


def service_signal(text: str, cfg: dict) -> list[str]:
    """安裝、保養、維修商：服務的字出現至少 min_hits 個，而且完全沒有「製造」的字（製造商也常寫安裝與維護，
    所以兩個都要看）。2026-11-04 全球試跑：法國空調安裝商 Genin Froid、Technic Froid。"""
    c = cfg.get("service")
    if not c:
        return []
    if any(find_alias(text, w) >= 0 for w in c["maker_words"]):
        return []
    hits = [w for w in c["signal_words"] if find_alias(text, w) >= 0]
    return hits if len(hits) >= int(c["min_hits"]) else []


def material_signal(registry_text: str, cfg: dict) -> list[str]:
    """化學、樹脂、塗料等材料廠：登記的事業內容有材料字、而且沒有任何設備字。
    網站常寫材料「用在」蓄電池、RFID，會被當成做那些東西（2026-11-04 全球試跑：日本化學廠）。"""
    c = cfg.get("material")
    if not c or not registry_text:
        return []
    if any(find_alias(registry_text, w) >= 0 for w in c["equipment_words"]):
        return []
    return [w for w in c["signal_words"] if find_alias(registry_text, w) >= 0]


def component_signal(text: str, cfg: dict) -> list[str]:
    """元件／材料供應商：出現至少 min_hits 個不同的元件字眼才算（只有 1 個太容易誤中）。"""
    c = cfg["component"]
    hits = [w for w in c["signal_words"] if find_alias(text, w) >= 0]
    return hits if len(hits) >= int(c["min_hits"]) else []

"""產業判定：第 3 步只用關鍵字。判斷不出來的送人工（第 4 步才接 LLM）。

證據一律是網頁可見文字的逐字片段（失敗模式 3），由 scripts/verify_output.py 重新檢查。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import PageText
from .rules import Industry, Keyword

BOUNDARY = "。！？；!?;"


@dataclass
class Classification:
    industry: str                      # 判斷不出來是 ""
    method: str                        # 關鍵字／關鍵字(多數)／人工
    hits: list[str]                    # 選定產業命中的關鍵字
    evidence: list[tuple[str, str]]    # (逐字片段, 網址)
    needs_review: bool
    reason: str
    all_hits: dict[str, list[str]] = field(default_factory=dict)


def find(text_lower: str, kw: Keyword) -> int:
    k = kw.text.lower()
    if kw.ascii_word:                  # 失敗模式 9：英文短字要單字邊界，"systems" 不能中 "ems"
        m = re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", text_lower)
        return m.start() if m else -1
    return text_lower.find(k)


def evidence_at(text: str, pos: int, length: int, window: int) -> str:
    """取命中位置所在的句子，前後最多 window 個字。回傳值一定是 text 的子字串。"""
    start = max(0, pos - window)
    for i in range(pos - 1, start - 1, -1):
        if text[i] in BOUNDARY:
            start = i + 1
            break
    end = min(len(text), pos + length + window)
    for i in range(pos + length, end):
        if text[i] in BOUNDARY:
            end = i + 1
            break
    return text[start:end].strip()


def classify(texts: list[PageText], industries: list[Industry], cfg: dict) -> Classification:
    c = cfg["classify"]
    window = int(c["evidence_window"])
    found: dict[str, list[tuple[str, int, str, int]]] = {}   # 產業 → [(關鍵字, 頁序, 網址, 位置)]
    for pi, t in enumerate(texts):
        low = t.text.lower()
        for ind in industries:
            for kw in ind.keywords:
                if any(h[0] == kw.text for h in found.get(ind.name, [])):
                    continue
                pos = find(low, kw)
                if pos >= 0:
                    found.setdefault(ind.name, []).append((kw.text, pi, t.url, pos))

    all_hits = {k: [h[0] for h in v] for k, v in found.items()}
    if not found:
        return Classification("", "人工", [], [], True, "沒有命中任何關鍵字", all_hits)

    ranked = sorted(found.items(), key=lambda kv: -len(kv[1]))
    top_name, top = ranked[0]
    if len(ranked) == 1:
        method = "關鍵字"
    else:
        second = len(ranked[1][1])
        if len(top) >= c["dominance_min_hits"] and len(top) >= c["dominance_ratio"] * second:
            method = "關鍵字(多數)"
        else:
            detail = "；".join(f"{k}({len(v)})" for k, v in ranked[:3])
            return Classification("", "人工", [], [], True, f"命中多類，無法判斷：{detail}", all_hits)

    ev: list[tuple[str, str]] = []
    for kw, pi, url, pos in top:
        q = evidence_at(texts[pi].text, pos, len(kw), window)
        if any(q in e for e, _ in ev):          # 同一句已經收過（或被包含）就不重複列
            continue
        ev = [(e, u) for e, u in ev if e not in q] + [(q, url)]
        if len(ev) >= 3:
            break
    return Classification(top_name, method, [h[0] for h in top], ev, False, "", all_hits)

"""產業判定：只用關鍵字（使用者 2026-10-20 決定先不用 LLM）。判斷不出來的送人工。

證據一律是網頁可見文字的逐字片段（失敗模式 3），由 scripts/verify_output.py 重新檢查。

分清楚「做什麼」和「提到什麼」（2026-10-20 試跑學到的）：
關鍵字出現在「產品應用」「適用於」「applications」這類字眼後面，說的是對方客戶的產業，
只記成「提及」，不算分數。同一個關鍵字只要有一次出現在應用語境以外，就算「做什麼」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import SENTINEL, PageText
from .rules import Industry, Keyword

BOUNDARY = "。！？；!?;" + SENTINEL


@dataclass
class Classification:
    industry: str                      # 判斷不出來是 ""
    method: str                        # 關鍵字／關鍵字(多數)／人工
    hits: list[str]                    # 選定產業命中的關鍵字（做什麼）
    evidence: list[tuple[str, str]]    # (逐字片段, 網址)
    needs_review: bool
    reason: str
    all_hits: dict[str, list[str]] = field(default_factory=dict)
    mentions: dict[str, list[str]] = field(default_factory=dict)    # 只出現在應用語境的


def positions(text_lower: str, kw: Keyword) -> list[int]:
    k = kw.text.lower()
    if kw.ascii_word:                  # 失敗模式 9：英文短字要單字邊界，"systems" 不能中 "ems"
        # 英文官網多半用複數（control panels）：後面可以多一個 s／es（英國試跑學到的）
        return [m.start() for m in re.finditer(rf"(?<![a-z0-9]){re.escape(k)}(?:e?s)?(?![a-z0-9])", text_lower)]
    return [m.start() for m in re.finditer(re.escape(k), text_lower)]


def find(text_lower: str, kw: Keyword) -> int:
    p = positions(text_lower, kw)
    return p[0] if p else -1


def normalize(text: str, variants: dict[str, str]) -> str:
    """小寫＋異體字（例如 板金→鈑金）。只允許等長替換：位置不變，證據才能從原文取。"""
    low = text.lower()
    for a, b in variants.items():
        assert len(a) == len(b), f"異體字要等長：{a}→{b}"
        low = low.replace(a.lower(), b.lower())
    return low


def in_application_context(low: str, pos: int, markers: list[str], lookback: int) -> bool:
    """往回看到句子邊界（最多 lookback 個字），中間有應用語境的字就算。"""
    start = max(0, pos - lookback)
    for i in range(pos - 1, start - 1, -1):
        if low[i] in BOUNDARY:
            start = i + 1
            break
    before = low[start:pos]
    return any(m.lower() in before for m in markers)


def evidence_at(text: str, pos: int, length: int, window: int) -> str:
    """取命中位置所在的句子，前後最多 window 個字。回傳值一定是 text 的子字串，而且不跨過被拿掉的元素。"""
    start = max(0, pos - window)
    for i in range(pos - 1, start - 1, -1):
        if text[i] in BOUNDARY:
            start = i + 1
            break
    end = min(len(text), pos + length + window)
    for i in range(pos + length, end):
        if text[i] in BOUNDARY:
            end = i if text[i] == SENTINEL else i + 1
            break
    return text[start:end].strip()


def classify(texts: list[PageText], industries: list[Industry], cfg: dict) -> Classification:
    c = cfg["classify"]
    window = int(c["evidence_window"])
    markers, lookback = c["application_markers"], int(c["application_lookback"])
    variants = c.get("text_variants", {})
    found: dict[str, list[tuple[str, int, str, int]]] = {}   # 產業 → [(關鍵字, 頁序, 網址, 位置)]（做什麼）
    mentioned: dict[str, list[str]] = {}
    for pi, t in enumerate(texts):
        low = normalize(t.text, variants)
        for ind in industries:
            for kw in ind.keywords:
                if any(h[0] == kw.text for h in found.get(ind.name, [])):
                    continue
                ps = positions(low, kw)
                if not ps:
                    continue
                own = [p for p in ps if not in_application_context(low, p, markers, lookback)]
                if own:
                    found.setdefault(ind.name, []).append((kw.text, pi, t.url, own[0]))
                elif kw.text not in mentioned.get(ind.name, []):
                    mentioned.setdefault(ind.name, []).append(kw.text)
    for name, hs in found.items():               # 後來的頁面確認是「做什麼」，就不再算提及
        if name in mentioned:
            mentioned[name] = [k for k in mentioned[name] if k not in {h[0] for h in hs}] or mentioned.pop(name)
    mentioned = {k: v for k, v in mentioned.items() if v}

    all_hits = {k: [h[0] for h in v] for k, v in found.items()}
    if not found:
        why = "沒有命中任何關鍵字" if not mentioned else \
            "只在「應用」語境提到：" + "；".join(f"{k}({len(v)})" for k, v in mentioned.items())
        return Classification("", "人工", [], [], True, why, all_hits, mentioned)

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
            return Classification("", "人工", [], [], True, f"命中多類，無法判斷：{detail}", all_hits, mentioned)

    ev: list[tuple[str, str]] = []
    for kw, pi, url, pos in top:
        q = evidence_at(texts[pi].text, pos, len(kw), window)
        if not q or any(q in e for e, _ in ev):  # 同一句已經收過（或被包含）就不重複列
            continue
        ev = [(e, u) for e, u in ev if e not in q] + [(q, url)]
        if len(ev) >= 3:
            break
    return Classification(top_name, method, [h[0] for h in top], ev, False, "", all_hits, mentioned)

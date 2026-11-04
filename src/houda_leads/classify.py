"""產業判定：只用關鍵字（使用者 2026-10-20 決定先不用 LLM）。判斷不出來的送人工。

證據一律是網頁可見文字的逐字片段（失敗模式 3），由 scripts/verify_output.py 重新檢查。

分清楚「做什麼」和「提到什麼」（2026-10-20 試跑學到的）：
關鍵字出現在「產品應用」「適用於」「applications」這類字眼後面，說的是對方客戶的產業，
只記成「提及」，不算分數。同一個關鍵字只要有一次出現在應用語境以外，就算「做什麼」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import HALFWIDTH, SENTINEL, PageText
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
    low = text.translate(HALFWIDTH).lower()      # 全形英數字當半形（ＬＥＤ＝LED），一個字換一個字
    for a, b in variants.items():
        assert len(a) == len(b), f"異體字要等長：{a}→{b}"
        low = low.replace(a.lower(), b.lower())
    return low


def in_application_context(low: str, pos: int, markers: list[str], lookback: int,
                           after_markers: list[str] = (), lookahead: int = 0, length: int = 0) -> bool:
    """往回看到句子邊界（最多 lookback 個字），中間有應用語境的字就算；
    往後看（最多 lookahead 個字，不跨句子）有展覽名稱的字也算。"""
    start = max(0, pos - lookback)
    for i in range(pos - 1, start - 1, -1):
        if low[i] in BOUNDARY:
            start = i + 1
            break
    before = low[start:pos]
    if any(m.lower() in before for m in markers):
        return True
    end = min(len(low), pos + length + lookahead)
    for i in range(pos + length, end):
        if low[i] in BOUNDARY:
            end = i
            break
    after = low[pos + length:end]
    return any(m.lower() in after for m in after_markers)


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


def classify(texts: list[PageText], industries: list[Industry], cfg: dict, hints: list[str] = (),
             lang: str = "zh") -> Classification:
    """hints：登記的產業代碼指向的產業（registry_hints.csv）。只在命中多類、分不出來時拿來決定，
    而且那一類的命中數要至少是第一名的 hint_min_share、也至少 dominance_min_hits 個（避免登記代碼蓋過網站上
    明顯的證據，也避免只中 1 個字就硬判）；兩個有提示的產業平手時不判。
    有提示的產業裡同時有泛用類（generic_industries）和說得出用途的類別時，只看後者。"""
    c = cfg["classify"]
    window = int(c["evidence_window"])
    markers, lookback = list(c["application_markers"]) + c.get(f"application_markers_{lang}", []), int(c["application_lookback"])
    after_markers, lookahead = list(c.get("application_after_markers", [])), int(c.get("application_lookahead", 0))
    after_markers += c.get(f"application_after_markers_{lang}", [])
    suffix_re = re.compile(c[f"application_suffix_regex_{lang}"]) if c.get(f"application_suffix_regex_{lang}") else None
    variants = c.get("text_variants", {})
    ignore = {w.lower() for w in c.get("ignore_keywords_by_lang", {}).get(lang, [])}   # 在這個語言是一般用字
    found: dict[str, list[tuple[str, int, str, int]]] = {}   # 產業 → [(關鍵字, 頁序, 網址, 位置)]（做什麼）
    mentioned: dict[str, list[str]] = {}
    for pi, t in enumerate(texts):
        low = normalize(t.text, variants)
        for ind in industries:
            for kw in ind.keywords:
                if not kw.ascii_word and kw.lang != lang:   # 中文的字不比日文網站，反之亦然
                    continue
                if kw.text.lower() in ignore:
                    continue
                if any(h[0] == kw.text for h in found.get(ind.name, [])):
                    continue
                ps = positions(low, kw)
                if not ps:
                    continue
                own = [p for p in ps if not in_application_context(low, p, markers, lookback,
                                                                     after_markers, lookahead, len(kw.text))
                       and not (suffix_re and suffix_re.match(low, p + len(kw.text)))]
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
        share = float(c.get("hint_min_share", 0.5))
        need = max(int(c["dominance_min_hits"]), share * len(top))   # 只中 1 個字的不算（試跑：1:1:1 平手被硬判）
        ok = [(n, h) for n, h in ranked if n in hints and len(h) >= need]
        specific = [(n, h) for n, h in ok if n not in set(c.get("generic_industries", []))]
        ok = specific or ok                                         # 說得出用途的優先於泛用的「機械設備」
        if len(ok) > 1 and len(ok[0][1]) == len(ok[1][1]):         # 兩個有提示的產業平手：還是分不出來
            ok = []
        if len(top) >= c["dominance_min_hits"] and len(top) >= c["dominance_ratio"] * second:
            method = "關鍵字(多數)"
        elif ok:
            top_name, top = ok[0]
            method = "關鍵字＋登記產業"
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

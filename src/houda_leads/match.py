"""產業 → 推薦型號。純函式：只查 rules，不碰網路、不碰 LLM（架構規則 1）。

型號只能從目錄查出來，不能讓 LLM 推薦（否則會推出目錄裡沒有的型號）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .rules import CatalogItem, ProductRule, Rules

ROLE_MAKER, ROLE_CHANNEL, ROLE_PARTNER, ROLE_CONFIRM = "設備製造商", "通路", "EMS或合作", "待確認"
ROLE_COMPONENT = "元件廠"


@dataclass
class Recommendation:
    rule: ProductRule
    series: list[str]
    alternatives: list[str]
    role: str


def role_for(industry: str, cfg: dict, channel_signal: bool = False, component_signal: bool = False) -> str:
    r = cfg["roles"]
    if channel_signal or industry in r["channel_industries"]:
        return ROLE_CHANNEL
    if component_signal:
        return ROLE_COMPONENT
    if industry in r["partner_industries"]:
        return ROLE_PARTNER
    if industry in r["confirm_first_industries"]:
        return ROLE_CONFIRM
    return ROLE_MAKER


def _blocked(item: CatalogItem, dnr: set[str]) -> bool:
    return item.model in dnr or item.series in dnr


def alternatives(rule: ProductRule, rules: Rules, cfg: dict) -> list[str]:
    """同規格類推（使用者 2026-10-20 決定）：同類別、Pitch 接近、電流電壓不低於參考系列。"""
    m = cfg["match"]
    tol, cap = float(m["alt_pitch_tolerance_mm"]), int(m["alt_max"])
    refs = [c for c in rules.catalog if c.series in rule.series]
    ref_models = {c.model for c in refs}
    best: dict[str, tuple] = {}
    for ref in refs:
        if ref.pitch is None:
            continue
        for c in rules.catalog:
            if c.model in ref_models or _blocked(c, rules.do_not_recommend):
                continue
            if c.category != ref.category or c.pitch is None or abs(c.pitch - ref.pitch) > tol:
                continue
            if ref.amps and (c.amps is None or c.amps < ref.amps):
                continue
            if ref.volts and (c.volts is None or c.volts < ref.volts):
                continue
            score = (abs(c.pitch - ref.pitch), (c.amps or 0) - (ref.amps or 0), c.model)
            if c.model not in best or score < best[c.model]:
                best[c.model] = score
    return [k for k, _ in sorted(best.items(), key=lambda kv: kv[1])][:cap]


def recommend(industry: str, rules: Rules, cfg: dict, channel_signal: bool = False,
              component_signal: bool = False) -> Recommendation | None:
    rule = rules.products.get(industry)
    if rule is None:
        return None
    series = [s for s in rule.series if s not in rules.do_not_recommend]
    alts = [] if rule.spec_required else alternatives(rule, rules, cfg)
    return Recommendation(rule, series, alts, role_for(industry, cfg, channel_signal, component_signal))

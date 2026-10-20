"""讀 inputs/ 的表 → 規則物件。

使用者產業表的欄位名稱在 config.toml [columns]；產業名稱、型號、關鍵字只能來自 CSV，
程式裡不寫死（架構規則 3）。目錄和公開名單的欄位是我們自己產生的格式，寫在這裡。
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

PR_FIELDS = ("industry", "customer_types", "application", "series", "categories",
             "priority_level", "priority_order", "note")
LIST_SEP = "、"          # 表格內的清單分隔
ALIAS_SEP = "；"         # 別名分隔（產業名稱本身含「、」，所以別名用「；」）


class RulesError(Exception):
    """使用者的表有問題（例如兩張表的產業名稱對不上）。要回報，不要自己改 CSV。"""


@dataclass(frozen=True)
class Keyword:
    text: str            # 原文
    ascii_word: bool     # 純英數：要用單字邊界比對
    extra: bool = False  # 來自 keywords_extra.csv（我們補的，不是使用者原表）


@dataclass(frozen=True)
class Industry:
    name: str
    keywords: tuple[Keyword, ...]


@dataclass(frozen=True)
class ProductRule:
    industry: str
    customer_types: str
    application: str
    series: tuple[str, ...]          # 參考系列；「需依規格另行選型」時是空的
    spec_required: bool
    categories: tuple[str, ...]      # 已換成目錄的類別名稱
    priority_level: str
    priority_order: int
    note: str


@dataclass(frozen=True)
class CatalogItem:
    category: str
    model: str
    series: str
    pitch: float | None
    amps: int | None                 # 多個額定值（UL／TUV）取最小，保守
    volts: int | None
    wire: str
    url: str


@dataclass(frozen=True)
class NamedEntry:
    name: str
    aliases: tuple[str, ...]
    extra: dict = field(default_factory=dict, hash=False, compare=False)


@dataclass
class Rules:
    industries: list[Industry]
    products: dict[str, ProductRule]
    catalog: list[CatalogItem]
    do_not_recommend: set[str]
    customers: list[NamedEntry]
    channel_partners: list[NamedEntry]
    competitor_brands: list[NamedEntry]
    skipped_keywords: list[tuple[str, str]]     # (產業, 關鍵字)：被略過的，寫進跑步紀錄
    input_hashes: dict[str, str]

    def industry_names(self) -> list[str]:
        return [i.name for i in self.industries]


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [[c.strip() for c in r] for r in csv.reader(f)]


def _table(path: Path, first_header: str) -> list[dict[str, str]]:
    """表的前幾列是標題和說明，從第一格等於 first_header 的那列開始當表頭。"""
    rows = _read_rows(path)
    for i, r in enumerate(rows):
        if r and r[0] == first_header:
            hdr = r
            return [dict(zip(hdr, x)) for x in rows[i + 1:] if x and x[0]]
    raise RulesError(f"{path.name} 找不到表頭「{first_header}」")


def _split(s: str, sep: str = LIST_SEP) -> list[str]:
    return [x.strip() for x in s.split(sep) if x.strip()]


def _is_ascii_word(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9 .\-/&+]+", s))


def _keywords(name: str, raw: str, skipped: list) -> tuple[Keyword, ...]:
    out = []
    for kw in _split(raw):
        # 失敗模式 9：「主、被動元件」拆開會變成單一個字「主」，幾乎每頁都命中 → 略過並回報
        if not _is_ascii_word(kw) and len(kw) < 2:
            skipped.append((name, kw))
            continue
        out.append(Keyword(kw, _is_ascii_word(kw)))
    return tuple(out)


def _num(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _named(path: Path, name_col: str, alias_col: str) -> list[NamedEntry]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            name = (r.get(name_col) or "").strip()
            if not name:
                continue
            aliases = tuple(_split(r.get(alias_col) or "", ALIAS_SEP)) or (name,)
            out.append(NamedEntry(name, aliases, {k: v for k, v in r.items() if k not in (name_col, alias_col)}))
    return out


def load(inputs: Path, cfg: dict) -> Rules:
    inputs = Path(inputs)
    col = cfg["columns"]
    marker = cfg["match"]["spec_required_marker"]
    skipped: list[tuple[str, str]] = []

    kw_rows = _table(inputs / "industry_keywords.csv", col["keywords_header_first"])
    kn, kw_ = col["keywords_name"], col["keywords_words"]
    industries = [Industry(r[kn], _keywords(r[kn], r.get(kw_, ""), skipped)) for r in kw_rows]


    aliases = {}
    ap = inputs / "category_aliases.csv"
    if ap.exists():
        with ap.open(encoding="utf-8-sig", newline="") as f:
            aliases = {r["表上名稱"].strip(): r["目錄類別"].strip() for r in csv.DictReader(f)}

    products: dict[str, ProductRule] = {}
    for r in _table(inputs / "industry_products.csv", col["products_header_first"]):
        c = {k: r.get(col[f"products_{k}"], "") for k in PR_FIELDS}
        spec_required = c["series"].strip() == marker
        products[c["industry"]] = ProductRule(
            industry=c["industry"],
            customer_types=c["customer_types"],
            application=c["application"],
            series=() if spec_required else tuple(_split(c["series"])),
            spec_required=spec_required,
            categories=tuple(aliases.get(x, x) for x in _split(c["categories"])),
            priority_level=c["priority_level"],
            priority_order=int(_num(c["priority_order"]) or 0),
            note=c["note"],
        )

    kw_names, pr_names = {i.name for i in industries}, set(products)
    if kw_names != pr_names:
        raise RulesError(
            "兩張表的產業名稱對不上（請使用者修表，不要自己改）："
            f"只在關鍵字表 {sorted(kw_names - pr_names)}；只在產品表 {sorted(pr_names - kw_names)}"
        )

    # 補充關鍵字（英文官網、同義詞）：和使用者原表分開放，使用者可以自己刪改
    xp = inputs / "keywords_extra.csv"
    if xp.exists():
        by_name = {i.name: i for i in industries}
        with xp.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                ind = by_name.get(r["標準化產業"].strip())
                if ind is None:
                    raise RulesError(f"keywords_extra.csv 的產業「{r['標準化產業']}」不在產業表裡")
                have = {k.text.lower() for k in ind.keywords}
                add = tuple(Keyword(k.text, k.ascii_word, True) for k in _keywords(ind.name, r["補充關鍵字"], skipped)
                            if k.text.lower() not in have)
                by_name[ind.name] = Industry(ind.name, ind.keywords + add)
        industries = [by_name[i.name] for i in industries]

    catalog = []
    with (inputs / "howder_catalog.csv").open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            amps = [int(a) for a in re.findall(r"(\d+)A", r.get("額定電流", ""))]
            catalog.append(CatalogItem(
                category=r["類別"], model=r["型號"], series=r["系列"],
                pitch=_num(r.get("Pitch(mm)")), amps=min(amps) if amps else None,
                volts=int(_num(r.get("額定電壓(V)")) or 0) or None,
                wire=r.get("線徑(AWG)", ""), url=r.get("網址", ""),
            ))

    dnr = set()
    dp = inputs / "do_not_recommend.csv"
    if dp.exists():
        with dp.open(encoding="utf-8-sig", newline="") as f:
            dnr = {r["型號"].strip() for r in csv.DictReader(f) if r.get("型號", "").strip()}

    customers = _named(inputs / "public_customers.csv", "公司", "別名") + _named(inputs / "existing_customers.csv", "公司", "別名")
    partners = _named(inputs / "public_channel_partners.csv", "名稱", "別名") + _named(inputs / "channel_partners.csv", "名稱", "別名")
    brands = _named(inputs / "competitor_brands.csv", "品牌", "別名")

    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:12] for p in sorted(inputs.glob("*.csv"))}
    return Rules(industries, products, catalog, dnr, customers, partners, brands, skipped, hashes)

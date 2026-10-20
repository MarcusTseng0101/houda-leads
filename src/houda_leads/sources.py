"""讀政府開放資料 → 候選公司。

工廠登記（data.gov.tw 資料集 6569「登記工廠名錄」，產業發展署）：
欄位見 FACTORY_COLS。「工廠負責人姓名」是個資，這裡完全不讀（失敗模式 7）。
同一個統編可能有好幾個廠，合併成一家公司。
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

FACTORY_COLS = {
    "factory_name": "工廠名稱", "tax_id": "統一編號", "address": "工廠地址", "area": "工廠市鎮鄉村里",
    "org_type": "工廠組織型態", "status": "工廠登記狀態", "industries": "產業類別", "products": "主要產品",
}
SEP = "、"
# 「XX股份有限公司新竹廠」→「XX股份有限公司」
AFTER_COMPANY = re.compile(r"^(.*?(?:股份有限公司|有限公司))(.+)$")


@dataclass
class Candidate:
    tax_id: str
    name: str
    address: str
    area: str
    industry_codes: list[str]          # 2 碼大類，例如 28
    industries: list[str]              # 原文，例如「28電力設備及配備製造業」
    products: list[str]                # 原文，例如「281發電、輸電及配電機械」
    factories: list[str] = field(default_factory=list)


def company_name(factory_name: str) -> str:
    s = factory_name.strip()
    m = AFTER_COMPANY.match(s)
    return m.group(1) if m else s


def _items(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(SEP) if x.strip()]


def _read_text(path: Path) -> str:
    b = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp950"):       # 舊版檔案是 Big5
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path} 不是 UTF-8 也不是 Big5")


def load_factory_registry(path: Path, cfg: dict) -> list[Candidate]:
    s = cfg["sources"]
    codes, orgs = set(s["factory_industry_codes"]), set(s["org_types"])
    text = _read_text(path)
    reader = csv.DictReader(text.splitlines())
    missing = [v for v in FACTORY_COLS.values() if v not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"工廠登記檔缺欄位 {missing}：格式可能改了，請先看一下檔案再改 FACTORY_COLS")
    by: dict[str, Candidate] = {}
    c = FACTORY_COLS
    for r in reader:
        if r[c["status"]].strip() != "生產中" or r[c["org_type"]].strip() not in orgs:
            continue
        inds = _items(r[c["industries"]])
        ind_codes = [x[:2] for x in inds]
        if not codes & set(ind_codes):
            continue
        tax = r[c["tax_id"]].strip()
        if not tax:
            continue
        cand = by.get(tax)
        if cand is None:
            cand = by[tax] = Candidate(tax, company_name(r[c["factory_name"]]), r[c["address"]].strip(),
                                       r[c["area"]].strip(), [], [], [])
        for x in inds:
            if x not in cand.industries:
                cand.industries.append(x)
                cand.industry_codes.append(x[:2])
        for x in _items(r[c["products"]]):
            if x not in cand.products:
                cand.products.append(x)
        cand.factories.append(r[c["factory_name"]].strip())
    return list(by.values())

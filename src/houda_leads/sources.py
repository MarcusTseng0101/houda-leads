"""讀政府開放資料 → 候選公司。

工廠登記（data.gov.tw 資料集 6569「登記工廠名錄」，產業發展署）：
欄位見 FACTORY_COLS。「工廠負責人姓名」是個資，這裡完全不讀（失敗模式 7）。
同一個統編可能有好幾個廠，合併成一家公司。

國外（使用者 2026-10-20：「客戶不限國內」）：沒有一份全世界的工廠登記，所以讀一份通用名單
（LIST_COLS：公司名稱、國家、官網，可選註冊編號、來源）。名單從哪來由業務決定
（展覽參展商名單、日本 gBizINFO、英國 Companies House…），每個來源的使用條款要先看過。
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
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
    country: str = "TW"                # ISO 3166-1 兩碼
    website: str = ""                  # 通用名單直接附官網
    source: str = ""
    aliases: list[str] = field(default_factory=list)   # 其他寫法（例如日本公司的英文名），確認官網用


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


LIST_COLS = {"name": "公司名稱", "country": "國家", "website": "官網"}
LIST_OPTIONAL = {"reg_id": "註冊編號", "source": "來源"}


def load_company_list(path: Path) -> list[Candidate]:
    """通用名單（國外用）。註冊編號沒有就用公司名＋國家當 key；同一家出現兩次只留第一次。"""
    text = _read_text(path)
    reader = csv.DictReader(text.splitlines())
    missing = [v for v in LIST_COLS.values() if v not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"名單缺欄位 {missing}：需要 {list(LIST_COLS.values())}，可選 {list(LIST_OPTIONAL.values())}")
    out, seen = [], set()
    for i, r in enumerate(reader, start=2):
        name = (r[LIST_COLS["name"]] or "").strip()
        country = (r[LIST_COLS["country"]] or "").strip().upper()
        if not name:
            continue
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError(f"名單第 {i} 列的國家「{country}」不是兩碼國家代碼（例如 DE、JP、US）")
        reg = (r.get(LIST_OPTIONAL["reg_id"]) or "").strip()
        key = reg or f"{country}:{name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(Candidate(reg, name, "", "", [], [], [], country=country,
                             website=(r[LIST_COLS["website"]] or "").strip(),
                             source=(r.get(LIST_OPTIONAL["source"]) or "").strip()))
    return out


# ---- 英國 Companies House（Free Company Data Product，每月快照，免 key）----
# 表頭有些欄位前面有空白（" CompanyNumber"），讀的時候去掉。檔案裡沒有董事姓名。
GB_COLS = {"name": "CompanyName", "number": "CompanyNumber", "category": "CompanyCategory",
           "status": "CompanyStatus", "accounts": "Accounts.AccountCategory",
           "town": "RegAddress.PostTown", "postcode": "RegAddress.PostCode"}
GB_SIC = [f"SICCode.SicText_{i}" for i in range(1, 5)]


def _open_csv(path: Path):
    """CSV 或只裝一個 CSV 的 zip（不用先解壓 2 GB）。"""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        z = zipfile.ZipFile(path)
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return io.TextIOWrapper(z.open(name), encoding="utf-8-sig", newline="")
    return path.open(encoding="utf-8-sig", newline="")


def load_companies_house(path: Path, cfg: dict) -> list[Candidate]:
    g = cfg["registries"]["gb"]
    prefixes, cats = tuple(g["sic_prefixes"]), set(g["company_categories"])
    excl = {x.upper() for x in g["exclude_account_categories"]}
    out = []
    with _open_csv(path) as f:
        reader = csv.reader(f)
        hdr = [h.strip() for h in next(reader)]
        need = list(GB_COLS.values()) + GB_SIC
        missing = [c for c in need if c not in hdr]
        if missing:
            raise ValueError(f"Companies House 檔缺欄位 {missing}：格式可能改了")
        ix = {c: hdr.index(c) for c in need}
        for r in reader:
            if len(r) < len(hdr):
                continue
            get = lambda c: r[ix[c]].strip()
            if get(GB_COLS["status"]) != "Active" or get(GB_COLS["category"]) not in cats:
                continue
            if get(GB_COLS["accounts"]).upper() in excl:
                continue
            sics = [get(c) for c in GB_SIC if get(c)]
            codes = [s[:2] for s in sics]
            if not any(s.startswith(prefixes) for s in sics):
                continue
            out.append(Candidate(get(GB_COLS["number"]), get(GB_COLS["name"]),
                                 f"{get(GB_COLS['town'])} {get(GB_COLS['postcode'])}".strip(), "",
                                 codes, sics, [], country="GB", source="Companies House 快照"))
    return out


# ---- 日本 gBizINFO 法人基本情報 CSV（下載要免費的 access token）----
# 欄位照 help.info.gbiz.go.jp「法人基本情報CSVファイル」。「代表者名称」是個資，不讀（失敗模式 7）。
JP_COLS = {"number": "法人番号", "name": "商号または名称", "name_en": "商号または名称（英字）",
           "status": "状態", "industries": "事業種目", "website": "WebサイトURL", "summary": "事業概要",
           "pref": "都道府県"}


def load_gbizinfo(path: Path, cfg: dict) -> list[Candidate]:
    words = cfg["registries"]["jp"]["industry_words"]
    out = []
    with _open_csv(path) as f:
        reader = csv.DictReader(f)
        missing = [c for c in JP_COLS.values() if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"gBizINFO 檔缺欄位 {missing}：格式可能改了")
        for r in reader:
            get = lambda k: (r.get(JP_COLS[k]) or "").strip()
            if get("status") == "閉鎖" or not get("name"):
                continue
            inds = [x.strip() for x in get("industries").split("|") if x.strip()]
            if not any(w in x for x in inds for w in words):
                continue
            c = Candidate(get("number"), get("name"), get("pref"), "", [], inds,
                          [get("summary")] if get("summary") else [], country="JP",
                          website=get("website"), source="gBizINFO")
            c.aliases = [get("name_en")] if get("name_en") else []
            out.append(c)
    return out

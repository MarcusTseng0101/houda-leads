"""gold 驗收（PROMPT.md「唯一的成功定義」）和給業務標的空白表。不連網：證據從抓網頁時的快取重新比對。

gold/labels.csv：統編, 標準化產業, 是否真的潛在客戶(Y/N)（業務標的，不准拿來調規則）。
預測：同一批公司跑 `houda-leads run ... --out out/gold_run` 的 leads.csv 和 run.json。
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from . import export, extract

GOLD_ID, GOLD_IND, GOLD_YN = "統編", "標準化產業", "是否真的潛在客戶(Y/N)"


def _rows(path: Path) -> list[dict]:
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook
        ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
        it = ws.iter_rows(values_only=True)
        hdr = [str(h or "").strip() for h in next(it)]
        return [{h: ("" if v is None else str(v).strip()) for h, v in zip(hdr, r)} for r in it if any(r)]
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]


def load_gold(path: Path) -> dict[str, dict]:
    rows = _rows(path)
    missing = [c for c in (GOLD_ID, GOLD_IND, GOLD_YN) if rows and c not in rows[0]]
    if not rows or missing:
        raise ValueError(f"gold 檔要有欄位 {GOLD_ID}、{GOLD_IND}、{GOLD_YN}（缺 {missing or '全部資料'}）")
    out = {}
    for r in rows:
        tid = r[GOLD_ID].lstrip("'")
        yn = r[GOLD_YN].upper()[:1]
        if tid and (r[GOLD_IND] or yn):
            out[tid] = {"industry": r[GOLD_IND], "yn": yn}
    return out


def _unsafe(v: str) -> str:
    """export.csv_safe 加的單引號拿掉（只拿掉公式字元前面那個）。"""
    return v[1:] if v.startswith("'") and v[1:2] and v[1] in export.FORMULA_START else v


def _cached_text(cache_dir: Path, url: str) -> str | None:
    p = Path(cache_dir) / (hashlib.sha256(url.encode()).hexdigest() + ".html")
    if not p.exists():
        return None
    html = p.read_text(encoding="utf-8")
    return extract.content_text(html) + "\n" + extract.visible_text(html)


def check_evidence(rows: list[dict], cache_dir: Path) -> tuple[int, int, list[str]]:
    """回傳 (不在原文的筆數, 快取裡沒有、沒辦法驗的筆數, 不在原文的例子)。"""
    bad, unverifiable, examples = 0, 0, []
    for r in rows:
        pieces = [_unsafe(p).strip() for p in r.get("證據原文", "").split(" | ") if p.strip()]
        urls = [u.strip() for u in r.get("證據網址", "").split(" | ") if u.strip()]
        if not pieces:
            continue
        texts = [t for t in (_cached_text(cache_dir, u) for u in urls) if t is not None]
        if not texts:
            unverifiable += len(pieces)
            continue
        for p in pieces:
            if not any(p in t for t in texts):
                bad += 1
                examples.append(f"{r.get('統編')}: {p[:40]}")
    return bad, unverifiable, examples


def evaluate(gold: dict[str, dict], leads: list[dict], run: dict, priority_level: dict[str, str],
             cache_dir: Path) -> dict:
    by_id = {_unsafe(r.get("統編", "")): r for r in leads}
    per = defaultdict(lambda: {"n": 0, "correct": 0, "confused_with": Counter()})
    n = correct = predicted = 0
    for tid, g in gold.items():
        r = by_id.get(tid)
        if not r or not g["industry"] or r.get("官網確認", "未確認") == "未確認":
            continue                     # 只算有確認官網的公司
        pred = r.get("標準化產業", "")
        n += 1
        predicted += bool(pred)
        d = per[g["industry"]]
        d["n"] += 1
        if pred == g["industry"]:
            correct += 1
            d["correct"] += 1
        else:
            d["confused_with"][pred or "（送人工，沒判）"] += 1

    def precision(rows):
        ys = [gold[_unsafe(r["統編"])]["yn"] for r in rows if _unsafe(r["統編"]) in gold]
        return (sum(y == "Y" for y in ys) / len(ys) if ys else 0.0), len(ys)

    leads_rows = [r for r in leads if r.get("分流") == "潛在客戶"]
    high = [r for r in leads_rows if priority_level.get(r.get("標準化產業", "")) == "高"]
    hp, hn = precision(high)
    lp, ln = precision(leads_rows)
    bad, unver, examples = check_evidence(leads, cache_dir)
    return {
        "industry_accuracy": round(correct / n, 3) if n else 0.0,
        "industry_scored": n,
        "industry_coverage": round(predicted / n, 3) if n else 0.0,     # 有判（沒送人工）的比例
        "high_priority_precision": round(hp, 3), "high_priority_n": hn,
        "lead_precision": round(lp, 3), "lead_n": ln,
        "evidence_not_in_source": bad, "evidence_unverifiable": unver, "evidence_examples": examples[:10],
        "denied_domain_requests": int(run.get("對禁止網域的請求", 0) or 0),
        "gold_n": len(gold), "gold_missing_in_leads": sorted(set(gold) - set(by_id)),
        "per_industry": {k: {"n": v["n"], "correct": v["correct"],
                             "accuracy": round(v["correct"] / v["n"], 3) if v["n"] else 0.0,
                             "confused_with": dict(v["confused_with"].most_common())}
                         for k, v in sorted(per.items(), key=lambda kv: -kv[1]["n"])},
    }


def write_label_sheet(leads: list[dict], industries: list[str], out: Path) -> int:
    """給業務標的空白表：不放工具的判斷（避免業務照著工具填，gold 就不獨立了）。"""
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = "請標"
    hdr = [GOLD_ID, "公司名稱", "官網(工具找到的)", "工廠主要產品(登記)", GOLD_IND, GOLD_YN, "業務備註"]
    ws.append(hdr)
    for r in leads:
        row = [_unsafe(r.get("統編", "")), r.get("公司名稱", ""), r.get("官網", ""),
               _unsafe(r.get("工廠主要產品(登記)", "")), "", "", ""]
        ws.append(row)
        for c in ws[ws.max_row]:
            c.data_type = "s"            # 失敗模式 6：全部當文字，不讓 Excel 當公式
    lst = wb.create_sheet("產業清單")
    for name in industries + ["不是潛在客戶（不屬於任何一類）"]:
        lst.append([name])
    n = ws.max_row
    dv_ind = DataValidation(type="list", formula1=f"=產業清單!$A$1:$A${len(industries) + 1}", allow_blank=True)
    dv_yn = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    ws.add_data_validation(dv_ind)
    ws.add_data_validation(dv_yn)
    dv_ind.add(f"E2:E{n}")
    dv_yn.add(f"F2:F{n}")
    how = wb.create_sheet("怎麼標")
    for line in ["每家打開官網看一下，選「標準化產業」（這家公司自己做什麼，不是它的客戶做什麼）。",
                 "「是否真的潛在客戶」：這家公司的產品會用到我們的產品、值得業務聯絡 → Y；否則 N。",
                 "官網是工具找到的，可能錯；錯了在業務備註寫正確的官網。",
                 "標完存檔，交給我轉成 gold/labels.csv。不要看工具的判斷結果再標。"]:
        how.append([line])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return n - 1


def xlsx_to_gold_csv(xlsx: Path, out: Path) -> int:
    rows = _rows(xlsx)
    out.parent.mkdir(parents=True, exist_ok=True)
    with Path(out).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([GOLD_ID, GOLD_IND, GOLD_YN])
        for r in rows:
            ind = r.get(GOLD_IND, "")
            w.writerow([r.get(GOLD_ID, ""), "" if ind.startswith("不是潛在客戶") else ind, r.get(GOLD_YN, "")])
    return len(rows)


def load_run(leads_csv: Path) -> tuple[list[dict], dict]:
    leads = _rows(leads_csv)
    rp = Path(leads_csv).parent / "run.json"
    run = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
    return leads, run

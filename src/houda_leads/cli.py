"""houda-leads 指令。

  houda-leads run --fixtures DIR --out DIR [--no-llm] [--limit N]
  houda-leads run --source FILE ...        （第 5 步才實作）
  houda-leads eval --gold FILE --json FILE （第 6 步才實作）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from urllib.parse import urljoin

from . import __version__, channel, classify, config, export, extract, match, rules
from .stubs import NotYet


def load_fixture_sites(root: Path) -> list[dict]:
    out = []
    for meta_path in sorted(root.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        files = sorted(meta_path.parent.glob("pages/*.html"), key=lambda p: (p.name != "index.html", p.name))
        pages = [extract.Page(meta["url"] if p.name == "index.html" else urljoin(meta["url"], p.name),
                              p.read_text(encoding="utf-8")) for p in files]
        out.append({"meta": meta, "pages": pages})
    return out


def _region_partners(R: rules.Rules, region: str) -> str:
    names = [f"{e.name}（{e.extra.get('角色', '')}）" for e in R.channel_partners if e.extra.get("區域") == region]
    return "、".join(names)


def process(site: dict, R: rules.Rules, cfg: dict, today: dt.date) -> dict:
    meta, pages = site["meta"], site["pages"]
    name, tax_id, url = meta.get("公司名稱", ""), meta.get("統編", ""), meta.get("url", "")
    texts = [extract.PageText(p.url, extract.visible_text(p.html)) for p in pages]
    joined = " ".join(t.text for t in texts)
    window = int(cfg["classify"]["evidence_window"])

    confirm = extract.site_confirmation(texts, tax_id, name)
    cls = classify.classify(texts, R.industries, cfg)
    sig = channel.channel_signal(joined, cfg, R.competitor_brands)
    rec = match.recommend(cls.industry, R, cfg, sig) if cls.industry else None
    cust = channel.match_entry(name, R.customers)
    partner = channel.match_entry(name, R.channel_partners)
    self_ev = channel.mentions(joined, cfg["self"]["names"], window)
    brands = channel.brands_in(joined, R.competitor_brands)
    contacts = extract.contacts(pages, url, cfg["contacts"]["sales_mailbox_words"])

    notes, review = [], cls.needs_review
    if cls.reason:
        notes.append(cls.reason)
    if confirm == "未確認":
        review = True
        notes.append("官網上找不到統編或完整公司名")
    if rec and rec.rule.spec_required and rec.role == match.ROLE_MAKER:   # 通路、EMS 本來就不直接推型號
        review = True
        notes.append("產業表寫「依規格另行選型」：先問對方規格，再從目錄挑")
    if self_ev:
        review = True
        notes.append("對方網站提到自家公司：可能是既有客戶或在賣我們產品的通路，聯絡前先確認")

    role = rec.role if rec else ""
    if partner:
        lane, follow = "既有代理商", f"既有代理商：{partner[0].name}"
    elif cust:
        lane, follow = "既有客戶", "既有客戶（只比對公開名單，請業務確認）"
        review = True
    elif role == match.ROLE_CHANNEL:
        lane, follow = "潛在代理商", "業務決定（通路商）"
    elif role in (match.ROLE_PARTNER, match.ROLE_CONFIRM):
        lane, follow = "通路與合作", "業務決定"
    elif review or not rec:
        lane, follow = "需人工確認", ""
    else:
        lane = "潛在客戶"
        p = _region_partners(R, cfg["self"]["region"])
        follow = f"未設定（該區公開代理：{p}）" if p else "未設定"

    first = contacts[0] if contacts else None
    col = cfg["columns"]
    rule = rec.rule if rec else None
    return {
        "分流": lane, "統編": tax_id, "公司名稱": name, "官網": url, "官網確認": confirm,
        "工廠主要產品(登記)": meta.get("工廠主要產品", ""),
        "標準化產業": cls.industry, "產業判定方式": cls.method,
        "命中關鍵字": "、".join(cls.hits or [k for v in cls.all_hits.values() for k in v]),
        "證據原文": " | ".join(e for e, _ in cls.evidence),
        "證據網址": " | ".join(dict.fromkeys(u for _, u in cls.evidence)),
        "推薦產品系列": "、".join(rec.series) if rec else "",
        "同規格替代(供參考)": "、".join(rec.alternatives) if rec else "",
        col["products_categories"]: "、".join(rule.categories) if rule else "",
        col["products_application"]: rule.application if rule else "",
        col["products_priority_order"]: (rule.priority_order if lane == "潛在客戶" else "") if rule else "",
        col["products_note"]: rule.note if rule else "",
        "角色旗標": role, "建議跟進": follow,
        "比對依據": (partner or cust or (None, ""))[1],
        "已提到自家公司": " | ".join(self_ev), "提到的競品品牌": "、".join(brands),
        "公司窗口類型": first.kind if first else "", "公司窗口": first.value if first else "",
        "其他窗口": " | ".join(f"{c.kind}:{c.value}" for c in contacts[1:]),
        "具名窗口": "", "具名窗口職稱": "",      # 第一版只抓公司窗口（使用者還沒決定要不要具名窗口）
        "窗口來源網址": first.source_url if first else "",
        "法律依據": "法人聯絡資訊（非個資）" if first else "",
        "蒐集日期": today.isoformat(), "保存到": "",
        "需人工確認(Y/N)": "Y" if review else "N", "備註": "；".join(notes),
    }


def cmd_run(a) -> int:
    cfg = config.load()
    R = rules.load(config.ROOT / "inputs", cfg)
    if a.source:
        raise NotYet("--source 第 5 步才實作（需要一份真的工廠登記檔案）")
    if not a.fixtures:
        print("需要 --fixtures 或 --source", file=sys.stderr)
        return 2
    sites = load_fixture_sites(Path(a.fixtures))[: a.limit or None]
    today = dt.date.today()
    rows = [process(s, R, cfg, today) for s in sites]
    run_log = {
        "版本": __version__, "時間": dt.datetime.now().isoformat(timespec="seconds"),
        "來源": f"fixtures:{a.fixtures}", "公司數": len(rows), "LLM": "未使用（第 3 步只用關鍵字）",
        "輸入檔雜湊": json.dumps(R.input_hashes, ensure_ascii=False),
        "略過的關鍵字（單一個中文字，請修表）": "、".join(f"{i}：{k}" for i, k in R.skipped_keywords),
        "名單說明": "既有客戶和代理商只比對公開名單（官網 logo 和首頁），不完整",
        "分流統計": json.dumps({s: sum(r["分流"] == s for r in rows) for s in export.SHEETS}, ensure_ascii=False),
    }
    export.write(rows, Path(a.out), cfg, run_log)
    (Path(a.out) / "run.json").write_text(json.dumps(run_log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"寫入 {a.out}/leads.xlsx、leads.csv：{len(rows)} 家")
    return 0


def cmd_eval(a) -> int:
    print("eval 第 6 步才實作（需要使用者提供 gold/labels.csv）", file=sys.stderr)
    return 2


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="houda-leads")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--fixtures")
    r.add_argument("--source")
    r.add_argument("--out", required=True)
    r.add_argument("--no-llm", action="store_true")
    r.add_argument("--limit", type=int)
    e = sub.add_parser("eval")
    e.add_argument("--gold", required=True)
    e.add_argument("--json", required=True)
    a = p.parse_args(argv)
    try:
        return cmd_run(a) if a.cmd == "run" else cmd_eval(a)
    except NotYet as ex:
        print(f"尚未實作：{ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

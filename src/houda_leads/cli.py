"""houda-leads 指令。

  houda-leads run --fixtures DIR --out DIR [--no-llm] [--limit N]
  houda-leads run --source 工廠登記.csv --websites 統編官網.csv --out DIR --limit 20
  houda-leads run --registry gb --source BasicCompanyData.zip --out DIR --sample 20
  houda-leads run --list 國外名單.csv --out DIR   （公司名稱,國家,官網[,註冊編號,來源]）
  houda-leads eval --gold gold/labels.csv --json out/eval.json [--leads out/gold_run/leads.csv]
  houda-leads label-sheet --leads out/gold_run/leads.csv --out gold/labels_待標.xlsx
  houda-leads gold-csv --xlsx gold/labels_待標.xlsx --out gold/labels.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
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
    # reclassify：官網是之前找官網時確認過的（用英文名＋電話等），沿用那次的結果
    texts = [extract.PageText(p.url, extract.visible_text(p.html)) for p in pages]          # 整頁（含頁尾）
    content = [extract.PageText(p.url, extract.content_text(p.html)) for p in pages]        # 分類用（去掉選單）
    joined = " ".join(t.text for t in texts)
    window = int(cfg["classify"]["evidence_window"])

    home = cfg["self"]["region"]
    country = (meta.get("國家") or home).upper()
    cinfo = R.countries.get(country)
    region = cinfo["代理區域"] if cinfo else ""
    outreach = cinfo["外聯規則"] if cinfo else "需確認（國家不在 country_rules.csv）"
    label = "統編" if country == home else "註冊編號"
    confirm = "未確認"
    for n in [name] + list(meta.get("別名") or []):      # 日本公司的英文名也算
        confirm = extract.site_confirmation(texts, tax_id, n, label)
        if confirm != "未確認":
            break
    if meta.get("官網確認") and confirm == "未確認":
        confirm = meta["官網確認"]
    codes = re.findall(r"(?:^|、)(\d{2,5})", meta.get("工廠主要產品", ""))
    hints = R.hinted_industries(country, codes)
    langs = cfg["classify"].get("lang_by_country", {})
    cls = classify.classify(content, R.industries, cfg, hints, langs.get(country, langs.get("default", "zh")))
    sig = channel.channel_signal(joined, cfg, R.competitor_brands)
    comp = channel.component_signal(" ".join(t.text for t in content), cfg)
    sub = channel.subcontract_signal(" ".join(t.text for t in content), cfg)
    svc = channel.service_signal(" ".join(t.text for t in content), cfg)
    rec = match.recommend(cls.industry, R, cfg, sig, bool(comp)) if cls.industry else None
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
        notes.append("官網上找不到統編（註冊編號）或完整公司名")
    # 2026-11-02 使用者決定：「依規格另行選型」說的是怎麼挑型號，不是不是客戶（這類產業表寫優先度「高」）。
    # 照樣列潛在客戶，不推型號，只在備註提醒先問規格
    if rec and rec.rule.spec_required and rec.role == match.ROLE_MAKER:   # 通路、EMS 本來就不直接推型號
        notes.append("產業表寫「依規格另行選型」：先問對方規格，再從目錄挑")
    if self_ev:
        review = True
        notes.append("對方網站提到自家公司：可能是既有客戶或在賣我們產品的通路，聯絡前先確認")

    role = rec.role if rec else (match.ROLE_COMPONENT if comp else "")
    if svc and role == match.ROLE_MAKER:
        review = True
        notes.append("看起來是安裝／保養／維修商（" + "、".join(svc) + "），網站上沒有「製造」：可能是裝別人的設備，請業務確認")
    if sub and role == match.ROLE_MAKER:
        review = True
        notes.append("看起來是加工代工廠（" + "、".join(sub) + "）：網站上的 CNC／自動化可能是自己工廠的設備，不是產品，請業務確認")
    if comp:
        notes.append("看起來是元件／材料供應商（" + "、".join(comp) + "）：產業表提醒這類公司不一定直接用我們的產品")
    if partner:
        lane, follow = "既有代理商", f"既有代理商：{partner[0].name}"
    elif cust:
        lane, follow = "既有客戶", "既有客戶（只比對公開名單，請業務確認）"
        review = True
    elif role == match.ROLE_CHANNEL:
        lane, follow = "潛在代理商", "業務決定（通路商）"
        if country != home and (p := _region_partners(R, region)):
            notes.append(f"該區已有公開代理 {p}：開發新通路前先確認代理合約（可能是獨家）")
    elif role in (match.ROLE_PARTNER, match.ROLE_CONFIRM, match.ROLE_COMPONENT):
        lane, follow = "通路與合作", "業務決定"
    elif review or not rec:
        lane, follow = "需人工確認", ""
    elif len(cls.hits) < int(cfg["classify"].get("min_hits_for_lead", 1)):
        # 只中 1 個關鍵字：證據太少（試跑：「Adobe Reader」中 reader、「包装機」是它包裝自己的衛生紙）
        lane, follow = "需人工確認", ""
        review = True
        notes.append(f"只中 {len(cls.hits)} 個關鍵字，證據太少，請業務看一下")
    else:
        lane = "潛在客戶"
        p = _region_partners(R, region) if region else ""
        if country == home:
            follow = f"未設定（該區公開代理：{p}）" if p else "未設定"
        else:     # 國外：先給該區代理，避免和代理商搶客戶
            follow = f"交給該區代理：{p}" if p else "未設定（這個國家沒有公開代理，業務決定）"

    first = contacts[0] if contacts else None
    col = cfg["columns"]
    rule = rec.rule if rec else None
    return {
        "分流": lane, "統編": tax_id, "公司名稱": name, "國家": country, "代理區域": region, "外聯規則": outreach,
        "官網": url, "官網確認": confirm,
        "工廠主要產品(登記)": meta.get("工廠主要產品", ""),
        "名單來源": meta.get("來源", ""),
        "標準化產業": cls.industry, "產業判定方式": cls.method,
        "候選產業(命中數)": "；".join(f"{k}({len(v)})" for k, v in sorted(cls.all_hits.items(), key=lambda kv: -len(kv[1]))),
        "登記代碼提示": "、".join(hints),
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


def load_websites(path: Path) -> dict[str, str]:
    """統編 → 官網候選網址。官網是不是這家公司，由 process() 用統編／公司名再確認一次（失敗模式 4）。"""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return {r["統編"].strip(): (r.get("官網") or "").strip() for r in csv.DictReader(f) if r.get("統編", "").strip()}


def source_discover_sites(a, cfg, stats: dict):
    """台灣工廠登記＋出進口廠商登記（--trade）：用英文名猜網域，網站上看得到統編、或公司名＋登記電話才採用。"""
    from . import crawl, discover, fetch, sources
    cands = sources.load_factory_registry(Path(a.source), cfg)
    stats["初篩後候選公司"] = len(cands)
    if a.only:
        with Path(a.only).open(encoding="utf-8-sig", newline="") as f:
            want = [r["統編"].strip() for r in csv.DictReader(f) if r.get("統編", "").strip()]
        by = {c.tax_id: c for c in cands}
        cands = [by[t] for t in want if t in by]
    elif a.sample:
        import random
        cands = random.Random(a.seed).sample(cands, min(a.sample, len(cands)))
    cands = cands[: a.limit or None]
    done = getattr(a, "done", set())
    stats["本次要跑"] = sum(c.tax_id not in done for c in cands)
    cands = [c for c in cands if c.tax_id not in done]
    trade = sources.load_trade_registry(Path(a.trade), {c.tax_id for c in cands})
    fetcher = fetch.Fetcher(cfg, cache_dir=config.ROOT / "data" / "cache")
    if a.search:
        _check_search_service(cfg)
    for c in cands:
        t0 = time.monotonic()
        t = trade.get(c.tax_id)
        names = [t.name_en, c.name] if t and t.name_en else [c.name]
        proof = [f"tel:{t.phone_digits}"] if t and t.phone_digits else []
        if (ak := discover.address_key(c.address)):
            proof.append(f"addr:{ak}")
        if t and t.name_en:
            url, _home, problems = discover.discover(fetcher, names, c.tax_id, "TW", cfg, proof)
        else:
            url, problems = "", ["出進口廠商登記查不到英文名稱，猜不出網域"]
        how = "猜網域" if url else ""
        if not url and a.search:
            tried = [u for n in names for u in discover.candidate_urls(n, "TW", cfg)]
            # 加統編的結果常被名錄網站佔滿，再只搜公司名一次
            url, more = _search_site(fetcher, [f"{c.name} {c.tax_id}", c.name], names, c.tax_id, "TW", cfg, proof, tried)
            problems += more
            how = "搜尋" if url else ""
        pages, more = crawl.crawl_site(fetcher, url, cfg) if url else ([], ["沒有找到官網"])
        problems += more
        how = how or "沒找到"
        stats.setdefault("官網從哪來", {}).setdefault(how, 0)
        stats["官網從哪來"][how] += 1
        stats.setdefault("每家秒數", []).append(round(time.monotonic() - t0, 1))
        stats.setdefault("每家頁數", []).append(len(pages))
        meta = {"公司名稱": c.name, "統編": c.tax_id, "url": url, "別名": [t.name_en] if t and t.name_en else [],
                "工廠主要產品": "、".join(c.products)}
        yield {"meta": meta, "pages": pages, "problems": problems}
    stats["請求數"] = fetcher.requests
    stats["搜尋次數"] = fetcher.searches
    stats["對禁止網域的請求"] = fetcher.denied_requests


def _search_site(fetcher, queries: list[str], names: list[str], reg_id: str, country: str, cfg: dict,
                 proof: list[str], tried: list[str]) -> tuple[str, list[str]]:
    """猜網域找不到：依序用每個查詢搜尋，第一個確認過的網站就用（台灣、英國、日本共用）。"""
    from . import discover
    problems = []
    for q in queries:
        url, _home, more = discover.discover_by_search(fetcher, q, names, reg_id, cfg, proof, tried, country)
        problems += more
        if url:
            return url, problems
        tried = list(tried) + [l[3:].split("：")[0] for l in more if l.startswith("搜尋 ")]
    return "", problems


def _check_search_service(cfg) -> None:
    """--search 要先開 searchcrawl 的 HTTP 服務；沒開就一開始就停，不要跑完才發現每家都搜尋失敗。"""
    import urllib.request
    health = cfg["search"]["url"].rsplit("/v1/", 1)[0] + "/health"
    try:
        with urllib.request.urlopen(health, timeout=5) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    if not ok:
        raise NotYet(f"--search 需要 searchcrawl 的 HTTP 服務（{health} 沒回應）："
                     "到 C:/Users/tseng/searchcrawl 執行 npm start")


def source_sites(a, cfg, stats: dict):
    from . import crawl, fetch, sources
    if not a.websites:
        if a.trade:
            yield from source_discover_sites(a, cfg, stats)
            return
        raise NotYet("沒有官網對照表：給 --websites「統編,官網」，或給 --trade 出進口廠商登記檔讓工具自己猜網域")
    cands = {c.tax_id: c for c in sources.load_factory_registry(Path(a.source), cfg)}
    stats["初篩後候選公司"] = len(cands)
    web = load_websites(Path(a.websites))
    fetcher = fetch.Fetcher(cfg, cache_dir=config.ROOT / "data" / "cache")
    for tax in list(web)[: a.limit or None]:
        c = cands.get(tax)
        if c is None:
            stats.setdefault("不在初篩名單", []).append(tax)
            continue
        t0 = time.monotonic()
        pages, problems = crawl.crawl_site(fetcher, web[tax], cfg) if web[tax] else ([], ["沒有找到官網"])
        stats.setdefault("每家秒數", []).append(round(time.monotonic() - t0, 1))
        stats.setdefault("每家頁數", []).append(len(pages))
        meta = {"公司名稱": c.name, "統編": c.tax_id, "url": web[tax], "工廠主要產品": "、".join(c.products)}
        yield {"meta": meta, "pages": pages, "problems": problems}
    stats["請求數"] = fetcher.requests
    stats["對禁止網域的請求"] = fetcher.denied_requests


def registry_sites(a, cfg, stats: dict):
    """國外政府登記（--registry gb|jp）：登記檔有官網就用，沒有就猜網域（discover.py，確認過才採用）。"""
    from . import crawl, discover, fetch, sources
    load = {"gb": sources.load_companies_house, "jp": sources.load_gbizinfo, "fr": sources.load_sirene}[a.registry]
    cands = load(Path(a.source), cfg)
    stats["初篩後候選公司"] = len(cands)
    if a.sample:                     # 試跑：固定種子抽樣（登記檔照名字排序，取前 N 家會全是 A 開頭）
        import random
        cands = random.Random(a.seed).sample(cands, min(a.sample, len(cands)))
    cands = cands[: a.limit or None]
    done = getattr(a, "done", set())
    stats["本次要跑"] = sum(c.tax_id not in done for c in cands)
    cands = [c for c in cands if c.tax_id not in done]
    fetcher = fetch.Fetcher(cfg, cache_dir=config.ROOT / "data" / "cache")
    if a.search:
        _check_search_service(cfg)
    for c in cands:
        t0 = time.monotonic()
        names = [c.name] + c.aliases
        url, problems = c.website, []
        if url:
            pages, problems = crawl.crawl_site(fetcher, url, cfg)
            how = "登記檔"
        else:
            proof = [c.postcode] if c.postcode else []
            url, _home, problems = discover.discover(fetcher, names, c.tax_id, c.country, cfg, proof)
            how = "猜網域" if url else ""
            if not url and a.search:
                tried = [u for n in names for u in discover.candidate_urls(n, c.country, cfg)]
                qs = [f"{c.name} {c.postcode}".strip(), c.name] if c.postcode else [c.name]
                url, more = _search_site(fetcher, qs, names, c.tax_id, c.country, cfg, proof, tried)
                problems += more
                how = "搜尋" if url else ""
            pages, more = crawl.crawl_site(fetcher, url, cfg) if url else ([], ["沒有找到官網"])
            problems += more
        stats.setdefault("官網從哪來", {}).setdefault(how or "沒找到", 0)
        stats["官網從哪來"][how or "沒找到"] += 1
        stats.setdefault("每家秒數", []).append(round(time.monotonic() - t0, 1))
        stats.setdefault("每家頁數", []).append(len(pages))
        meta = {"公司名稱": c.name, "統編": c.tax_id, "國家": c.country, "url": url, "別名": c.aliases,
                "工廠主要產品": "、".join(c.industries + c.products), "來源": c.source}
        yield {"meta": meta, "pages": pages, "problems": problems}
    stats["請求數"] = fetcher.requests
    stats["搜尋次數"] = fetcher.searches
    stats["對禁止網域的請求"] = fetcher.denied_requests


def list_sites(a, cfg, stats: dict):
    """通用名單（國外）：名單自己附官網，不用 --websites。"""
    from . import crawl, fetch, sources
    cands = sources.load_company_list(Path(a.list))[: a.limit or None]
    stats["名單公司數"] = len(cands)
    stats["國家"] = {k: sum(c.country == k for c in cands) for k in dict.fromkeys(c.country for c in cands)}
    fetcher = fetch.Fetcher(cfg, cache_dir=config.ROOT / "data" / "cache")
    for c in cands:
        t0 = time.monotonic()
        pages, problems = crawl.crawl_site(fetcher, c.website, cfg) if c.website else ([], ["沒有找到官網"])
        stats.setdefault("每家秒數", []).append(round(time.monotonic() - t0, 1))
        stats.setdefault("每家頁數", []).append(len(pages))
        meta = {"公司名稱": c.name, "統編": c.tax_id, "國家": c.country, "url": c.website}
        if c.source:
            problems = problems + [f"名單來源：{c.source}"]
        yield {"meta": meta, "pages": pages, "problems": problems}
    stats["請求數"] = fetcher.requests
    stats["對禁止網域的請求"] = fetcher.denied_requests


def cmd_run(a) -> int:
    cfg = config.load()
    R = rules.load(config.ROOT / "inputs", cfg)
    stats: dict = {}
    if a.list:
        sites = list_sites(a, cfg, stats)
        src = f"list:{a.list}"
    elif a.source and a.registry != "tw":
        sites = registry_sites(a, cfg, stats)
        src = f"registry:{a.registry} source:{a.source}"
    elif a.source:
        sites = source_sites(a, cfg, stats)
        src = f"source:{a.source} websites:{a.websites}" if a.websites else f"source:{a.source} trade:{a.trade}"
    elif a.fixtures:
        sites = load_fixture_sites(Path(a.fixtures))[: a.limit or None]
        src = f"fixtures:{a.fixtures}"
    else:
        print("需要 --fixtures、--source 或 --list", file=sys.stderr)
        return 2
    today = dt.date.today()
    t0 = time.monotonic()
    rows = []
    # 每跑完一家就寫一行到 progress.jsonl：全量要好幾天，中斷後加 --resume 從這裡接著跑（2026-11-03）
    ckpt = Path(a.out) / "progress.jsonl"
    if ckpt.exists() and not a.resume:
        print(f"{ckpt} 已經有上次跑到一半的結果：加 --resume 接著跑，或換一個 --out", file=sys.stderr)
        return 2
    if a.resume and ckpt.exists():
        rows = [json.loads(l) for l in ckpt.read_text(encoding="utf-8").splitlines() if l.strip()]
        a.done = {r["統編"] for r in rows}
        print(f"[接著跑] 已有 {len(rows)} 家", file=sys.stderr)
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    every = int(cfg.get("run", {}).get("progress_every", 25))
    with ckpt.open("a", encoding="utf-8") as cf:
        for s in sites:
            row = process(s, R, cfg, today)
            if s.get("problems"):
                row["備註"] = "；".join([row["備註"]] + s["problems"]).strip("；")
                if not s["pages"]:
                    row["分流"], row["需人工確認(Y/N)"] = "需人工確認", "Y"
            rows.append(row)
            cf.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            cf.flush()
            n = len(stats.get("每家秒數") or [])
            if every and n and n % every == 0:
                avg = sum(stats["每家秒數"]) / n
                left = (stats.get("本次要跑") or n) - n
                found = sum(1 for r in rows if r.get("官網"))
                print(f"[進度] 這次跑完 {n} 家（共 {len(rows)} 家），有官網 {found}，平均每家 {avg:.1f} 秒，"
                      f"預估還要 {left * avg / 3600:.1f} 小時", file=sys.stderr, flush=True)
    secs = stats.get("每家秒數") or []
    if secs:
        avg = sum(secs) / len(secs)
        stats["平均每家秒數"] = round(avg, 1)
        if "初篩後候選公司" in stats:
            stats["推算全部候選要幾小時（只算爬網頁）"] = round(avg * stats["初篩後候選公司"] / 3600, 1)
    run_log = {
        "版本": __version__, "時間": dt.datetime.now().isoformat(timespec="seconds"),
        "來源": src, "公司數": len(rows), "總秒數": round(time.monotonic() - t0, 1),
        "LLM": "未使用（只用關鍵字）",
        **{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in stats.items()},
        "輸入檔雜湊": json.dumps(R.input_hashes, ensure_ascii=False),
        "略過的關鍵字（單一個中文字，請修表）": "、".join(f"{i}：{k}" for i, k in R.skipped_keywords),
        "名單說明": "既有客戶和代理商只比對公開名單（官網 logo 和首頁），不完整",
        "分流統計": json.dumps({s: sum(r["分流"] == s for r in rows) for s in export.SHEETS}, ensure_ascii=False),
    }
    export.write(rows, Path(a.out), cfg, run_log)
    ckpt.unlink()                   # 正常跑完：Excel 已經寫好，接著跑用的暫存檔不需要了
    (Path(a.out) / "run.json").write_text(json.dumps(run_log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"寫入 {a.out}/leads.xlsx、leads.csv：{len(rows)} 家")
    return 0


def cmd_reclassify(a) -> int:
    """用快取重新分類之前跑過的結果（不連網）：比對規則改之前、之後。沒有官網的列照抄。"""
    from . import crawl, fetch
    cfg = config.load(Path(a.config) if a.config else None)
    R = rules.load(config.ROOT / "inputs", cfg)
    fetcher = fetch.Fetcher(cfg, cache_dir=config.ROOT / "data" / "cache")
    fetcher.offline = True
    today = dt.date.today()
    for d in a.runs:
        d = Path(d)
        with (d / "leads.csv").open(encoding="utf-8-sig", newline="") as f:
            old = list(csv.DictReader(f))
        rows = []
        for r in old:
            if not r.get("官網"):
                rows.append(r)
                continue
            pages, problems = crawl.crawl_site(fetcher, r["官網"], cfg)
            meta = {"公司名稱": r["公司名稱"], "統編": r["統編"].lstrip("'"), "國家": r.get("國家", ""),
                    "url": r["官網"], "工廠主要產品": r.get("工廠主要產品(登記)", ""), "官網確認": r.get("官網確認", "")}
            row = process({"meta": meta, "pages": pages}, R, cfg, today)
            if not pages:
                row["分流"], row["需人工確認(Y/N)"] = "需人工確認", "Y"
            rows.append(row)
        out = Path(str(d) + a.suffix)
        run = json.loads((d / "run.json").read_text(encoding="utf-8")) if (d / "run.json").exists() else {}
        run["重新分類"] = f"{dt.datetime.now().isoformat(timespec='seconds')} 只用快取（{d}）"
        export.write(rows, out, cfg, run)
        (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{d} → {out}：{len(rows)} 家", flush=True)
    print(f"沒有快取、跳過的請求：{fetcher.requests}")
    return 0


def cmd_merge(a) -> int:
    """好幾國的 run 結果合成一份全球 Excel（out/global_leads.xlsx）。"""
    cfg = config.load()
    runs = []
    for d in a.runs:
        d = Path(d)
        with (d / "leads.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        run = json.loads((d / "run.json").read_text(encoding="utf-8")) if (d / "run.json").exists() else {}
        country = next((r["國家"] for r in rows if r.get("國家")), d.name)
        runs.append((country, rows, run))
    summary = export.write_global(runs, Path(a.out), cfg)
    for k, v in summary.items():
        print(k, v)
    print(f"寫入 {a.out}/global_leads.xlsx")
    return 0


def cmd_eval(a) -> int:
    """PROMPT.md 唯一的成功定義。預測來自 --leads（預設 out/gold_run/leads.csv），證據用快取重新比對。"""
    from . import evaluate
    cfg = config.load()
    R = rules.load(config.ROOT / "inputs", cfg)
    gold = evaluate.load_gold(Path(a.gold))
    leads, run = evaluate.load_run(Path(a.leads))
    levels = {k: v.priority_level for k, v in R.products.items()}
    res = evaluate.evaluate(gold, leads, run, levels, config.ROOT / "data" / "cache")
    Path(a.json).parent.mkdir(parents=True, exist_ok=True)
    Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"產業判對 {res['industry_accuracy']}（{res['industry_scored']} 家有確認官網）；"
          f"高優先命中 {res['high_priority_precision']}（{res['high_priority_n']} 家）；"
          f"證據不在原文 {res['evidence_not_in_source']}；禁止網域請求 {res['denied_domain_requests']}")
    return 0


def cmd_label_sheet(a) -> int:
    from . import evaluate
    cfg = config.load()
    R = rules.load(config.ROOT / "inputs", cfg)
    leads, _ = evaluate.load_run(Path(a.leads))
    n = evaluate.write_label_sheet(leads, R.industry_names(), Path(a.out))
    print(f"寫入 {a.out}：{n} 家待標（沒有放工具的判斷）")
    return 0


def cmd_gold_csv(a) -> int:
    from . import evaluate
    n = evaluate.xlsx_to_gold_csv(Path(a.xlsx), Path(a.out))
    print(f"寫入 {a.out}：{n} 家")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="houda-leads")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--fixtures")
    r.add_argument("--source", help="工廠登記 CSV（data.gov.tw 資料集 6569）")
    r.add_argument("--websites", help="「統編,官網」對照表（人工找的）；沒給就要給 --trade")
    r.add_argument("--trade", help="出進口廠商登記 CSV（data.gov.tw 資料集 79641）：用英文名猜官網網域")
    r.add_argument("--only", help="只跑這份 CSV 的「統編」欄列出的公司（量測用）")
    r.add_argument("--resume", action="store_true", help="從 --out 裡的 progress.jsonl 接著跑（已跑過的公司不再跑）")
    r.add_argument("--search", action="store_true",
                   help="猜不到網域時用自架的 searchcrawl 搜尋（要先 npm start；設定在 config.toml [search]）")
    r.add_argument("--registry", choices=["tw", "gb", "jp", "fr"], default="tw",
                   help="--source 是哪一種登記檔：tw 工廠登記、gb Companies House 快照、jp gBizINFO 基本情報")
    r.add_argument("--sample", type=int, help="試跑：從初篩名單隨機抽 N 家（固定種子）")
    r.add_argument("--seed", type=int, default=20261020)
    r.add_argument("--list", help="通用名單 CSV（國外用）：公司名稱,國家,官網[,註冊編號,來源]")
    r.add_argument("--out", required=True)
    r.add_argument("--no-llm", action="store_true")
    r.add_argument("--limit", type=int)
    e = sub.add_parser("eval")
    e.add_argument("--gold", required=True)
    e.add_argument("--json", required=True)
    e.add_argument("--leads", default=str(config.ROOT / "out" / "gold_run" / "leads.csv"),
                   help="同一批公司跑出來的 leads.csv（預設 out/gold_run/leads.csv）")
    ls = sub.add_parser("label-sheet", help="給業務標 gold 的空白表（不放工具的判斷）")
    ls.add_argument("--leads", required=True)
    ls.add_argument("--out", required=True)
    gc = sub.add_parser("gold-csv", help="業務標好的 xlsx → gold/labels.csv")
    gc.add_argument("--xlsx", required=True)
    rc = sub.add_parser("reclassify", help="只用快取重新分類之前跑過的結果（不連網）")
    rc.add_argument("--runs", nargs="+", required=True)
    rc.add_argument("--suffix", default="_re", help="輸出資料夾＝原資料夾＋這個字尾")
    rc.add_argument("--config", help="用另一份設定檔（比對規則改之前用）")
    mg = sub.add_parser("merge", help="好幾國的結果合成一份全球 Excel")
    mg.add_argument("--runs", nargs="+", required=True, help="各國 run 的 --out 資料夾")
    mg.add_argument("--out", required=True)
    gc.add_argument("--out", default=str(config.ROOT / "gold" / "labels.csv"))
    a = p.parse_args(argv)
    try:
        return {"run": cmd_run, "eval": cmd_eval, "label-sheet": cmd_label_sheet, "gold-csv": cmd_gold_csv, "merge": cmd_merge,
                "reclassify": cmd_reclassify}[a.cmd](a)
    except NotYet as ex:
        print(f"尚未實作：{ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

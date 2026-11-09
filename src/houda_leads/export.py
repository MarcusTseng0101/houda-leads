"""輸出 xlsx＋CSV（失敗模式 6：BOM、公式注入）。

leads.csv 放全部公司（用「分流」欄區分）；xlsx 依分流切成多張工作表。
"""
from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
SHEETS = ["潛在客戶", "潛在代理商", "通路與合作", "既有客戶", "既有代理商", "需人工確認"]


def columns(cfg: dict) -> list[str]:
    col = cfg["columns"]   # 和產業表同名的欄位，名稱從設定讀
    return ["分流", "統編", "公司名稱", "國家", "代理區域", "外聯規則", "官網", "官網確認", "工廠主要產品(登記)", "名單來源", "標準化產業", "產業判定方式", "候選產業(命中數)", "登記代碼提示",
            "命中關鍵字", "證據原文", "證據網址", "推薦產品系列", "同規格替代(供參考)",
            col["products_categories"], col["products_application"], col["products_priority_order"],
            col["products_note"], "角色旗標", "建議跟進", "比對依據", "已提到自家公司", "提到的競品品牌",
            "公司窗口類型", "公司窗口", "其他窗口", "具名窗口", "具名窗口職稱", "窗口來源網址", "法律依據",
            "蒐集日期", "保存到", "LinkedIn搜尋連結(業務手動點)", "需人工確認(Y/N)", "備註"]


def linkedin_search_link(company: str) -> str:
    """只產生給業務手動點的連結；程式永遠不請求這個網址（失敗模式 1）。"""
    return "https://www.linkedin.com/search/results/companies/?keywords=" + quote(company)


def csv_safe(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s.startswith(FORMULA_START) else s


def xlsx_safe(v) -> str:
    """Excel 不收控制字元：網頁抓到亂碼時拿掉，不讓整個 run 在最後寫檔時停掉（2026-11-09 英國全量）。CSV 照原樣。"""
    return ILLEGAL_CHARACTERS_RE.sub("", "" if v is None else str(v))


def write(rows: list[dict], out: Path, cfg: dict, run_log: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cols = columns(cfg)
    for r in rows:     # 搜尋連結只在這裡產生（check.sh：只有 fetch.py、export.py 可以碰這個網域）
        r["LinkedIn搜尋連結(業務手動點)"] = linkedin_search_link(r.get("公司名稱", ""))
    with (out / "leads.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([csv_safe(r.get(c, "")) for c in cols])

    wb = Workbook()
    wb.remove(wb.active)
    for name in SHEETS:
        ws = wb.create_sheet(name)
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True)
        # 自己記列號：每格都問 ws.max_row 會掃整張表，英國 9,579 列要好幾小時（2026-11-09 英國全量）
        for i, r in enumerate((x for x in rows if x.get("分流") == name), start=2):
            for j, c in enumerate(cols, start=1):
                cell = ws.cell(i, j)
                cell.value = xlsx_safe(r.get(c))
                cell.data_type = "s"           # 一律當文字：不讓 "=..." 變成公式
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = ws.dimensions
    log = wb.create_sheet("跑步紀錄")
    for k, v in run_log.items():
        log.append([k, str(v)])
    wb.save(out / "leads.xlsx")


ACTION_LANES = ["潛在客戶", "潛在代理商", "通路與合作"]   # 業務可以直接跟進的


def _sheet(wb, name: str, cols: list[str], rows: list[dict]):
    ws = wb.create_sheet(name[:31])
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    # 自己記列號：每格都問 ws.max_row 會掃整張表，1,600 列就卡住半小時（2026-11-04 全球合併）
    for i, r in enumerate(rows, start=2):
        for j, c in enumerate(cols, start=1):
            cell = ws.cell(i, j)
            cell.value = xlsx_safe(r.get(c))
            cell.data_type = "s"               # 一律當文字：不讓 "=..." 變成公式
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions


def _priority(r: dict, key: str) -> tuple:
    v = str(r.get(key) or "")
    return (ACTION_LANES.index(r.get("分流")) if r.get("分流") in ACTION_LANES else 9,
            int(v) if v.isdigit() else 99)


def write_global(runs: list[tuple[str, list[dict], dict]], out: Path, cfg: dict) -> dict:
    """好幾個國家的結果合成一份：總覽、每國一頁（可以直接跟進的）、需人工確認一頁。
    runs：(國家, 那次的 leads.csv 列, 那次的 run.json)。"""
    out.mkdir(parents=True, exist_ok=True)
    cols = columns(cfg)
    key = cfg["columns"]["products_priority_order"]
    wb = Workbook()
    ws = wb.active
    ws.title = "總覽"
    head = ["國家", "公司數", "找到官網", "官網比例"] + SHEETS + ["每家秒數", "名單來源"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    summary, manual = {}, []
    for country, rows, run in runs:
        n, web = len(rows), sum(1 for r in rows if r.get("官網"))
        lanes = {s: sum(r.get("分流") == s for r in rows) for s in SHEETS}
        summary[country] = {"公司數": n, "找到官網": web, **lanes}
        ws.append([country, n, web, f"{web / n:.0%}" if n else "", *lanes.values(),
                   str(run.get("平均每家秒數", "")), str(run.get("來源", ""))])
        act = sorted((r for r in rows if r.get("分流") in ACTION_LANES), key=lambda r: _priority(r, key))
        _sheet(wb, f"{country} 可跟進", cols, act)
        manual += [r for r in rows if r.get("分流") == "需人工確認"]
    _sheet(wb, "需人工確認（全部）", cols, manual)
    wb.save(out / "global_leads.xlsx")
    return summary

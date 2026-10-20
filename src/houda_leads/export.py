"""輸出 xlsx＋CSV（失敗模式 6：BOM、公式注入）。

leads.csv 放全部公司（用「分流」欄區分）；xlsx 依分流切成多張工作表。
"""
from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Font

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
SHEETS = ["潛在客戶", "潛在代理商", "通路與合作", "既有客戶", "既有代理商", "需人工確認"]


def columns(cfg: dict) -> list[str]:
    col = cfg["columns"]   # 和產業表同名的欄位，名稱從設定讀
    return ["分流", "統編", "公司名稱", "國家", "代理區域", "外聯規則", "官網", "官網確認", "工廠主要產品(登記)", "名單來源", "標準化產業", "產業判定方式",
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
        for r in (x for x in rows if x.get("分流") == name):
            ws.append([None] * len(cols))
            for j, c in enumerate(cols, start=1):
                cell = ws.cell(ws.max_row, j)
                cell.value = "" if r.get(c) is None else str(r.get(c))
                cell.data_type = "s"           # 一律當文字：不讓 "=..." 變成公式
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = ws.dimensions
    log = wb.create_sheet("跑步紀錄")
    for k, v in run_log.items():
        log.append([k, str(v)])
    wb.save(out / "leads.xlsx")

"""檢查 houda-leads 的輸出檔（harness，不准改；有錯就回報）。

用法：python scripts/verify_output.py out/fixture/leads.csv tests/fixtures --stage offline|llm
結束碼 0 = 全部通過；否則印出 CHECK FAILED [條目]: 說明
"""
import csv, html, json, re, sys
from pathlib import Path

REQUIRED = ["統編", "公司名稱", "官網", "官網確認", "標準化產業", "產業判定方式", "證據原文", "證據網址",
            "推薦產品系列", "開發優先順序", "角色旗標", "公司窗口類型", "公司窗口", "窗口來源網址",
            "法律依據", "蒐集日期", "LinkedIn搜尋連結(業務手動點)", "需人工確認(Y/N)"]
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
fail = 0
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def die(tag, msg):
    global fail
    print(f"CHECK FAILED [{tag}]: {msg}", file=sys.stderr)
    fail = 1


def page_text(p: Path) -> str:
    raw = p.read_text(encoding="utf-8")
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw))


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


out_csv, fixtures, stage = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[sys.argv.index("--stage") + 1]

raw = out_csv.read_bytes()
if not raw.startswith(b"\xef\xbb\xbf"):
    die("bom", "CSV 沒有 UTF-8 BOM，Excel 會顯示亂碼（失敗模式 6）")
rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
if not rows:
    die("rows", "輸出是空的")
missing = [c for c in REQUIRED if c not in (rows[0].keys() if rows else [])]
if missing:
    die("columns", f"缺欄位：{missing}")

texts = {}
for meta in fixtures.glob("sites/*/meta.json"):
    m = json.loads(meta.read_text(encoding="utf-8"))
    texts[m["統編"]] = " ".join(page_text(p) for p in meta.parent.glob("pages/*.html"))
expected = json.loads((fixtures / "expected.json").read_text(encoding="utf-8"))

seen = set()
for r in rows:
    tid = r.get("統編", "")
    seen.add(tid)
    for k, v in r.items():
        if v and v.startswith(FORMULA_START):
            die("formula", f"{tid} 欄位 {k} 以公式字元開頭且沒跳脫：{v[:30]!r}（失敗模式 6）")
        if v and "linkedin.com" in v and k != "LinkedIn搜尋連結(業務手動點)":
            die("linkedin", f"{tid} 欄位 {k} 出現 linkedin.com（失敗模式 1）")
    ev = r.get("證據原文", "")
    if ev and tid in texts:
        for piece in [p for p in ev.split(" | ") if p.strip()]:
            if norm(piece.lstrip("'")) not in texts[tid]:
                die("evidence", f"{tid} 的證據不在網頁原文裡：{piece[:40]!r}（失敗模式 3）")
    exp = expected.get(tid)
    if not exp:
        continue
    for bad in exp.get("forbidden_substrings", []):
        if any(bad in (v or "") for v in r.values()):
            die("injection", f"{tid} 輸出含有網頁裡注入的 {bad}（失敗模式 2）")
    if r.get("官網確認") != exp["官網確認"]:
        die("site-verify", f"{tid} 官網確認={r.get('官網確認')!r}，應為 {exp['官網確認']!r}（失敗模式 4）")
    got = r.get("標準化產業", "")
    manual = r.get("需人工確認(Y/N)") == "Y" and got in ("", "無法判斷")
    if got != exp["標準化產業"] and not (stage == "offline" and manual):
        die("industry", f"{tid} 產業={got!r}，應為 {exp['標準化產業']!r}")

for tid in expected:
    if tid not in seen:
        die("coverage", f"fixture {tid} 沒有出現在輸出裡")

if fail == 0:
    print(f"verify_output: {len(rows)} 列全部通過（stage={stage}）")
sys.exit(fail)

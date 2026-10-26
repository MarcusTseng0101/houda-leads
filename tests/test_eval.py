"""第 6 步 eval：成功定義的四個數字、每一類的混淆、給業務的空白表。合成資料，不連網。"""
import csv
import hashlib
import json

from openpyxl import load_workbook

from houda_leads import cli, evaluate

LEVELS = {"電源": "高", "照明": "中高"}


def cache_page(d, url, html):
    d.mkdir(parents=True, exist_ok=True)
    (d / (hashlib.sha256(url.encode()).hexdigest() + ".html")).write_text(html, encoding="utf-8")


def lead(tid, ind, lane="潛在客戶", confirm="統編", ev="", url=""):
    return {"統編": tid, "公司名稱": f"公司{tid}", "官網": url, "官網確認": confirm, "標準化產業": ind,
            "分流": lane, "證據原文": ev, "證據網址": url, "工廠主要產品(登記)": "281發電"}


def test_metrics_and_confusion(tmp_path):
    cache = tmp_path / "cache"
    cache_page(cache, "https://a.example/", "<html><body><p>本公司製造 UPS 不斷電系統。</p></body></html>")
    gold = {"1": {"industry": "電源", "yn": "Y"}, "2": {"industry": "電源", "yn": "N"},
            "3": {"industry": "照明", "yn": "Y"}, "4": {"industry": "照明", "yn": "Y"},
            "5": {"industry": "電源", "yn": "Y"}}
    leads = [lead("1", "電源", ev="本公司製造 UPS 不斷電系統。", url="https://a.example/"),
             lead("2", "電源"),
             lead("3", "電源"),                          # 判錯：照明 → 電源
             lead("4", "", lane="需人工確認"),           # 送人工：算錯
             lead("5", "電源", confirm="未確認")]         # 沒確認官網：不算產業正確率
    r = evaluate.evaluate(gold, leads, {"對禁止網域的請求": 0}, LEVELS, cache)
    assert r["industry_scored"] == 4 and r["industry_accuracy"] == 0.5
    assert r["per_industry"]["照明"] == {"n": 2, "correct": 0, "accuracy": 0.0,
                                        "confused_with": {"電源": 1, "（送人工，沒判）": 1}}
    assert r["high_priority_n"] == 4 and r["high_priority_precision"] == 0.75     # 1、2、3、5 列為高；Y 有 3 家
    assert r["evidence_not_in_source"] == 0 and r["denied_domain_requests"] == 0


def test_evidence_not_in_source_and_unverifiable(tmp_path):
    cache = tmp_path / "cache"
    cache_page(cache, "https://a.example/", "<html><body><p>我們做照明。</p></body></html>")
    leads = [lead("1", "電源", ev="這句不在網頁上", url="https://a.example/"),
             lead("2", "電源", ev="'=cmd 在快取裡沒有", url="https://none.example/")]
    bad, unver, ex = evaluate.check_evidence(leads, cache)
    assert (bad, unver) == (1, 1) and ex[0].startswith("1:")


def test_formula_escape_removed_before_comparing(tmp_path):
    cache = tmp_path / "cache"
    cache_page(cache, "https://a.example/", "<html><body><p>=SUM 是網頁上的字</p></body></html>")
    bad, _, _ = evaluate.check_evidence([lead("1", "電源", ev="'=SUM 是網頁上的字", url="https://a.example/")], cache)
    assert bad == 0


def test_label_sheet_is_blind_and_roundtrips(tmp_path, R):
    leads = [lead("12345678", "電源"), lead("'=1+1", "照明")]
    out = tmp_path / "labels.xlsx"
    assert evaluate.write_label_sheet(leads, R.industry_names(), out) == 2
    ws = load_workbook(out)["請標"]
    hdr = [c.value for c in ws[1]]
    col = hdr.index("標準化產業") + 1
    assert all(ws.cell(i, col).value in (None, "") for i in range(2, 4))     # 不放工具的判斷
    assert ws.cell(3, 1).value == "=1+1" and ws.cell(3, 1).data_type == "s"  # 文字，不是公式
    ws.cell(2, col).value = R.industry_names()[0]
    ws.cell(2, col + 1).value = "Y"
    ws.cell(3, col).value = "不是潛在客戶（不屬於任何一類）"
    ws.cell(3, col + 1).value = "N"
    ws.parent.save(out)
    g = tmp_path / "labels.csv"
    evaluate.xlsx_to_gold_csv(out, g)
    gold = evaluate.load_gold(g)
    assert gold["12345678"] == {"industry": R.industry_names()[0], "yn": "Y"}
    assert gold["=1+1"] == {"industry": "", "yn": "N"}


def test_eval_cli_writes_json(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    with (run / "leads.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(lead("1", "x")))
        w.writeheader()
        w.writerow(lead("1", "電源、能源與充電設備"))
    (run / "run.json").write_text(json.dumps({"對禁止網域的請求": 0}), encoding="utf-8")
    g = tmp_path / "g.csv"
    g.write_text("統編,標準化產業,是否真的潛在客戶(Y/N)\n1,電源、能源與充電設備,Y\n", encoding="utf-8-sig")
    out = tmp_path / "eval.json"
    assert cli.main(["eval", "--gold", str(g), "--json", str(out), "--leads", str(run / "leads.csv")]) == 0
    e = json.loads(out.read_text(encoding="utf-8"))
    assert e["industry_accuracy"] == 1.0 and e["high_priority_precision"] == 1.0
    assert set(e) >= {"industry_accuracy", "high_priority_precision", "evidence_not_in_source",
                      "denied_domain_requests", "per_industry"}

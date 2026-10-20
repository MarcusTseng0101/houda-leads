"""端到端：每一種分流都要跑得到（harness 的 3 個 fixture 都會分到「潛在客戶」）。"""
import datetime as dt
from pathlib import Path

import pytest

from houda_leads import cli, config, extract

DEMO = config.ROOT / "examples" / "demo_sites"
TODAY = dt.date(2026, 10, 20)


@pytest.fixture(scope="module")
def demo(R, cfg):
    return {r["統編"]: r for r in (cli.process(s, R, cfg, TODAY) for s in cli.load_fixture_sites(DEMO))}


def site(name, tax, body, url="https://co.example.tw/"):
    return {"meta": {"公司名稱": name, "統編": tax, "url": url},
            "pages": [extract.Page(url, f"<html><body><p>{name}</p><p>{body}</p></body></html>")]}


@pytest.mark.parametrize("tax,lane", [
    ("90000001", "潛在客戶"),
    ("90000002", "潛在代理商"),     # 經銷＋競品品牌
    ("90000003", "通路與合作"),     # EMS
    ("90000004", "需人工確認"),     # 半導體：依規格另行選型
    ("90000005", "需人工確認"),     # 網站提到自家公司
    ("90000006", "需人工確認"),     # 官網未確認
])
def test_demo_lanes(demo, tax, lane):
    assert demo[tax]["分流"] == lane, demo[tax]["備註"]


def test_main_lead_has_priority_and_follow_up(demo, cfg):
    r = demo["90000001"]
    assert r[cfg["columns"]["products_priority_order"]] == 1
    assert r["推薦產品系列"] and r["同規格替代(供參考)"]
    assert "台灣總代理" in r["建議跟進"]
    assert r["公司窗口類型"] == "業務信箱"


def test_distributor_records_competitor_brands(demo):
    r = demo["90000002"]
    assert "Dinkle" in r["提到的競品品牌"] and "Phoenix Contact" in r["提到的競品品牌"]
    assert r["開發優先順序"] == ""          # 通路不和設備製造商一起排


def test_self_mention_has_evidence(demo):
    assert "厚達" in demo["90000005"]["已提到自家公司"]


def test_spec_required_has_no_models(demo):
    r = demo["90000004"]
    assert r["推薦產品系列"] == "" and r["同規格替代(供參考)"] == ""


def test_public_customer_is_routed_out(R, cfg):
    r = cli.process(site("台達電子工業股份有限公司", "1", "電源供應器與 UPS 不斷電系統。"), R, cfg, TODAY)
    assert r["分流"] == "既有客戶" and "台達" in r["比對依據"]


def test_public_partner_is_routed_out(R, cfg):
    r = cli.process(site("承洧科技股份有限公司", "2", "電子零件買賣。"), R, cfg, TODAY)
    assert r["分流"] == "既有代理商"


def test_cli_run_writes_all_sheets(tmp_path):
    import openpyxl
    assert cli.main(["run", "--fixtures", str(DEMO), "--out", str(tmp_path), "--no-llm"]) == 0
    wb = openpyxl.load_workbook(tmp_path / "leads.xlsx")
    assert wb.sheetnames[:6] == ["潛在客戶", "潛在代理商", "通路與合作", "既有客戶", "既有代理商", "需人工確認"]
    assert "跑步紀錄" in wb.sheetnames


def test_source_mode_not_yet(tmp_path):
    assert cli.main(["run", "--source", "x.csv", "--out", str(tmp_path)]) == 2

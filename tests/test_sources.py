"""第 5 步：工廠登記讀檔。用合成資料（格式照 2026-09 的真檔，但不放真的負責人姓名）。"""
import csv

from houda_leads import sources

HDR = ["工廠名稱", "工廠登記編號", "工廠設立許可案號", "工廠地址", "工廠市鎮鄉村里", "工廠負責人姓名", "統一編號",
       "工廠組織型態", "工廠設立核准日期", "工廠登記核准日期", "工廠登記狀態", "產業類別", "主要產品"]


def write(tmp_path, rows, enc="utf-8-sig"):
    p = tmp_path / "f.csv"
    with p.open("w", encoding=enc, newline="") as f:
        w = csv.writer(f)
        w.writerow(HDR)
        w.writerows(rows)
    return p


def row(name, tax, org="股份有限公司", ind="28電力設備及配備製造業  ", prod="289其他電力設備及配備  ", status="生產中"):
    return [name, "X1", "", "新北市中和區某路1號", "新北市中和區某里", "王小明", tax, org, "", "1100101", status, ind, prod]


def test_filters_and_merges(tmp_path, cfg):
    p = write(tmp_path, [
        row("甲電機股份有限公司", "11111111"),
        row("甲電機股份有限公司二廠", "11111111", ind="29機械設備製造業", prod="293通用機械設備"),
        row("乙五金行", "22222222", org="獨資"),                                 # 獨資：排除
        row("丙食品股份有限公司", "33333333", ind="08食品製造業", prod="089其他食品"),   # 產業不符：排除
        row("丁電子股份有限公司", "44444444", status="歇業"),                      # 不是生產中：排除
    ])
    cs = sources.load_factory_registry(p, cfg)
    assert [c.tax_id for c in cs] == ["11111111"]
    c = cs[0]
    assert c.name == "甲電機股份有限公司"
    assert c.industry_codes == ["28", "29"]
    assert "293通用機械設備" in c.products and len(c.factories) == 2


def test_owner_name_never_kept(tmp_path, cfg):
    p = write(tmp_path, [row("甲電機股份有限公司", "11111111")])
    c = sources.load_factory_registry(p, cfg)[0]
    assert "王小明" not in repr(c)                     # 負責人姓名是個資，不讀


def test_big5_file(tmp_path, cfg):
    p = write(tmp_path, [row("甲電機股份有限公司", "11111111")], enc="cp950")
    assert sources.load_factory_registry(p, cfg)[0].name == "甲電機股份有限公司"


def test_company_name_from_factory_name():
    assert sources.company_name("台灣鉅邁股份有限公司汐止廠") == "台灣鉅邁股份有限公司"
    assert sources.company_name("煜益鋁業股份有限公司11號廠") == "煜益鋁業股份有限公司"
    assert sources.company_name("琮剛工業社一廠") == "琮剛工業社一廠"


def test_missing_column_is_loud(tmp_path, cfg):
    p = tmp_path / "bad.csv"
    p.write_text("工廠名稱,統一編號\nA,1\n", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="缺欄位"):
        sources.load_factory_registry(p, cfg)

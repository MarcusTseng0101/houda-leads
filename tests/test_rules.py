"""Build order 第 1 步：兩張表讀得進來、對得上。"""
import shutil

import pytest

from houda_leads import config, rules


def test_18_industries_and_tables_agree(R):
    assert len(R.industries) == 18
    assert set(R.industry_names()) == set(R.products)


def test_every_industry_has_keywords(R):
    for ind in R.industries:
        assert ind.keywords, ind.name


def test_single_char_keyword_is_skipped_and_reported(R):
    # 失敗模式 9：「主、被動元件」拆開會出現「主」
    assert any(kw == "主" for _, kw in R.skipped_keywords)
    for ind in R.industries:
        assert all(len(k.text) >= 2 or k.ascii_word for k in ind.keywords)


def test_spec_required_rows_have_no_series(R):
    sr = [p for p in R.products.values() if p.spec_required]
    assert len(sr) == 4
    assert all(p.series == () for p in sr)


def test_reference_series_exist_in_catalog(R):
    series = {c.series for c in R.catalog}
    for p in R.products.values():
        for s in p.series:
            assert s in series, (p.industry, s)      # match.py 用完全相同比對


def test_categories_mapped_to_catalog_names(R):
    cats = {c.category for c in R.catalog}
    for p in R.products.values():
        for c in p.categories:
            assert c in cats or c == "待確認", (p.industry, c)


def test_catalog_224_items(R):
    assert len(R.catalog) == 224


def test_public_lists_loaded(R):
    assert len(R.customers) == 16
    assert len(R.channel_partners) == 6
    assert any("Dinkle" in b.aliases for b in R.competitor_brands)


def test_mismatched_tables_raise(tmp_path, cfg):
    src = config.ROOT / "inputs"
    for p in src.glob("*.csv"):
        shutil.copy(p, tmp_path / p.name)
    kw = tmp_path / "industry_keywords.csv"
    kw.write_text(kw.read_text(encoding="utf-8").replace("醫療與牙科設備", "醫療設備"), encoding="utf-8")
    with pytest.raises(rules.RulesError, match="對不上"):
        rules.load(tmp_path, cfg)

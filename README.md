# houda-leads

厚達端子台的潛在客戶開發工具（本機 CLI）。從公開資料找設備廠，到對方官網看產品，依產業表配對推薦型號，
附公司窗口，輸出 Excel。**不爬 LinkedIn、不寄信。**

## 現在能做什麼（第 1～3 步）

```bash
uv sync
uv run houda-leads run --fixtures examples/demo_sites --out out/demo   # 6 家虛構公司的範例
uv run pytest -q
bash scripts/check.sh --stage offline                                  # 完成的定義
```

輸出 `out/demo/leads.xlsx`：潛在客戶／潛在代理商／通路與合作／既有客戶／既有代理商／需人工確認／跑步紀錄。

## 國外客戶

台灣公司從工廠登記來（`--source`）；國外公司沒有一份全世界的名單，業務自己準備一份 CSV：

```bash
uv run houda-leads run --list 國外名單.csv --out out/overseas --limit 20
```

欄位：`公司名稱,國家,官網`，可選 `註冊編號,來源`。國家用兩碼代碼（DE、JP、US）。
名單可以從展覽參展商名單、日本 gBizINFO、英國 Companies House 這類地方來，**每個來源的使用條款要先看過**。

輸出多三欄：`國家`、`代理區域`、`外聯規則`（`inputs/country_rules.csv`）。國外的潛在客戶，
建議跟進會寫「交給該區代理」（歐洲 Comet、美國 BlockMaster、日本 ABA、中國逸特），避免和代理商搶客戶。
`外聯規則` 是提醒，不是法律意見：德國、奧地利、韓國要先取得同意；寫「需確認」的寄之前問法務。

## 換一家公司用

改 `inputs/` 的表和 `config.toml`（表的欄位名稱、角色分類、自家公司名稱），不用改 `src/`。

## 文件
- `PROMPT.md`：規格（失敗模式、成功定義、build order）
- `RESEARCH.md`：法律、資安、名單來源、SaaS 評估
- `PROGRESS.md`：做到哪、猜的數字、待決定的事

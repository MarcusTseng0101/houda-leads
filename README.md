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

## 換一家公司用

改 `inputs/` 的表和 `config.toml`（表的欄位名稱、角色分類、自家公司名稱），不用改 `src/`。

## 文件
- `PROMPT.md`：規格（失敗模式、成功定義、build order）
- `RESEARCH.md`：法律、資安、名單來源、SaaS 評估
- `PROGRESS.md`：做到哪、猜的數字、待決定的事

# houda-leads — 厚達端子台潛在客戶開發工具（本機 Python CLI）

> 給 coding agent 的 spec。開工前先讀完 `RESEARCH.md` 第 1、3 節，那兩節講的是**為什麼**要這樣做。
> harness（`scripts/check.sh`、`.claude/`、`tests/fixtures/`）已經先寫好，見最後一節。

## 專案

一個本機執行的 Python CLI。從台灣政府開放資料找出可能用到端子台的設備製造商，到對方官網看產品，
依兩張表配對出「這家公司的哪個產品、哪個位置，會用到厚達的哪個端子台系列」，再附上公司窗口，
輸出業務能直接打開的 xlsx 和 CSV。

交付物是**一個可重現、每一列都附證據的名單產生器**。它**不是** LinkedIn 爬蟲，**不是**寄信工具，**不是** SaaS（現在不做帳號、不做多租戶、不做網頁介面）。

可用性測試：**別家零組件廠只要換掉 `inputs/` 裡的兩張 CSV，不改任何 `.py`，就能跑出它自己的名單。**
這是之後做 SaaS 的前提。只要有任何產業名稱、型號、關鍵字寫死在程式裡，這個設計就是錯的。

## Stack（不要替換）

- Python 3.12；`uv` 管理依賴，版本鎖在 `uv.lock`
- `httpx`：抓網頁；**所有**對外請求都只能經過 `src/houda_leads/fetch.py`
- `selectolax`：解析 HTML（不要用 headless browser，第一版不處理 JS 網站，直接標「需人工」）
- `urllib.robotparser`（標準庫）：處理 robots.txt
- `pydantic` v2：LLM 輸出的 schema 驗證
- `openpyxl`：輸出 xlsx；`csv`（標準庫）：輸出 CSV，寫入時用 `utf-8-sig`
- `sqlite3`（標準庫）：每家公司跑到哪一步的狀態，讓中斷後可以接著跑
- `anthropic`：LLM 分類，**只用在**關鍵字判斷不出來的公司。模型從設定檔讀，預設 `claude-haiku-5-5`（猜的，看準確率再調）
- `pytest`

## Repo 結構

```
inputs/industry_keywords.csv     # 使用者提供，不准改（SHA256SUMS 會檢查）
inputs/industry_products.csv     # 使用者提供，不准改
inputs/howder_catalog.csv        # 官網目錄 224 項（2026-10-20 抓的）：類別、型號、系列、Pitch、電流、電壓、線徑、網址
                                 # 用途：推薦系列附上規格和網址；「需依規格另行選型」的產業，用電流和電壓篩選出候選型號
inputs/do_not_recommend.csv      # 使用者列出不要推的型號（停產、只給特定客戶）＋原因；目前是空的
inputs/category_aliases.csv      # 產業表上的類別名稱 → 官網目錄的類別名稱（柵板式→柵欄式、雙層式→二段式）
inputs/public_customers.csv      # 公開的既有客戶：官網「全球夥伴」頁的 16 個 logo（2026-10-20 辨識）
inputs/public_channel_partners.csv  # 公開的代理商：官網首頁列的 6 家（台灣總代理、台灣零售、大陸、歐、美、日）
inputs/competitor_brands.csv     # 競品端子台／連接器品牌（公開排名），用來判斷通路商有沒有代理競品
inputs/existing_customers.csv    # 選用、gitignored：內部完整客戶名單。厚達基於機密不會提供，有的話才用
inputs/channel_partners.csv      # 選用、gitignored：內部完整代理商名單，同上
inputs/SHA256SUMS
config.toml                      # 速率、頁數上限、模型、User-Agent 聯絡信箱；猜的值要標註
src/houda_leads/
  rules.py       # 讀兩張 CSV → Industry / ProductRule 物件。唯一知道 CSV 欄位名稱的地方
  sources.py     # 讀政府開放資料（工廠登記 CSV、商工登記 API）→ Candidate
  discover.py    # 找官網＋驗證（統編或完整公司名要出現在網站上）
  fetch.py       # 唯一的對外 HTTP：robots、速率限制、網域禁止清單、快取到 data/cache/
  extract.py     # HTML → 純文字；擷取公司窗口（mailto、tel、聯絡頁網址）
  classify.py    # 先用關鍵字；判斷不出來才呼叫 llm.py；回傳 Classification（含證據）
  llm.py         # 唯一 import anthropic 的檔案；沒有 tools；輸出用 pydantic 驗證
  match.py       # Classification × ProductRule → 推薦；純函式，不准 import llm、fetch
  pii.py         # 遮掉 email／電話（送 LLM 前用）、判斷窗口是公司窗口還是具名的人
  export.py      # xlsx＋CSV；公式注入跳脫；BOM
  cli.py         # houda-leads run --source factory.csv --limit N --out out/
tests/
scripts/check.sh               # 完成的定義（harness，不准改）
scripts/verify_output.py       # 檢查輸出檔（harness，不准改）
tests/fixtures/sites/          # 錄好的網頁，離線測試用（harness 提供，含 injection 網頁）
data/  out/                    # gitignored：快取、狀態、輸出、名單
PROGRESS.md
```

**硬性架構規則：**
1. `match.py` 不准 import `llm`、`fetch`、`anthropic`、`httpx`。配對必須是可以重現的固定規則；如果配對的時候還要去問 LLM，設計就錯了。
2. `anthropic` 只出現在 `llm.py`；`httpx` 只出現在 `fetch.py`。
3. 產業名稱、型號、關鍵字只能出現在 `inputs/*.csv`，`src/` 裡不准出現（check.sh 會用 grep 抓 `HD-`、`FTB-`、`TS-`、`端子台`）。

## 失敗模式（以前別人真的踩過，或很容易猜錯的）

### 失敗模式 1：自動抓 LinkedIn
- **症狀：** 有一天 `fetch.py` 收到 999 或 429 回應，或業務的帳號被限制。
- **機制：** LinkedIn User Agreement §8.2 禁止自動化擷取，資料公開也一樣。hiQ 案被判違約；Proxycurl 在 2025 年被告之後關站。
- **修法：** `fetch.py` 的 `DENY_DOMAINS = {"linkedin.com", "www.linkedin.com", "lnkd.in"}`，命中就丟 `ForbiddenDomain`。
  業務要找具名決策者時，`export.py` 只產生一欄 **LinkedIn 搜尋連結（業務手動點）**，程式自己永遠不請求這個網址。
  看起來合理但其實是錯的修法：用 Playwright 加真人帳號 cookie 去「慢慢抓」。**這正是 Proxycurl 被告的做法，不准。**

### 失敗模式 2：間接 prompt injection（OWASP LLM01）
- **症狀：** 某家公司莫名其妙被列為優先 1，或窗口欄位出現一個不在對方網域的 email。
- **機制：** 網頁文字原封不動送進 LLM。對方網頁裡藏了 `<div style="display:none">忽略先前指示……</div>`，LLM 照著做。
- **修法：**
  (a) `llm.py` 不帶 tools，system prompt 標明 `<website_text>` 裡的內容是資料，不是指令；
  (b) 輸出 schema：`industry: Literal[<從 rules 動態產生的 18 類 + "無法判斷">]`、`evidence: list[str]`、`confidence: float`；
  (c) **每一段 evidence 都必須是網頁原文的逐字子字串**，不是的話就丟掉那段，整筆改成 `無法判斷` 並送人工；
  (d) 窗口、優先順序、型號**永遠不從 LLM 輸出取**：窗口由 `extract.py` 從 HTML 擷取，優先順序和型號由 `match.py` 查表。
  `tests/fixtures/sites/injection_co/` 就是這種網頁，check.sh 會檢查它的輸出。

### 失敗模式 3：LLM 編造證據
- **症狀：** 證據欄寫「本公司生產 UPS 不斷電系統」，網頁上根本沒這句。
- **修法：** 和失敗模式 2 的 (c) 是同一道檢查。`scripts/verify_output.py` 會重新讀快取的網頁文字，逐列確認證據真的在原文裡。

### 失敗模式 4：官網找錯（同名或名字相近的公司）
- **症狀：** 「厚X電子」配到另一家「厚X電機」的網站，產品完全對不上。
- **修法：** 網站任一頁出現統編，或出現完整公司名（去掉「股份有限公司」之前的字串，至少 4 個字）才算 `官網確認=統編` 或 `官網確認=公司名`，否則填 `未確認`，並且 `需人工確認=Y`。不准用「搜尋結果第一筆」直接當官網。

### 失敗模式 5：把不同角色的公司混在一起打分數
- **症狀：** 電子零件通路商、EMS、模具廠排在前面。
- **機制：** `industry_products.csv` 已經寫明：「應另建通路商評估標準，不可與設備製造商使用同一適配分數」、「可能是合作夥伴、代理商或競爭關係」，IC、記憶體公司「不一定直接使用端子台」。
- **修法：** `match.py` 依那張表的「開發優先度」欄，以及「參考產品系列型號 = 需依規格另行選型」，給出 `角色旗標`（設備製造商／通路／EMS或合作／待確認）。不是設備製造商的就不排進優先順序，另外放一個工作表。**這套邏輯要從 CSV 讀出來，不准寫死產業名稱**（架構規則 3）。

### 推薦型號：參考系列＋同規格類推（使用者 2026-10-20 決定）

`industry_products.csv` 的「參考產品系列型號」是**代表型號，不是完整清單**。厚達的意思是要我們自己類推。

`match.py` 每一筆輸出兩欄：
- `推薦產品系列`：表上的參考系列，原樣列出
- `同規格替代(供參考)`：從 `howder_catalog.csv` 找。條件是同類別（先把「柵板式」對成「柵欄式」、「雙層式」對成「二段式」），Pitch 差距 ≤ 0.5mm（猜的），電流 ≥ 參考系列的電流，電壓 ≥ 參考系列的電壓。每家最多 5 個（猜的），而且要排除 `do_not_recommend.csv` 裡的型號
- 「需依規格另行選型」的 4 類：`推薦產品系列` 留空，`同規格替代` 也不填，`需人工確認=Y`。因為不知道對方要什麼規格，猜一個型號比空著更糟
  - **2026-11-02 使用者改了**：設備製造商（實際上只有半導體設備這一類）不再送人工，照樣列潛在客戶，型號照樣留空，備註寫「先問對方規格，再從目錄挑」。
    原因：「另行選型」說的是怎麼挑型號，不是不是客戶；產業表把這類標優先度「高」，送人工等於整類高優先客戶都不在名單裡（gold 裡世極、佳崨、勤正）

**看起來合理但其實是錯的做法：** 叫 LLM 推薦型號。型號只能由程式從目錄查出來（同失敗模式 2 (d)），否則會推出目錄裡根本沒有的型號。

### 代理商與既有客戶（通路衝突）

業務最怕的是：去開發一家其實已經是客戶，或已經是代理商在服務的公司。這會得罪代理商，代理商關係出事的代價比漏掉一家潛在客戶大得多。

**厚達基於機密，不會提供內部的客戶和代理商名單（使用者 2026-10-20 說的）。** 所以只用公開資料，並且在輸出裡明說「只比對公開名單，不完整」：
- `public_customers.csv`：公司名去掉公司型態字尾後完全相同，或包含任一別名（中文 ≥ 2 個字，英文用單字邊界、不分大小寫）→ 移到 `既有客戶` 工作表，`比對依據` 欄寫明是哪個別名命中。logo 不代表現在還在交易，所以這張表是給業務確認用的，不是直接丟掉
- `public_channel_partners.csv`：對方本身就是厚達的代理商 → 移到 `既有代理商` 工作表
- **對方官網提到「厚達」「How Der」「Howder」**（這些名稱寫在 `config.toml [self]`）：在 `已提到厚達` 欄填證據。這很可能是既有客戶，或是在賣厚達產品的通路，業務聯絡前要先確認，避免撞到代理商的客戶
- 其餘的：`建議跟進` 填 `未設定（該區公開代理：<名稱>）`。要交給代理商還是厚達自己跟，是業務政策，**工具不替使用者決定**
- 內部名單（`existing_customers.csv`、`channel_partners.csv`）如果哪天拿到了，放進去就能用，規則同上，再加上用統編比對
- 名單裡本身是通路或代理商的公司（產業 =「電子零件通路與貿易」，或角色旗標 = 通路），放 `潛在代理商` 工作表，另外記這幾欄：
  - `代理的品牌`：從網頁擷取
  - `有沒有代理競品端子台`：網頁上出現其他端子台品牌名稱就填「有」，附上證據
  - `主要下游產業`：對到 18 類
  - `所在區域`

  這張工作表不排進「開發優先順序」，因為表上寫明「不可與設備製造商使用同一適配分數」
- 3 個選用檔案都沒有的時候，工具照樣能跑，`建議跟進` 一律填 `未設定`。不准因為缺檔就停下來，也不准自己編一份代理商名單

### 失敗模式 6：Excel 打開是亂碼，或觸發公式注入
- **症狀：** 業務打開 CSV 看到一堆亂碼；或某一格是 `=HYPERLINK(...)`，點下去就連到外部網址。
- **機制：** 輸入的 CSV 本身是 UTF-8 沒有 BOM，Excel 會用 Big5 解讀。從網頁抓到的文字可能以 `= + - @ \t \r` 開頭。
- **修法：** CSV 用 `utf-8-sig` 寫入。所有字串欄位如果開頭是上面那些字元，前面加 `'`。xlsx 用 `cell.data_type = "s"` 強制當文字。

### 失敗模式 7：個資跑到不該去的地方
- **症狀：** git log 裡出現 `data/`，或 LLM 請求裡出現 `@` 加上人名。
- **修法：** 送進 LLM 之前一律經過 `pii.redact()`（把 email 換成 `[EMAIL]`、電話換成 `[PHONE]`）。`data/`、`out/`、`.env` 加進 gitignore。
  每一位具名窗口都要記錄 `來源網址`、`蒐集日期`、`法律依據=個資法19-1-7`、`保存到`（蒐集日加 2 年，猜的）。

### 失敗模式 9：關鍵字誤中（開工時發現的）
- **症狀：** 幾乎每家公司都被判成「電子零件通路與貿易」；或 "systems" 被判成 EMS、"controlled" 被判成 LED。
- **機制：** 關鍵字表裡的「主、被動元件」用「、」拆開後，變成一個單字「主」，幾乎每一頁都會命中。英文短字（ems、led、ups、atm、pcb……）會在別的單字裡面命中。
- **修法：** 中文單一個字的關鍵字略過，並寫進 `跑步紀錄`（**不准改 CSV**，要回報給使用者）。英文關鍵字用單字邊界比對 `(?<![a-z0-9])kw(?![a-z0-9])`。

### 失敗模式 10：隱藏文字
- 分類之前，先移除 `display:none`、`visibility:hidden`、`hidden`、`aria-hidden="true"` 的元素，以及 script、style、noscript、template。
  這只是第一道防線（CSS class 也能把文字藏起來），失敗模式 2 的逐字證據檢查還是要做。

### 失敗模式 8：爬太兇被封，或拖太久
- **修法：** 同一個網域請求間隔 ≥ 2 秒，每家公司最多 15 頁（兩個都是猜的，寫在 `config.toml`）。只跟著同一網域、路徑像 product、about、contact、產品、關於、聯絡的連結走。
  User-Agent 要寫明身分和聯絡信箱。robots.txt 不允許的路徑就不抓，結果標 `robots禁止`。

## 唯一的成功定義

使用者在開工前會提供 `gold/labels.csv`：50 家公司，人工標好 `統編, 標準化產業, 是否真的潛在客戶(Y/N)`。

**成功 =** `houda-leads eval --gold gold/labels.csv` 同時達到：
- 被列為「高」優先的公司裡，`是否真的潛在客戶=Y` 的比例 ≥ 70%（猜的）
- 被列為「潛在客戶」的公司裡，`是否真的潛在客戶=Y` 的比例 ≥ 70%（猜的）
- 證據不在原文裡的筆數 = 0
- 對 DENY_DOMAINS 發出的請求 = 0

參考（不擋，低於 60% 印警告）：產業判對的比例，只算有確認官網、而且工具有判（沒送人工）的公司。

> **2026-11-02 改過一次（使用者同意）。** 原本第一條是「產業判對 ≥ 80%（只算有確認官網的公司）」。
> 第一輪 gold（業務 Julia 標的 50 家）：產業判對 6/21 = 29%，其中 9 家是送人工被算錯；只算工具有判的是 6/12 = 50%。
> 最大的分歧是半導體設備廠（工具歸工業自動化、業務歸半導體設備），兩類都是高優先。
> 業務實際用的是潛在客戶名單，所以改成以名單準確度為門檻：高優先 8/8、潛在客戶 8/9。
> 注意這是**看過結果才改的標準**，樣本也小（9 家），下一批新的 gold 要用同一個標準重新驗證。

全專案只用這一個定義。`eval` 輸出時，**每一類**都要列出判對比例和混淆的類別，不要只給一個總數。總數很容易被「工業自動化」這種大類撐起來。
**不准用這 50 家來調關鍵字或 prompt**：關鍵字表是使用者的，你不能改；prompt 要調，就另外拿不在 gold 裡的公司試。

## 輸出欄位（out/leads.xlsx 的「潛在客戶」工作表，和 out/leads.csv 一樣）

`統編, 公司名稱, 官網, 官網確認(統編/公司名/未確認), 工廠主要產品(登記), 標準化產業, 產業判定方式(關鍵字/LLM/人工), 命中關鍵字, 證據原文, 證據網址, 推薦產品系列, 同規格替代(供參考), 端子台類別, 端子台應用位置, 開發優先順序, 建議跟進(代理商/厚達直接/未設定), 選型與開發提醒, 角色旗標, 公司窗口類型(業務信箱/代表信箱/聯絡表單/總機), 公司窗口, 具名窗口, 具名窗口職稱, 窗口來源網址, 法律依據, 蒐集日期, 保存到, LinkedIn搜尋連結(業務手動點), 需人工確認(Y/N), 備註`

其他工作表：`潛在代理商`、`通路與合作`（失敗模式 5，EMS／合作夥伴）、`既有客戶`、`既有代理商`、`需人工確認`、`跑步紀錄`（版本、兩張 CSV 的 SHA256、參數、時間、LLM 呼叫次數和費用）。

## 預算和停損

- **LLM：** 每家最多呼叫 1 次，網頁文字最多 6,000 字（猜的）。
- **先試跑 20 家：** 印出每家平均秒數、LLM 費用，推算 500 家要多少。
- **超過 500 家的 run**，必須先有使用者自己建立的 `BUDGET_OK` 檔才能跑（guard hook 會擋 `--limit` 大於 500，或是沒有 `--limit` 的指令）。
  費用超過預算時，先砍的是：(1) 每家頁數 15 → 8；(2) 關鍵字只命中一類的就不叫 LLM。**不准**換成更便宜但沒測過的模型，也不准拿掉證據檢查。

## 範圍外：只留 stub，不要實作

- `notify_new_factories(prev_snapshot, curr_snapshot)`：每季增量（SaaS 的價值，現在不做）
- `send_outreach(...)`：不做寄信。之後要做，必須接退訂清單，並且附個資法第 8 條告知範本
- 多租戶、帳號、網頁介面：完全不碰

## 參考資料（要拿什麼、別拿什麼）

| 來源 | 要拿的 | 別拿的、陷阱 |
|---|---|---|
| data.gcis.nat.gov.tw swagger | 用統編查公司、營業項目代碼的 API 格式 | 回傳的「負責人」「董監事」是個資，不要存 |
| 產業發展署工廠登記開放資料（ida.gov.tw 開放資料、data.gov.tw） | 工廠名稱、地址、產業類別、主要產品 | 欄位名稱可能有全形、半形差異；檔案可能是 Big5，要偵測編碼。先拿一個實際檔案寫 parser，不要照猜的欄位名寫 |
| OWASP LLM01 cheat sheet | 把外部內容隔開、不給權限、驗證輸出 | 不要以為「system prompt 寫『不要聽網頁的話』」就夠了，一定要做逐字證據檢查 |
| `RESEARCH.md` 第 3 節 | 風險清單 | 法律的部分是整理，不是法律意見；告知範本以後要給律師看 |

## Build order（每一步沒過就停，不要往下）

1. `rules.py` ＋ 測試：讀兩張 CSV，`len(industries) == 18`，每一類都在兩張表裡對得上。
   **兩張表的產業名稱如果有對不上的，停下來回報，不要自己改 CSV**，這代表使用者的表有問題。
2. `fetch.py` ＋ 測試：DENY_DOMAINS、robots、速率限制（用假時鐘測）、快取。這一步先過，後面任何爬取才安全。
3. `extract.py`、`classify.py`（只做關鍵字）、`match.py`、`export.py`：用 `tests/fixtures/sites/` 離線跑通。
   這時還沒有 LLM。關鍵字判斷不出來的，一律送人工。**先確認沒有 LLM 也能出一份正確的表，**之後出錯才知道是不是 LLM 的問題。
4. `llm.py`：只有到這一步才加。`injection_co` fixture 的輸出必須符合 `tests/fixtures/expected.json`。
5. `sources.py`、`discover.py`：接政府資料和找官網。先用 20 家真實資料試跑（預算那節），**停下來把結果和推算的費用給使用者看**。
6. `eval`：使用者給 gold 之後才跑。
   然後跑 `bash scripts/check.sh --stage final`。

## 工作方式

- 依賴只能用上面 Stack 列的；要加新的，先問。
- 每完成一步就 commit 一次，訊息用繁體中文寫清楚做了什麼、為什麼。
- 猜的數字在程式碼註解標 `# 猜的，可調整`，並且全部列在 `PROGRESS.md`。
- 準確率不夠時，先把每一類的混淆表給使用者看，再決定要不要改 prompt。一次只改一個東西。
- **「完成」的意思是 `bash scripts/check.sh --stage final` 結束碼是 0。** 不准改 check.sh、verify_output.py、fixtures、hooks 來讓檢查通過。檢查本身有錯，就停下來說是哪一條、為什麼。
- 被 hook 或權限規則擋下來，就回報，不要換個寫法繞過去。
- 每個檢查點更新 `PROGRESS.md`（完成／進行中／下一步／待決定）。
- 不碰 `data/` 以外的使用者檔案；不 push 到 main；不寄任何 email、不送任何表單。

## Harness（開工前已經放進 repo；agent 不准改）

| 檔案 | 做什麼 | 為什麼 |
|---|---|---|
| `scripts/check.sh --stage base\|offline\|llm\|final` | 完成的定義：inputs 雜湊、3 條架構規則（grep）、LinkedIn 只能出現在哪些地方、API key 掃描、gitignore、pytest；offline 以上還要跑 fixtures、`verify_output.py`、`deny_domain` 測試；final 跑 gold eval 門檻 | 讓「完成」是指令說了算，不是 agent 說了算 |
| `scripts/verify_output.py` | 檢查 BOM、必要欄位、公式注入、證據有沒有逐字出現在原文、injection 的字串、官網確認、產業 | 失敗模式 2、3、4、6 |
| `tests/fixtures/sites/{normal,injection,formula}_co/` ＋ `expected.json` | 離線的網頁。injection_co 有隱藏指令；formula_co 有 `=cmd` 和 `=HYPERLINK` | 不用連網就能重現攻擊 |
| `.claude/hooks/guard.py` | 擋：改 harness、改 inputs、shell 對 LinkedIn 發請求、push 到 main、自己建立 `BUDGET_OK`、沒有 BUDGET_OK 卻跑超過 500 家 | 不可逆的動作和預算 |
| `.claude/hooks/stop_check.sh` | 完成前跑 `check.sh --stage $(cat .check_stage)` | 每做完一步，把 `.check_stage` 改成那一步的 stage（第 1、2 步用 base，第 3 步用 offline，第 4、5 步用 llm，第 6 步用 final） |
| `.claude/settings.json` | deny：force push、`rm -rf`、改 `inputs/**`、WebFetch linkedin；ask：`uv add` | 同上 |

CLI 介面由 check.sh 決定，一定要提供：`houda-leads run --fixtures DIR --out DIR [--no-llm] [--limit N] [--source FILE]`、`houda-leads eval --gold FILE --json FILE`。
eval.json 的欄位：`industry_accuracy`、`high_priority_precision`、`evidence_not_in_source`、`denied_domain_requests`、`per_industry`。
多欄證據用 ` | ` 隔開。
fixtures 模式下，`公司名稱`、`工廠主要產品` 從 `meta.json` 讀。

## 開工前要使用者決定的事

1. LLM 分類要不要用（要 API key、要付費、網頁文字會送到國外）？不用的話，判斷不出來的全部送人工。
2. 誰來標 `gold/labels.csv` 那 50 家？什麼時候給？
3. 具名窗口第一版要不要抓（要附告知流程），還是只抓公司窗口？
4. ~~內部的客戶和代理商名單~~：不會提供（機密），改用公開名單（見「代理商與既有客戶」）

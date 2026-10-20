"""PreToolUse：保護 harness、擋 LinkedIn、擋沒批准的大量執行。exit 2 = 擋下並把原因告訴 agent。"""
import json, os, re, sys

call = json.load(sys.stdin)
tool, args = call.get("tool_name", ""), call.get("tool_input", {})


def block(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


PROTECTED = ("scripts/check.sh", "scripts/verify_output.py", "tests/fixtures/", ".claude/", "inputs/", "BUDGET_OK")
path = args.get("file_path", "").replace("\\", "/")
if tool in ("Edit", "Write") and any(p in path for p in PROTECTED):
    block(f"{path} 是 harness 或使用者的輸入，不准改。檢查有錯就停下來回報是哪一條。")

cmd = args.get("command", "")
if tool == "Bash":
    if any(p in cmd for p in PROTECTED) and re.search(r"(>|sed -i|rm |mv |cp |touch |tee )", cmd):
        block("不准用 shell 寫入 harness、inputs/ 或建立 BUDGET_OK（只有使用者能建立）。")
    if re.search(r"linkedin\.com|lnkd\.in", cmd, re.I):
        block("不准對 LinkedIn 發請求（失敗模式 1）。具名窗口只產生給業務手動點的搜尋連結。")
    if re.search(r"git\s+push\b.*\b(main|master)\b", cmd):
        block("不准 push 到 main。")
    if re.search(r"houda-leads\s+run\b", cmd) and "--fixtures" not in cmd:
        m = re.search(r"--limit\s+(\d+)", cmd)
        if (m is None or int(m.group(1)) > 500) and not os.path.exists("BUDGET_OK"):
            block("超過 500 家（或沒有 --limit）的 run 需要 BUDGET_OK。先跑 --limit 20，回報推算的時間和費用，等使用者批准。")
sys.exit(0)

#!/usr/bin/env bash
# houda-leads 的完成定義。結束碼 0 = 這個 stage 承諾的都做到了。
# 用法：bash scripts/check.sh --stage base|offline|llm|final
# 不准改這個檔案來讓檢查通過；檢查有錯就停下來回報。
set -u
stage=base
[ "${1:-}" = "--stage" ] && stage="${2:-base}"
fail=0
die() { echo "CHECK FAILED [$1]: $2" >&2; fail=1; }
py() { uv run python "$@"; }

# ── base：每個 stage 都檢查 ──
(cd inputs && sha256sum -c --quiet SHA256SUMS) || die "inputs" "inputs/*.csv 被改過（使用者的表不准改）"

grep -nE "^\s*(import|from)\s+(houda_leads\.)?(llm|fetch)|anthropic|httpx" src/houda_leads/match.py \
  && die "arch-1" "match.py 不准碰 llm／fetch／anthropic／httpx"
grep -rlE "^\s*(import|from)\s+anthropic" src | grep -v "src/houda_leads/llm.py" \
  && die "arch-2" "anthropic 只能出現在 llm.py"
grep -rlE "^\s*(import|from)\s+httpx" src | grep -v "src/houda_leads/fetch.py" \
  && die "arch-2" "httpx 只能出現在 fetch.py"
grep -rnE "(HD|FTB|TS|HB|HI|HA)-[0-9]|端子台|配電盤|電源供應器" src \
  && die "arch-3" "產業名稱／型號／關鍵字寫死在 src/（只能在 inputs/*.csv）"
grep -rn --exclude-dir=__pycache__ "linkedin" src | grep -vE "src/houda_leads/(fetch|export)\.py" \
  && die "linkedin" "只有 fetch.py（禁止清單）和 export.py（手動搜尋連結）可以提到 linkedin"

grep -rnE "sk-ant-[A-Za-z0-9]" --exclude-dir=.git --exclude-dir=.venv . && die "secret" "repo 裡有 API key"
for p in data out .env inputs/existing_customers.csv inputs/channel_partners.csv; do
  git check-ignore -q "$p" || die "gitignore" "$p 沒有被 gitignore（失敗模式 7）"
done
[ -z "$(git ls-files data out .env inputs/existing_customers.csv inputs/channel_partners.csv)" ] || die "pii-git" "data/、out/、.env 或客戶／代理商名單被 commit 了"

uv run pytest -q >/dev/null 2>&1 || die "pytest" "單元測試沒過（uv run pytest -q）"
[ -f PROGRESS.md ] || die "progress" "缺 PROGRESS.md"

# ── offline：fixtures 離線跑通（第 3 步之後）──
if [ "$stage" != base ]; then
  vstage=llm; llmflag=""
  [ "$stage" = offline ] && { vstage=offline; llmflag="--no-llm"; }
  rm -rf out/fixture
  uv run houda-leads run --fixtures tests/fixtures/sites --out out/fixture $llmflag >/dev/null 2>&1 \
    || die "run" "houda-leads run --fixtures 跑失敗"
  [ -f out/fixture/leads.xlsx ] || die "xlsx" "缺 out/fixture/leads.xlsx"
  [ -f out/fixture/leads.csv ] && py scripts/verify_output.py out/fixture/leads.csv tests/fixtures --stage $vstage \
    || die "verify" "verify_output.py 沒過（看上面的 CHECK FAILED）"
  uv run pytest -q tests -k "deny_domain" 2>&1 | grep -q passed \
    || die "deny-test" "缺少或沒通過 deny_domain 測試（fetch 要擋 linkedin.com）"
fi

# ── final：gold set 驗收 ──
if [ "$stage" = final ]; then
  [ -f gold/labels.csv ] || die "gold" "缺 gold/labels.csv（使用者要標 50 家）"
  [ -f gold/labels.csv ] && { uv run houda-leads eval --gold gold/labels.csv --json out/eval.json >/dev/null 2>&1 \
    || die "eval" "houda-leads eval 跑失敗"; }
  [ -f out/eval.json ] && py - <<'EOF' || die "eval-threshold" "沒達到成功定義（PROMPT.md「唯一的成功定義」）"
import json, sys
e = json.load(open("out/eval.json", encoding="utf-8"))
ok = e["industry_accuracy"] >= 0.80 and e["high_priority_precision"] >= 0.70 \
     and e["evidence_not_in_source"] == 0 and e["denied_domain_requests"] == 0
print(json.dumps(e, ensure_ascii=False, indent=1)); sys.exit(0 if ok else 1)
EOF
fi

[ $fail -eq 0 ] && echo "ALL CHECKS PASS (stage=$stage)"
exit $fail

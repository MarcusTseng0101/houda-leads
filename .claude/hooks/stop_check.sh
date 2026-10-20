#!/usr/bin/env bash
# 沒通過 check.sh 不准說完成。stage 讀 .check_stage（agent 每做完一步改這個檔；預設 base）。
input=$(cat)
echo "$input" | grep -q '"stop_hook_active": *true' && exit 0
stage=$(cat .check_stage 2>/dev/null || echo base)
out=$(bash scripts/check.sh --stage "$stage" 2>&1) && exit 0
echo "還沒完成：scripts/check.sh --stage $stage 沒過：" >&2
echo "$out" | tail -20 >&2
echo "修好原因，或停下來告訴使用者哪一條檢查有錯。不准改檢查。" >&2
exit 2

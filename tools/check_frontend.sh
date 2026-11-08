#!/usr/bin/env bash
# 確認版控裡的前端產物跟 web/ 的原始碼一致（避免改了原始碼忘記 build）。
# 用法：tools/check_frontend.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v node >/dev/null 2>&1; then
  echo "⚠ 沒有 node，跳過前端一致性檢查"
  exit 0
fi

node web/stamp.mjs --check

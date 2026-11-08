#!/usr/bin/env bash
# 起整組（vidpipe + vidgrab），兩邊都帶各自 git 的 build number。
# 網頁左上角會顯示 build；每次 commit 都會變，確認瀏覽器載到的是新版。
#
#   ./tools/compose-up.sh            # = docker compose up -d --build
#   ./tools/compose-up.sh logs -f    # 其他 compose 指令照轉
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

stamp() {  # stamp <目錄> → "commit數.短sha"，不是 git 就回 dev
  local n s
  if n=$(git -C "$1" rev-list --count HEAD 2>/dev/null) && s=$(git -C "$1" rev-parse --short HEAD 2>/dev/null); then
    echo "$n.$s"
  else
    echo dev
  fi
}

set -a; [ -f .env ] && . ./.env; set +a
export VIDPIPE_BUILD="${VIDPIPE_BUILD:-$(stamp .)}"
export VIDGRAB_BUILD="${VIDGRAB_BUILD:-$(stamp "${VIDGRAB_SRC:-../vidgrab}")}"
echo "▸ vidpipe build $VIDPIPE_BUILD · vidgrab build $VIDGRAB_BUILD"

if [ $# -eq 0 ]; then set -- up -d --build; fi
exec docker compose "$@"

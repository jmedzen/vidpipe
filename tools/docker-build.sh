#!/usr/bin/env bash
# 建 vidpipe 映像，並把 git 的 build number（commit 數.短 sha）帶進去。
#
#   ./tools/docker-build.sh              # vidpipe:latest
#   ./tools/docker-build.sh vidpipe:v1   # 自訂 tag
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TAG="${1:-vidpipe:latest}"
BUILD="${VIDPIPE_BUILD:-$(git rev-list --count HEAD 2>/dev/null).$(git rev-parse --short HEAD 2>/dev/null)}"
[ "$BUILD" = "." ] && BUILD=dev

echo "▸ 建置 $TAG（build $BUILD）"
docker build --build-arg "VIDPIPE_BUILD=$BUILD" -t "$TAG" .

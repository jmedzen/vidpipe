# vidpipe — Shorts 流水線
#
#   ./tools/docker-build.sh
#   docker run --rm -v "$PWD/work:/data/work" -v "$PWD/out:/data/out" vidpipe version
#
# 網頁介面（容器裡必須綁 0.0.0.0，所以要 --allow-lan 與 token；對外範圍由 -p 決定）：
#   docker run --rm -e VIDPIPE_API_TOKEN="$(openssl rand -hex 24)" -p 127.0.0.1:8320:8320 \
#     -v "$PWD/work:/data/work" vidpipe serve --host 0.0.0.0 --allow-lan --no-open
#
# 網頁介面＋vidgrab＋連主機 audio.cpp 的完整組合：docker compose up -d（見 docker-compose.yml）
#
# ffmpeg 是渲染引擎；fonts-noto-cjk 讓燒進影片的中文字幕不會變成方塊。
# Docker 內連不到主機的 127.0.0.1：vidgrab / audio.cpp 的位址用 VIDPIPE_VIDGRAB_URL、
# VIDPIPE_TTS_BASE_URL 指到實際 IP（NAS 部署時 audio.cpp 在 Mac 的區網 IP 上）。
FROM python:3.12-slim

ARG VIDPIPE_BUILD=dev
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIDPIPE_BUILD=${VIDPIPE_BUILD} \
    VIDPIPE_WORK_DIR=/data/work \
    VIDPIPE_OUT_DIR=/data/out \
    VIDPIPE_CONFIG=/data/config/vidpipe.json

# fontconfig：ffmpeg 的 subtitles 濾鏡靠它找字體；fonts-noto-cjk 含 Noto Sans CJK TC（繁中）
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk fontconfig tini \
 && fc-cache -f \
 && fc-match "Noto Sans CJK TC" | grep -qi "Noto Sans CJK" \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install . \
 && useradd -m -u 1000 vidpipe \
 && mkdir -p /data/work /data/out /data/config \
 && chown -R vidpipe /data

USER vidpipe
VOLUME ["/data/work", "/data/out", "/data/config"]
EXPOSE 8320
ENTRYPOINT ["/usr/bin/tini", "--", "vidpipe"]
CMD ["version"]

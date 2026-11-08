# vidpipe — Shorts 流水線
#
#   docker build --build-arg VIDPIPE_BUILD="$(git rev-list --count HEAD).$(git rev-parse --short HEAD)" -t vidpipe .
#   docker run --rm -v "$PWD/work:/data/work" -v "$PWD/out:/data/out" vidpipe version
#
# ffmpeg 是渲染引擎；fonts-noto-cjk 讓燒進影片的中文字幕不會變成方塊。
# Docker 內連不到主機的 127.0.0.1：vidgrab / audio.cpp 的位址用 VIDPIPE_VIDGRAB_URL、
# VIDPIPE_TTS_BASE_URL 指到 host.docker.internal 或實際 IP。
FROM python:3.12-slim

ARG VIDPIPE_BUILD=dev
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIDPIPE_BUILD=${VIDPIPE_BUILD} \
    VIDPIPE_WORK_DIR=/data/work \
    VIDPIPE_OUT_DIR=/data/out

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install . \
 && useradd -m -u 1000 vidpipe \
 && mkdir -p /data/work /data/out \
 && chown -R vidpipe /data

USER vidpipe
VOLUME ["/data/work", "/data/out"]
ENTRYPOINT ["vidpipe"]
CMD ["version"]

from __future__ import annotations

import shutil

import pytest
from mediagen import make
from vgfake import FakeVidgrab

from vidpipe import assets
from vidpipe.clients import VidgrabClient
from vidpipe.config import Config
from vidpipe.errors import BadInput, NotFound, UpstreamError

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")


@pytest.fixture
def video(tmp_path):
    return make(tmp_path / "src.mp4", "color=c=red:s=320x240", audio=True, seconds=1.0)


@pytest.fixture
def vg(fake_server, video):
    return FakeVidgrab(fake_server, video.read_bytes())


def cfg_for(tmp_path, fake_server, **kw):
    return Config(work_dir=str(tmp_path / "work"), vidgrab_url=fake_server.url, **kw)


def resolve(clip, cfg, fake_server, duration=3.0):
    def make_client(c):
        return VidgrabClient(c.vidgrab_url, c.vidgrab_token, retries=0, sleep=lambda s: None)

    return assets.resolve(clip, duration, cfg, where="scenes[0].clip", make=make_client)


# ------------------------------------------------------------------ 本機檔案

def test_no_clip_means_nothing_to_resolve(tmp_path, fake_server):
    assert resolve(None, cfg_for(tmp_path, fake_server), fake_server) is None
    assert resolve({}, cfg_for(tmp_path, fake_server), fake_server) is None


def test_local_path_clip_keeps_start_and_records_the_file_as_the_source(
        tmp_path, fake_server, video):
    r = resolve({"path": str(video), "start": 0.5}, cfg_for(tmp_path, fake_server), fake_server)
    assert (r.path, r.offset) == (str(video), 0.5)
    assert r.credit == {"kind": "file", "path": str(video.resolve())}
    assert resolve({"path": str(video)}, cfg_for(tmp_path, fake_server), fake_server).offset == 0.0


def test_missing_or_non_video_local_files_are_reported_with_the_scene_path(
        tmp_path, fake_server):
    cfg = cfg_for(tmp_path, fake_server)
    with pytest.raises(NotFound, match=r"scenes\[0\]\.clip.*nope\.mp4"):
        resolve({"path": str(tmp_path / "nope.mp4")}, cfg, fake_server)
    audio = make(tmp_path / "a.m4a", "color=c=red:s=64x64", audio=True, seconds=1.0)
    # 有影像的檔案才算數；這裡用沒有影像軌的檔案
    import subprocess
    only_audio = tmp_path / "only.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-vn", "-c:a", "aac",
                    str(only_audio)], check=True)
    with pytest.raises(BadInput, match="沒有影像軌"):
        resolve({"path": str(only_audio)}, cfg, fake_server)


# ------------------------------------------------------------------ vidgrab 素材：已有的片段

def test_unknown_asset_is_not_found_with_a_hint(tmp_path, fake_server, vg):
    with pytest.raises(NotFound, match="pixabay:404") as err:
        resolve({"asset_id": "pixabay:404"}, cfg_for(tmp_path, fake_server), fake_server)
    assert "search" in err.value.hint and vg.batches == []


def test_an_existing_clip_that_covers_the_range_is_downloaded_once_and_reused(
        tmp_path, fake_server, vg):
    vg.add(clips=[vg.clip("pixabay:1", 2, 12)])
    cfg = cfg_for(tmp_path, fake_server)
    r = resolve({"asset_id": "pixabay:1", "start": 5}, cfg, fake_server, duration=3.0)
    assert r.offset == 3.0                                  # 片段從 2 秒開始：5 − 2
    assert r.path.startswith(str(assets.cache_dir(cfg))) and vg.batches == []
    assert r.credit["author"] == "Ann" and r.credit["license"]["label"] == "可自由使用"
    assert r.credit["clip_id"] == "pixabay:1#2-12" and r.credit["kind"] == "asset"
    again = resolve({"asset_id": "pixabay:1", "start": 5}, cfg, fake_server, duration=3.0)
    assert again.path == r.path and len(vg.downloads) == 1  # 第二次走快取


def test_the_shortest_covering_clip_wins_and_a_full_download_covers_everything(
        tmp_path, fake_server, vg):
    vg.add(clips=[vg.clip("pixabay:1", None, None), vg.clip("pixabay:1", 0, 15),
                  vg.clip("pixabay:1", 4, 9)])
    r = resolve({"asset_id": "pixabay:1", "start": 5}, cfg_for(tmp_path, fake_server),
                fake_server, duration=3.0)
    assert r.credit["clip_id"] == "pixabay:1#4-9" and r.offset == 1.0
    # 12–15 還在 0–15 的片段裡；14–17 超出了，只有整支涵蓋
    r = resolve({"asset_id": "pixabay:1", "start": 14}, cfg_for(tmp_path, fake_server),
                fake_server, duration=3.0)
    assert r.credit["clip_id"] == "pixabay:1#full" and r.offset == 14.0


def test_a_clip_id_is_used_as_is_with_start_relative_to_the_clip(tmp_path, fake_server, vg):
    vg.add(clips=[vg.clip("pixabay:1", 2, 12)])
    r = resolve({"asset_id": "pixabay:1#2-12", "start": 1.5}, cfg_for(tmp_path, fake_server),
                fake_server)
    assert r.offset == 1.5 and vg.batches == []
    vg.items["pixabay:1"]["clips"][0]["exists"] = False
    with pytest.raises(NotFound, match="不見了"):
        resolve({"asset_id": "pixabay:1#2-12"}, cfg_for(tmp_path, fake_server), fake_server)


def test_a_readable_local_path_is_used_directly_without_downloading(
        tmp_path, fake_server, vg, video):
    clip = vg.clip("pixabay:1", 2, 12, path=str(video), size=video.stat().st_size)
    vg.add(clips=[clip])
    r = resolve({"asset_id": "pixabay:1", "start": 2}, cfg_for(tmp_path, fake_server),
                fake_server)
    assert r.path == str(video) and vg.downloads == []
    # 大小對不上（同一個路徑上其實是別的檔案）→ 不能信，改用下載
    clip["size_bytes"] = video.stat().st_size + 1
    r = resolve({"asset_id": "pixabay:1", "start": 2}, cfg_for(tmp_path, fake_server),
                fake_server)
    assert r.path != str(video) and len(vg.downloads) == 1


def test_token_is_sent_on_every_request(tmp_path, fake_server, vg):
    vg.add(clips=[vg.clip("pixabay:1", 0, 10)])
    resolve({"asset_id": "pixabay:1"}, cfg_for(tmp_path, fake_server, vidgrab_token="s3cret"),
            fake_server)
    assert vg.headers and all(h.get("authorization") == "Bearer s3cret" for h in vg.headers)


# ------------------------------------------------------------------ vidgrab 素材：要去抓

def test_missing_range_is_requested_from_vidgrab_with_a_margin_and_no_frames(
        tmp_path, fake_server, vg):
    vg.add(duration=30.0)
    cfg = cfg_for(tmp_path, fake_server)
    r = resolve({"asset_id": "pixabay:1", "start": 3.0}, cfg, fake_server, duration=4.0)
    (req,) = vg.batches
    assert req["project"] == "proj" and req["asset_ids"] == ["pixabay:1"]
    assert req["ranges"] == [[3.0, 7.5]]                    # 3 + 4 + 0.5 的餘裕
    assert req["frames"] is False and req["wait"] is True
    assert r.offset == 0.0 and len(vg.downloads) == 1 and r.credit["clip_id"] == "pixabay:1#3-7.5"


def test_the_requested_range_is_clamped_to_the_asset_length(tmp_path, fake_server, vg):
    vg.add(duration=6.0)
    resolve({"asset_id": "pixabay:1", "start": 3.0}, cfg_for(tmp_path, fake_server),
            fake_server, duration=4.0)
    assert vg.batches[0]["ranges"] == [[3.0, 6.0]]
    # 第二次：素材只有 6 秒，已抓的 3–6 就是能拿到的全部，不該再抓
    resolve({"asset_id": "pixabay:1", "start": 3.0}, cfg_for(tmp_path, fake_server),
            fake_server, duration=4.0)
    assert len(vg.batches) == 1


def test_start_beyond_the_asset_is_rejected_before_asking_vidgrab(tmp_path, fake_server, vg):
    vg.add(duration=10.0)
    with pytest.raises(BadInput, match="超過素材長度"):
        resolve({"asset_id": "pixabay:1", "start": 10.0}, cfg_for(tmp_path, fake_server),
                fake_server)
    assert vg.batches == []


def test_an_idempotent_batch_with_no_new_clips_falls_back_to_the_existing_one(
        tmp_path, fake_server, vg):
    item = vg.add(duration=30.0)
    existing = vg.clip("pixabay:1", 3, 7.5, exists=False)  # 蓋不到：檔案不在

    def on_batch(body):
        existing["exists"] = True   # 這次「抓」其實是確認它在，沒有新片段
        item["clips"].append(existing)
        return 200, {"schema": "vidgrab.batch/1", "timed_out": False,
                     "results": [{"asset_id": "pixabay:1", "new_clips": [], "error": None}]}

    item["clips"].append(existing)
    vg.on_batch = on_batch
    r = resolve({"asset_id": "pixabay:1", "start": 3.0}, cfg_for(tmp_path, fake_server),
                fake_server, duration=4.0)
    assert r.credit["clip_id"] == "pixabay:1#3-7.5" and len(vg.batches) == 1


def test_a_batch_error_is_an_upstream_error_that_can_be_retried(tmp_path, fake_server, vg):
    vg.add(duration=30.0)
    vg.on_batch = lambda body: (200, {"timed_out": False, "results": [
        {"asset_id": "pixabay:1", "new_clips": [], "error": "HTTP 403: expired"}]})
    with pytest.raises(UpstreamError, match="expired") as err:
        resolve({"asset_id": "pixabay:1", "start": 1}, cfg_for(tmp_path, fake_server),
                fake_server)
    assert err.value.retryable and err.value.upstream == "vidgrab"
    vg.on_batch = lambda body: (200, {"timed_out": True, "results": [
        {"asset_id": "pixabay:1", "new_clips": [], "error": None}]})
    with pytest.raises(UpstreamError, match="超時"):
        resolve({"asset_id": "pixabay:1", "start": 1}, cfg_for(tmp_path, fake_server),
                fake_server)


def test_a_batch_that_outlives_the_wait_is_polled_until_done(tmp_path, fake_server, vg):
    vg.add(duration=30.0)
    clip = vg.clip("pixabay:1", 1, 4.5)

    def on_batch(body):
        vg.items["pixabay:1"]["clips"].append(clip)
        return 202, {"job": "j1", "done": False}

    vg.on_batch = on_batch
    vg.jobs = [{"done": False}, {"done": True, "result": {
        "timed_out": False, "results": [{"new_clips": [clip], "error": None}]}}]
    r = resolve({"asset_id": "pixabay:1", "start": 1.0}, cfg_for(tmp_path, fake_server),
                fake_server, duration=3.0)
    assert r.credit["clip_id"] == "pixabay:1#1-4.5" and vg.jobs == []


def test_a_failed_polled_job_is_reported(tmp_path, fake_server, vg):
    vg.add(duration=30.0)
    vg.on_batch = lambda body: (202, {"job": "j1", "done": False})
    vg.jobs = [{"done": True, "error": "yt-dlp 壞了"}]
    with pytest.raises(UpstreamError, match="yt-dlp 壞了"):
        resolve({"asset_id": "pixabay:1", "start": 1.0}, cfg_for(tmp_path, fake_server),
                fake_server)


def test_download_failures_leave_no_partial_file(tmp_path, fake_server, vg):
    vg.add(clips=[vg.clip("pixabay:1", 0, 10)])
    original = vg.handle

    def broken(method, path, headers, body):
        if path.startswith("/file/"):
            return 500, {"error": "disk on fire"}
        return original(method, path, headers, body)

    fake_server.handler = broken
    cfg = cfg_for(tmp_path, fake_server)
    with pytest.raises(UpstreamError):
        resolve({"asset_id": "pixabay:1"}, cfg, fake_server)
    root = assets.cache_dir(cfg)
    assert not root.exists() or not list(root.iterdir())

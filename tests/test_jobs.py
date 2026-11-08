from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mediagen import make

import vidpipe
from vidpipe import cli, ffmpeg, jobs
from vidpipe.errors import BadInput, NotFound

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")


# ------------------------------------------------------------ 記錄本身

def test_success_records_state_and_events(tmp_path):
    with jobs.JobRecorder(tmp_path, "demo", args={"x": 1}) as job:
        job.stage("encode", "第一步")
        job.progress(0.5)
        job.set_result({"path": "a.mp4"})
        job.log("hello")
    state = jobs.get_job(tmp_path, job.id)
    assert state["status"] == "succeeded" and state["progress"] == 1.0
    assert state["args"] == {"x": 1} and state["result"] == {"path": "a.mp4"}
    assert [s["name"] for s in state["stages"]] == ["encode"]
    assert state["stages"][0]["status"] == "succeeded" and state["finished_at"]
    events = jobs.read_events(tmp_path, job.id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert [e["kind"] for e in events] == ["start", "stage", "progress", "log", "end"]
    assert jobs.read_events(tmp_path, job.id, after=3) == events[3:]


def test_failure_records_structured_error_and_reraises(tmp_path):
    with pytest.raises(BadInput):
        with jobs.JobRecorder(tmp_path, "demo") as job:
            job.stage("s1")
            raise BadInput("壞掉了", hint="換一個")
    state = jobs.get_job(tmp_path, job.id)
    assert state["status"] == "failed" and state["error"]["code"] == "bad_input"
    assert state["error"]["hint"] == "換一個" and state["stages"][0]["status"] == "failed"
    assert jobs.read_events(tmp_path, job.id)[-1]["kind"] == "error"


def test_unexpected_exception_becomes_internal_error(tmp_path):
    with pytest.raises(RuntimeError):
        with jobs.JobRecorder(tmp_path, "demo") as job:
            raise RuntimeError("boom")
    assert jobs.get_job(tmp_path, job.id)["error"]["code"] == "internal"


def test_cancelled_and_keyboard_interrupt_are_cancelled(tmp_path):
    for exc in (jobs.Cancelled("x"), KeyboardInterrupt()):
        with pytest.raises(type(exc)):
            with jobs.JobRecorder(tmp_path, "demo") as job:
                job.stage("s")
                raise exc
        state = jobs.get_job(tmp_path, job.id)
        assert state["status"] == "cancelled" and state["stages"][0]["status"] == "cancelled"


def test_module_helpers_are_noops_without_a_recorder(tmp_path):
    assert jobs.active() is None
    jobs.stage("x")
    jobs.progress(0.5)
    jobs.log("nothing")
    with jobs.JobRecorder(tmp_path, "demo") as job:
        assert jobs.active() is job
        jobs.stage("seen")
    assert jobs.active() is None
    assert jobs.get_job(tmp_path, job.id)["stage"] == "seen"


def test_progress_is_throttled_but_final_value_is_written(tmp_path):
    with jobs.JobRecorder(tmp_path, "demo") as job:
        job.stage("s")
        for i in range(1, 100):
            job.progress(i / 100)  # 迴圈很快，不該每次都寫檔
        job.progress(1.0)
    events = [e for e in jobs.read_events(tmp_path, job.id) if e["kind"] == "progress"]
    assert 1 <= len(events) <= 12 and events[-1]["ratio"] == 1.0


def test_disabled_recorder_writes_nothing(tmp_path):
    with jobs.JobRecorder(tmp_path, "demo", enabled=False) as job:
        job.stage("s")
    assert not (tmp_path / "_jobs").exists() and job.enabled is False


def test_unwritable_work_dir_does_not_break_the_command(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("我是檔案不是資料夾")
    with jobs.JobRecorder(blocker, "demo") as job:
        job.stage("s")
        job.progress(0.3)
    assert job.enabled is False


# ------------------------------------------------------------ 讀取

def test_list_newest_first_with_status_filter_and_limit(tmp_path):
    ids = []
    for status in ("ok", "bad", "ok"):
        try:
            with jobs.JobRecorder(tmp_path, "demo") as job:
                if status == "bad":
                    raise BadInput("x")
        except BadInput:
            pass
        ids.append(job.id)
        time.sleep(1.05)  # id 以秒為單位排序；測試裡要讓時間往前走
    listed = jobs.list_jobs(tmp_path)
    assert [j["id"] for j in listed] == list(reversed(ids))
    assert [j["status"] for j in jobs.list_jobs(tmp_path, status="failed")] == ["failed"]
    assert len(jobs.list_jobs(tmp_path, limit=2)) == 2
    with pytest.raises(BadInput, match="狀態"):
        jobs.list_jobs(tmp_path, status="nope")
    assert jobs.list_jobs(tmp_path / "empty") == []


def test_ids_cannot_escape_the_jobs_dir(tmp_path):
    (tmp_path / "secret.json").write_text("{}")
    for bad in ("../secret", "..", "x" * 5, "20260101-000000-zzzzzz", ""):
        with pytest.raises(BadInput):
            jobs.get_job(tmp_path, bad)
        with pytest.raises(BadInput):
            jobs.read_events(tmp_path, bad)
    with pytest.raises(NotFound):
        jobs.get_job(tmp_path, "20260101-000000-abcdef")


def test_broken_files_are_skipped(tmp_path):
    (tmp_path / "_jobs").mkdir()
    (tmp_path / "_jobs" / "20260101-000000-abcdef.json").write_text("{not json")
    (tmp_path / "_jobs" / "20260101-000001-abcdef.json").write_text("[]")
    (tmp_path / "_jobs" / "junk.json").write_text("{}")
    assert jobs.list_jobs(tmp_path) == []


def _fake_running(tmp_path, pid, host=None):
    job_id = "20260101-000000-abcdef"
    (tmp_path / "_jobs").mkdir(exist_ok=True)
    state = {"id": job_id, "kind": "demo", "status": "running", "pid": pid,
             "host": host or platform.node(), "stages": [], "args": {}}
    (tmp_path / "_jobs" / f"{job_id}.json").write_text(json.dumps(state))
    return job_id


def test_dead_process_is_reported_as_interrupted(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    job_id = _fake_running(tmp_path, dead.pid)
    assert jobs.get_job(tmp_path, job_id)["status"] == "interrupted"
    assert jobs.list_jobs(tmp_path, status="interrupted")[0]["id"] == job_id
    # 別台機器上的工作無從確認，維持 running
    other = _fake_running(tmp_path, dead.pid, host="some-other-host")
    assert other == job_id
    assert jobs.get_job(tmp_path, job_id)["status"] == "running"


def test_cancel_refuses_unrelated_processes(tmp_path):
    victim = subprocess.Popen(["sleep", "60"])
    try:
        job_id = _fake_running(tmp_path, victim.pid)  # pid 被重用的情境：這支不是 vidpipe
        with pytest.raises(jobs.Conflict, match="vidpipe 行程"):
            jobs.request_cancel(tmp_path, job_id)
        assert victim.poll() is None  # 沒被殺
    finally:
        victim.kill()
        victim.wait()


def test_cancel_refuses_finished_and_remote_jobs(tmp_path):
    with jobs.JobRecorder(tmp_path, "demo") as done:
        pass
    with pytest.raises(jobs.Conflict, match="不在執行中"):
        jobs.request_cancel(tmp_path, done.id)
    remote = _fake_running(tmp_path, os.getpid(), host="some-other-host")
    with pytest.raises(jobs.Conflict, match="別台機器"):
        jobs.request_cancel(tmp_path, remote)


def test_cancel_signals_a_real_vidpipe_process(tmp_path):
    # 行程的指令列含 "vidpipe" 才會被認定是我們的
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "vidpipe-fake"])
    try:
        job_id = _fake_running(tmp_path, proc.pid)
        deadline = time.time() + 5
        while time.time() < deadline and not jobs._is_vidpipe_process(proc.pid):
            time.sleep(0.05)
        jobs.request_cancel(tmp_path, job_id)
        proc.wait(timeout=10)
        assert proc.returncode == -signal.SIGTERM
        assert (tmp_path / "_jobs" / f"{job_id}.cancel").exists()
    finally:
        if proc.poll() is None:
            proc.kill()


# ------------------------------------------------------------ ffmpeg 進度與 CLI

@needs_ffmpeg
def test_ffmpeg_run_reports_progress_to_the_active_job(tmp_path):
    src = make(tmp_path / "in.mp4", "testsrc=s=320x240", audio=False, seconds=3)
    with jobs.JobRecorder(tmp_path / "work", "demo") as job:
        job.stage("encode")
        ffmpeg.run(["-i", str(src), "-c:v", "libx264", "-preset", "ultrafast",
                    str(tmp_path / "o.mp4")], duration=3.0)
    ratios = [e["ratio"] for e in jobs.read_events(tmp_path / "work", job.id)
              if e["kind"] == "progress"]
    assert ratios and ratios[-1] == 1.0 and ratios == sorted(ratios)


@needs_ffmpeg
def test_ffmpeg_failure_with_progress_keeps_the_error_tail(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"nope")
    with jobs.JobRecorder(tmp_path / "work", "demo"):
        with pytest.raises(ffmpeg.RenderError, match="ffmpeg 失敗"):
            ffmpeg.run(["-i", str(bad), str(tmp_path / "o.mp4")], duration=2.0)


@needs_ffmpeg
def test_cli_records_a_job_for_long_commands(tmp_path, capsys):
    src = make(tmp_path / "in.mp4", "testsrc=s=320x240", audio=True, seconds=2)
    assert cli.main(["normalize", str(src), "-o", str(tmp_path / "o.mp4")]) == 0
    out = json.loads(capsys.readouterr().out)
    state = jobs.get_job(tmp_path / "work", out["job"])
    assert state["status"] == "succeeded" and state["kind"] == "normalize"
    assert state["args"]["fit"] == "blur_pad" and state["result"]["width"] == 1080
    assert [s["name"] for s in state["stages"]] == ["encode"]

    assert cli.main(["normalize", str(tmp_path / "nope.mp4"), "-o", str(tmp_path / "x.mp4")]) == 1
    capsys.readouterr()
    failed = jobs.list_jobs(tmp_path / "work", status="failed")
    assert len(failed) == 1 and failed[0]["error"]["code"] == "not_found"


@needs_ffmpeg
def test_cli_jobs_job_and_opt_out(tmp_path, capsys, monkeypatch):
    src = make(tmp_path / "in.mp4", "testsrc=s=320x240", audio=False, seconds=1)
    cli.main(["trim", str(src), "-o", str(tmp_path / "t.mp4"), "--duration", "0.5"])
    job_id = json.loads(capsys.readouterr().out)["job"]
    assert cli.main(["jobs", "--limit", "5"]) == 0
    listed = json.loads(capsys.readouterr().out)["jobs"]
    assert [j["id"] for j in listed] == [job_id]
    assert cli.main(["job", job_id]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["job"]["kind"] == "trim" and body["events"][0]["kind"] == "start"
    assert cli.main(["job", "../x"]) == 2
    capsys.readouterr()
    assert cli.main(["cancel", job_id]) == 1  # 已經結束的不能取消
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "conflict"

    monkeypatch.setenv("VIDPIPE_RECORD_JOBS", "false")
    cli.main(["trim", str(src), "-o", str(tmp_path / "t2.mp4"), "--duration", "0.5"])
    assert json.loads(capsys.readouterr().out)["job"] is None
    assert len(jobs.list_jobs(tmp_path / "work")) == 1


@needs_ffmpeg
def test_sigterm_cancels_a_running_command_and_leaves_no_partial_file(tmp_path):
    # 長一點的來源，確保殺它的時候還在編碼
    long_src = make(tmp_path / "long.mp4", "testsrc2=s=1280x720", audio=False, seconds=40)
    out = tmp_path / "out.mp4"
    src_root = str(Path(vidpipe.__file__).resolve().parents[1])  # 測試會 chdir，不能用相對路徑
    env = {**os.environ, "VIDPIPE_WORK_DIR": str(tmp_path / "work"),
           "PYTHONPATH": os.pathsep.join(
               filter(None, [src_root, os.environ.get("PYTHONPATH", "")]))}
    env.pop("VIDPIPE_CONFIG", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "vidpipe", "normalize", str(long_src), "-o", str(out),
         "--fit", "blur_pad"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        state = None
        deadline = time.time() + 60
        while time.time() < deadline and proc.poll() is None:
            listed = jobs.list_jobs(tmp_path / "work")
            if listed and (listed[0].get("progress") or 0) > 0.03:
                state = listed[0]
                break
            time.sleep(0.2)
        err = proc.stderr.read() if proc.poll() is not None else ""
        assert state is not None, f"工作沒有回報進度；stderr={err}"
        # 真的走 request_cancel：確認行程、送 SIGTERM
        jobs.request_cancel(tmp_path / "work", state["id"])
        stdout, _ = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    final = jobs.get_job(tmp_path / "work", state["id"])
    assert final["status"] == "cancelled" and final["error"]["code"] == "cancelled"
    assert json.loads(stdout)["error"]["code"] == "cancelled"
    assert not out.exists() and [p for p in tmp_path.iterdir() if ".partial" in p.name] == []

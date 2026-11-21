"""작업 큐: 제작(대본→이미지→영상화→음성→렌더→카피)을 순서대로 실행하며 실시간 진행률·로그를 남긴다."""
from __future__ import annotations

import json
import queue
import threading
import traceback
from datetime import datetime
from pathlib import Path

from . import audio, copywriter, evidence, kling, llm, planner, render, settings, sfx, tts

_queue: "queue.Queue[str]" = queue.Queue()
_jobs: dict[str, dict] = {}
_lock = threading.RLock()
_worker_started = False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _progress_file(pid: str) -> Path:
    return planner.project_dir(pid) / "logs" / "progress.json"


def log_line(pid: str, msg: str) -> None:
    d = planner.project_dir(pid) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n"
    with open(d / "run.log", "a", encoding="utf-8") as f:
        f.write(line)
    job = _find_job(pid)
    if job is not None:
        job.setdefault("logs", []).append(line.strip())
        job["logs"] = job["logs"][-200:]


def _find_job(pid: str) -> dict | None:
    for j in _jobs.values():
        if j.get("project_id") == pid and j.get("status") == "running":
            return j
    return None


def set_progress(pid: str, pct: float, stage: str) -> None:
    with _lock:
        data = {"pct": round(min(max(pct, 0), 100), 1), "stage": stage, "updated_at": _now()}
        p = _progress_file(pid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        job = _find_job(pid)
        if job is not None:
            job["pct"] = data["pct"]
            job["stage"] = stage


def is_busy(pid: str) -> bool:
    j = _jobs.get(pid)
    return bool(j and j.get("status") in ("queued", "running"))


def get_progress(pid: str) -> dict:
    p = _progress_file(pid)
    job = _find_job(pid)
    data = {"pct": 0, "stage": "대기"}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    if job is not None:
        data.update({"pct": job.get("pct", data.get("pct")), "stage": job.get("stage", data.get("stage"))})
    data["running"] = job is not None
    log = planner.project_dir(pid) / "logs" / "run.log"
    if log.exists():
        try:
            data["log"] = log.read_text(encoding="utf-8").splitlines()[-120:]
        except Exception:
            data["log"] = []
    else:
        data["log"] = []
    return data


def _start_worker() -> None:
    global _worker_started
    with _lock:
        if _worker_started:
            return
        _worker_started = True
        t = threading.Thread(target=_loop, daemon=True, name="tastypop-worker")
        t.start()


def _loop() -> None:
    while True:
        pid = _queue.get()
        job = _jobs.get(pid)
        if job is None:
            continue
        job["status"] = "running"
        job["started_at"] = _now()
        set_progress(pid, 1, "시작")
        try:
            job["fn"]()
            job["status"] = "done"
            job["pct"] = 100
            set_progress(pid, 100, "완료")
            log_line(pid, "✅ 작업 완료")
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)
            log_line(pid, f"❌ 오류: {e}")
            job["trace"] = traceback.format_exc()[-2000:]
            set_progress(pid, job.get("pct", 0), f"오류: {e}")
        finally:
            job["finished_at"] = _now()
            _queue.task_done()


def enqueue(pid: str, fn, kind: str = "render") -> dict:
    _start_worker()
    _jobs[pid] = {"project_id": pid, "status": "queued", "kind": kind, "pct": 0,
                  "stage": "대기열", "created_at": _now(), "fn": fn, "logs": []}
    _queue.put(pid)
    return {"job": pid, "status": "queued"}


def job_status(pid: str) -> dict | None:
    j = _jobs.get(pid)
    if not j:
        return None
    return {k: v for k, v in j.items() if k not in ("fn", "trace")}


# ------------------------------------------------------------------ 유스케이스
def prepare_media(project: dict, progress=None, log=None, generate_images: bool = True,
                  generate_videos: bool = True) -> dict:
    """완전자동 모드: 대본→키프레임→영상화."""
    pid = project["id"]
    cfg = settings.load_settings()
    s = cfg["models"]
    d = planner.project_dir(pid)

    if generate_images:
        have = [p for p in sorted((d / "kf").glob("*.png"))]
        todo = [c for c in project["cuts"] if not (d / c["kf"]).exists()]
        if todo and not settings.key("gemini"):
            raise llm.LLMError(
                "Gemini API 키가 없어 이미지를 자동 생성할 수 없습니다.\n"
                "① [설정]에서 Gemini 키를 넣거나 ② 0원 협업 모드로 `이미지_요청서.txt` 를 받아 kf/ 폴더에 이미지를 넣어 주세요."
            )
        prompts = planner.scene_prompts(project)
        total = len(todo) or 1
        for i, c in enumerate(todo):
            out = d / c["kf"]
            prompt = prompts[c["id"] - 1]
            if log:
                log(f"키프레임 생성 {c['id']:02d} …")
            p, model = llm.generate_image(prompt, out)
            if log:
                log(f"키프레임 {p.name} 완료 ({model})")
            if progress:
                progress(10 + 25 * (i + 1) / total, f"키프레임 {i+1}/{total}")

    if generate_videos and project.get("mode") == "auto":
        todo = [c for c in project["cuts"] if not (d / c["clip"]).exists()]
        if todo and not (settings.key("kling_access_key") and settings.key("kling_secret_key")):
            if log:
                log("Kling 키가 없어 영상화는 건너뜁니다(키프레임 그대로 렌더).")
        else:
            total = len(todo) or 1
            for i, c in enumerate(todo):
                kf = d / c["kf"]
                if not kf.exists():
                    continue
                prompt = f"{c.get('motion') or 'slow cinematic push-in'}, appetizing, seamless loop motion"
                if log:
                    log(f"영상화 {c['id']:02d} 요청 …")
                kling.image_to_video(
                    kf, prompt, d / c["clip"],
                    duration=5,
                    on_progress=lambda st, msg: log(f"  └ Kling {c['id']:02d}: {st} {msg}") if log else None,
                )
                if log:
                    log(f"영상화 {c['id']:02d} 완료")
                if progress:
                    progress(40 + 20 * (i + 1) / total, f"영상화 {i+1}/{total}")
    return project


def finish_project(project: dict, dialogue: list[dict] | None = None) -> dict:
    pid = project["id"]
    set_progress(pid, 60, "음성 합성")
    if project["content_type"] == "drama4" and dialogue is None:
        d = planner.project_dir(pid)
        log_line(pid, "성우별 대사 합성 시작")
        dialogue = tts.synthesize_dialogue(project["cuts"], d / "voice")
        project["dialogue_meta"] = [{"index": x["index"], "dur": x["dur"], "ko": x["ko"],
                                     "en": x["en"], "speaker": x["speaker"]} for x in dialogue]
        project["subtitles"] = tts.subtitle_timings(dialogue, project["cuts"])
        planner.save_project(project)
        log_line(pid, f"대사 {sum(1 for x in dialogue if x['wav'])}개 합성 · 영어 자막 {len(project['subtitles'])}줄")
    out = render.render(project, dialogue=dialogue,
                        progress=lambda p, s: set_progress(pid, p, s), log=lambda m: log_line(pid, m))
    set_progress(pid, 97, "카피·썸네일")
    copywriter.build_all(project)
    copywriter.make_thumbnail(project)
    log_line(pid, "유튜브·인스타 카피와 썸네일 생성 완료")
    set_progress(pid, 98, "증빙 백업")
    try:
        evidence.build(planner.load_project(pid), log=lambda m: log_line(pid, m))
    except Exception as e:
        log_line(pid, f"증빙 백업 경고: {e}")
    return out


def run_full(pid: str, generate_images: bool = True, generate_videos: bool = True,
             auto_script: bool = False, script_extra: str = "") -> dict:
    """프로젝트 제작 전체 실행(큐에 넣고 즉시 반환)."""
    project = planner.load_project(pid)

    def fn():
        p = planner.load_project(pid)
        set_progress(pid, 3, "대본")
        if auto_script:
            log_line(pid, f"대본 생성 중 ({settings.load_settings()['models']['script_provider']})")
            text = llm.generate_text(planner.script_prompt(p, script_extra), planner.SCRIPT_SYSTEM)
            script = llm.extract_json(text)
            planner.apply_script(p, script)
            p = planner.load_project(pid)
            log_line(pid, f"대본 반영 · 훅: {p['hook']['text']}")
        prepare_media(p, progress=lambda x, s: set_progress(pid, x, s), log=lambda m: log_line(pid, m),
                      generate_images=generate_images, generate_videos=generate_videos)
        p = planner.load_project(pid)
        finish_project(p)

    return enqueue(pid, fn, kind="full")


def run_render(pid: str) -> dict:
    def fn():
        p = planner.load_project(pid)
        finish_project(p)

    return enqueue(pid, fn, kind="render")


def ensure_sfx() -> dict:
    return sfx.ensure_library()


def bundle(project_ids: list[str], title: str = "") -> dict:
    """주말 몰아보기 합본: 완성편들을 이어 붙여 롱폼 mp4 생성."""
    projects = [planner.load_project(x) for x in project_ids]
    base = planner.project_dir(project_ids[0])
    out_dir = settings.PROJECTS_DIR / "bundle"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for p in projects:
        f = planner.project_dir(p["id"]) / "out" / "final.mp4"
        if f.exists():
            files.append(f)
    if len(files) < 2:
        raise render.RenderError("합본할 완성 영상이 2개 이상 필요합니다.")
    lst = out_dir / "list.txt"
    lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
    name = title or f"몰아보기_{datetime.now().strftime('%m%d')}.mp4"
    out = settings.PROJECTS_DIR / "bundle" / planner.sanitize_filename(name)
    from . import ffmpeg_tools

    cmd = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)]
    rc, o = ffmpeg_tools.run(cmd, timeout=1200)
    if rc != 0:
        raise render.RenderError(f"합본 실패: {o[-400:]}")
    return {"file": str(out), "count": len(files)}

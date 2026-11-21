"""일일 자동 예약: 정해진 시간에 1편을 자동 제작(그리고 원하면 예약 업로드까지).

- 대시보드가 켜져 있을 때: 내부 스레드가 1분마다 점검
- 맥북이 꺼져 있어도 매일 돌게 하려면: macOS launchd 등록([⏰ launchd 등록] 버튼)
기본값은 꺼짐(OFF). 대시보드에서 직접 켜야 동작한다.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import jobs, planner, settings, youtube

TOPIC_POOL = [
    "불닭 치즈 떡볶이", "김치찌개 끓이는 밤", "붕어빵 겨울 간식", "마라탕 3단계 맵기",
    "치킨 + 맥주 조합", "비빔밥 5색 재료", "핫도그 치즈 폭탄", "김밥 456",
    "짜장면 후루룩", "탕후루 딸기", "삼겹살 불판", "라면 계란 2개",
]

_state_file = settings.CONFIG_DIR / "scheduler_state.json"
_stop = threading.Event()
_thread: threading.Thread | None = None


def _load_state() -> dict:
    if _state_file.exists():
        try:
            return json.loads(_state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_run": None, "last_project": None, "last_error": None}


def _save_state(s: dict) -> None:
    settings.ensure_dirs()
    _state_file.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def status() -> dict:
    s = settings.load_settings()["scheduler"]
    st = _load_state()
    return {
        "enabled": bool(s.get("enabled")),
        "time": s.get("time", "07:00"),
        "weekdays": s.get("weekdays", [0, 1, 2, 3, 4, 5, 6]),
        "content_type": s.get("content_type", "asmr3d"),
        "auto_upload": bool(s.get("auto_upload")),
        "upload_hour": s.get("upload_hour", 19),
        "last_run": st.get("last_run"),
        "last_project": st.get("last_project"),
        "last_error": st.get("last_error"),
        "running": bool(_thread and _thread.is_alive()),
        "launchd": launchd_status(),
    }


def _pick_topic() -> str:
    projects = planner.list_projects()
    used = {p.get("topic") for p in projects}
    for t in TOPIC_POOL:
        if t not in used:
            return t
    return TOPIC_POOL[len(projects) % len(TOPIC_POOL)]


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hh, mm = [int(x) for x in str(value).split(":")[:2]]
        return hh, mm
    except Exception:
        return 7, 0


def _due_today(s: dict, now: datetime) -> bool:
    days = s.get("weekdays")
    if days is None:
        days = [0, 1, 2, 3, 4, 5, 6]
    if int(now.weekday()) not in [int(d) for d in days]:
        return False
    hh, mm = _parse_time(s.get("time", "07:00"))
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return now >= due


def _already_ran_today() -> bool:
    last = _load_state().get("last_run")
    if not last:
        return False
    try:
        return datetime.fromisoformat(last).date() == datetime.now().date()
    except Exception:
        return False


def daily_job(force: bool = False) -> dict:
    """launchd/수동 1회 실행용: 동기적으로 1편 제작 후 종료."""
    s = settings.load_settings()["scheduler"]
    now = datetime.now()
    if not force:
        if not s.get("enabled"):
            return {"ok": False, "message": "자동 예약이 OFF 입니다."}
        if _already_ran_today():
            return {"ok": False, "message": "오늘은 이미 실행했습니다."}
        if not _due_today(s, now):
            return {"ok": False, "message": "아직 예약 시간/요일이 아닙니다."}

    projects = planner.list_projects()
    next_ep = s.get("next_ep") or (max([p.get("ep") or 0 for p in projects] + [7]) + 1)
    topic = _pick_topic()
    project = planner.create_project(
        topic=topic,
        content_type=s.get("content_type", "asmr3d"),
        mode="auto" if settings.key("gemini") else "semi",
        ep=int(next_ep),
    )
    pid = project["id"]
    jobs.log_line(pid, f"launchd 자동 실행 · {now:%Y-%m-%d %H:%M}")
    try:
        p = planner.load_project(pid)
        if settings.key("gemini"):
            from . import llm

            text = llm.generate_text(planner.script_prompt(p), planner.SCRIPT_SYSTEM)
            planner.apply_script(p, llm.extract_json(text))
            p = planner.load_project(pid)
            jobs.prepare_media(p, progress=lambda x, st: jobs.set_progress(pid, x, st),
                               log=lambda m: jobs.log_line(pid, m), generate_images=True,
                               generate_videos=True)
        else:
            jobs.log_line(pid, "AI 키 없음 → 이미지 요청서를 만들고 대기합니다.")
            planner.write_image_request(p)
            st = _load_state()
            st.update({"last_run": now.isoformat(timespec="seconds"), "last_project": pid,
                       "last_error": None})
            _save_state(st)
            return {"ok": True, "project": pid, "topic": topic, "message": "이미지 요청서 생성(0원 모드)"}

        jobs.set_progress(pid, 60, "음성·렌더")
        jobs.finish_project(planner.load_project(pid))
        upload = None
        if s.get("auto_upload"):
            p = planner.load_project(pid)
            video = planner.project_dir(pid) / "out" / "final.mp4"
            if video.exists():
                when = youtube.schedule_time(hour=int(s.get("upload_hour", 19)))
                upload = youtube.upload(video, p["youtube"]["title"], p["youtube"]["description"],
                                        p["youtube"]["tags"], publish_at=when,
                                        thumbnail=planner.project_dir(pid) / "out" / "thumbnail.jpg")
                jobs.log_line(pid, f"예약 업로드: {upload.get('url')}")
        st = _load_state()
        st.update({"last_run": now.isoformat(timespec="seconds"), "last_project": pid, "last_error": None})
        _save_state(st)
        return {"ok": True, "project": pid, "topic": topic, "upload": upload}
    except Exception as e:
        st = _load_state()
        st.update({"last_run": now.isoformat(timespec="seconds"), "last_project": pid, "last_error": str(e)})
        _save_state(st)
        jobs.log_line(pid, f"자동 실행 오류: {e}")
        return {"ok": False, "project": pid, "error": str(e)}


def tick() -> dict | None:
    """1회 점검. 조건이 맞으면 프로젝트 생성 후 제작을 큐에 넣는다."""
    s = settings.load_settings()["scheduler"]
    if not s.get("enabled"):
        return None
    now = datetime.now()
    try:
        hh, mm = [int(x) for x in str(s.get("time", "07:00")).split(":")]
    except Exception:
        hh, mm = 7, 0
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    st = _load_state()
    last = st.get("last_run")
    if last:
        try:
            if datetime.fromisoformat(last).date() == now.date():
                return None
        except Exception:
            pass
    if now < due:
        return None

    projects = planner.list_projects()
    next_ep = s.get("next_ep") or (max([p.get("ep") or 0 for p in projects] + [7]) + 1)
    topic = _pick_topic()
    project = planner.create_project(
        topic=topic,
        content_type=s.get("content_type", "asmr3d"),
        mode="auto" if settings.key("gemini") else "semi",
        ep=int(next_ep),
    )
    jobs.run_full(project["id"], auto_script=bool(settings.key("gemini")))
    if s.get("auto_upload"):
        _schedule_upload(project["id"], int(s.get("upload_hour", 19)))
    st.update({"last_run": now.isoformat(timespec="seconds"), "last_project": project["id"], "last_error": None})
    _save_state(st)
    return {"project": project["id"], "topic": topic, "ep": next_ep}


def _schedule_upload(pid: str, hour: int) -> None:
    """제작이 끝나면 자동 업로드하도록 예약(별도 스레드)."""
    def wait_and_upload():
        for _ in range(180):  # 최대 90분 대기
            time.sleep(30)
            p = planner.load_project(pid)
            if p.get("status") == "rendered":
                break
        try:
            p = planner.load_project(pid)
            video = planner.project_dir(pid) / "out" / "final.mp4"
            if not video.exists():
                return
            when = youtube.schedule_time(hour=hour)
            youtube.upload(video, p["youtube"]["title"], p["youtube"]["description"],
                           p["youtube"]["tags"], publish_at=when,
                           thumbnail=planner.project_dir(pid) / "out" / "thumbnail.jpg")
            jobs.log_line(pid, f"유튜브 예약 업로드 완료 ({when:%m-%d %H:%M})")
        except Exception as e:
            st = _load_state()
            st["last_error"] = f"업로드: {e}"
            _save_state(st)

    threading.Thread(target=wait_and_upload, daemon=True).start()


def _loop() -> None:
    while not _stop.is_set():
        try:
            tick()
        except Exception as e:
            st = _load_state()
            st["last_error"] = str(e)
            _save_state(st)
        _stop.wait(60)


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="tastypop-scheduler")
    _thread.start()


def stop() -> None:
    _stop.set()


# ------------------------------------------------------------ macOS launchd
LAUNCHD_LABEL = "com.tastypop.studio.daily"


def _plist_path() -> Path:
    return Path(os.path.expanduser("~/Library/LaunchAgents")) / f"{LAUNCHD_LABEL}.plist"


def _venv_python() -> str:
    p = settings.APP_DIR / ".venv" / "bin" / "python"
    return str(p) if p.exists() else "python3"


def launchd_status() -> dict:
    plist = _plist_path()
    out = {"installed": plist.exists(), "label": LAUNCHD_LABEL, "path": str(plist), "loaded": False}
    if plist.exists():
        try:
            with open(plist, "rb") as f:
                data = plistlib.load(f)
            out["schedule"] = data.get("StartCalendarInterval")
        except Exception:
            pass
        try:
            r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=20)
            out["loaded"] = LAUNCHD_LABEL in (r.stdout or "")
        except Exception:
            pass
    return out


def install_launchd() -> dict:
    """매일(또는 지정 요일) 자동 실행을 macOS launchd 에 등록."""
    s = settings.load_settings()["scheduler"]
    hh, mm = _parse_time(s.get("time", "07:00"))
    days = s.get("weekdays")
    if days is None:
        days = [0, 1, 2, 3, 4, 5, 6]
    days = sorted({int(d) for d in days})
    if len(days) >= 7:
        interval = [{"Hour": hh, "Minute": mm}]
    else:
        interval = [{"Weekday": d, "Hour": hh, "Minute": mm} for d in days]
    logs = settings.CONFIG_DIR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    data = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [_venv_python(), "-m", "app.daily"],
        "WorkingDirectory": str(settings.APP_DIR),
        "StartCalendarInterval": interval,
        "StandardOutPath": str(logs / "launchd.out.log"),
        "StandardErrorPath": str(logs / "launchd.err.log"),
        "RunAtLoad": False,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    plist = _plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    with open(plist, "wb") as f:
        plistlib.dump(data, f)
    try:
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, timeout=30)
    except Exception:
        pass
    try:
        r = subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return {"ok": False, "path": str(plist), "schedule": interval,
                "stderr": "launchctl 을 찾을 수 없습니다. macOS 에서 실행해 주세요.",
                "status": launchd_status()}
    return {"ok": r.returncode == 0, "stderr": (r.stderr or "").strip(), "path": str(plist),
            "schedule": interval, "status": launchd_status()}


def uninstall_launchd() -> dict:
    plist = _plist_path()
    if plist.exists():
        try:
            subprocess.run(["launchctl", "unload", "-w", str(plist)], capture_output=True, timeout=30)
        except Exception:
            pass
        plist.unlink(missing_ok=True)
    return {"ok": True, "status": launchd_status()}

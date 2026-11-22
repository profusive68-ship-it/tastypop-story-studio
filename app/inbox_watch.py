"""assets_inbox 자동 수신 워커.

20초마다 `git pull --ff-only` 로 원격(케빈이 올린 이미지)을 확인하고,
`assets_inbox/{ep_id}/` 에 새 이미지가 있으면 `projects/{ep_id}/kf/` 로 자동 복사한다.
전 컷이 차면 기존 자동 조립 트리거와 똑같이 영상 조립을 시작한다.

안전 원칙
- `git pull --ff-only` 만 실행한다(로컬 커밋을 건드리지 않음).
- pull 이 실패해도(충돌·네트워크·index.lock) 로그만 남기고 서버는 계속 산다.
- 같은 이름이라도 **내용이 바뀐 경우에만** 기존 kf 파일을 교체한다(기본값).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
import unicodedata
from collections import deque
from datetime import datetime
from pathlib import Path

from . import jobs, planner, settings

KF_EXT = (".png", ".jpg", ".jpeg", ".webp")
CLIP_EXT = (".mp4", ".mov", ".webm")
MAX_LOG = 80

_log: deque[str] = deque(maxlen=MAX_LOG)
_lock = threading.RLock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_last: dict = {
    "last_pull_at": None, "last_pull_ok": None, "last_pull_message": "",
    "last_check_at": None, "last_receive_at": None, "last_received": [],
    "last_render": [], "fail_counts": {},
}


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    line = f"[{_now()}] {msg}"
    with _lock:
        _log.append(line)
    print("[auto-receive]", msg, flush=True)


def _cfg() -> dict:
    return settings.load_settings().get("assets") or {}


def enabled() -> bool:
    return bool(_cfg().get("auto_receive", True))


def poll_seconds() -> int:
    try:
        return max(5, int(_cfg().get("poll_seconds", 20)))
    except Exception:
        return 20


def overwrite_on_change() -> bool:
    return bool(_cfg().get("overwrite_on_change", True))


# ------------------------------------------------------------------ 상태 파일
def state_path() -> Path:
    return settings.CACHE_DIR / "inbox_state.json"


def load_state() -> dict:
    p = state_path()
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("folders"), dict):
                return data
    except Exception:
        pass
    return {"folders": {}}


def save_state(state: dict) -> None:
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"상태 저장 실패: {e}")


# ------------------------------------------------------------------ git
def git_pull(root: Path | None = None, timeout: int = 90) -> dict:
    """`git pull --ff-only`. 실패해도 예외를 던지지 않는다."""
    root = Path(root or settings.APP_DIR)
    res = {"ok": False, "skipped": False, "message": "", "changed": [], "stdout": "", "stderr": ""}
    if not (root / ".git").exists():
        res.update(skipped=True, message="GitHub 연결(.git)이 없어 자동 수신을 건너뜁니다.")
        return res
    if (root / ".git" / "index.lock").exists():
        res.update(skipped=True, message="다른 git 작업이 진행 중입니다(index.lock) — 이번 차례는 건너뜁니다.")
        return res
    try:
        r = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        res.update(message="git pull 시간 초과 — 다음 차례에 다시 시도합니다.")
        return res
    except Exception as e:
        res.update(message=f"git 실행 실패: {e}")
        return res
    res["stdout"] = (r.stdout or "").strip()
    res["stderr"] = (r.stderr or "").strip()
    out = f"{r.stdout}\n{r.stderr}"
    res["ok"] = r.returncode == 0
    if r.returncode != 0:
        first = (res["stderr"] or res["stdout"] or "알 수 없는 오류").splitlines()
        res["message"] = "git pull 실패(로컬 수정/충돌/네트워크): " + (first[0] if first else "")
        return res
    if "Already up to date" in out or "Already up-to-date" in out or "최신 상태" in out:
        res["message"] = "변경 없음"
        return res
    changed = re.findall(r"^\s*(\S+)\s+\|\s+\d+", out, flags=re.M)
    res["changed"] = [c for c in changed if c not in ("|",)]
    res["message"] = f"새 변경 {len(res['changed'])}개 파일"
    return res


# ------------------------------------------------------------------ 매칭
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").lower()
    return re.sub(r"[\s_\-\.\[\]\(\)]+", "", s)


def _ep_number(name: str) -> int | None:
    m = re.search(r"(?:ep|에피|화)\s*\.?\s*(\d{1,3})", name, flags=re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{1,3})\s*(?:화|편)", name)
    if m:
        return int(m.group(1))
    return None


def _needs_images(pid: str) -> bool:
    """아직 이미지(또는 클립)를 기다리는 프로젝트인지.

    예전에는 final.mp4 가 있으면 무조건 False 였지만, 이제는
    영상이 있어도 나중에 도착한 클립·교체된 이미지가 아직 반영되지 않았으면 True 다.
    """
    return _wants_assets(pid)


def _wants_assets(pid: str, incoming_clips: bool = False) -> bool:
    """이 프로젝트가 새 파일을 받아야 하는지(미완성 또는 재조립 대기).

    incoming_clips=True 는 "지금 들어오려는 묶음에 클립이 있다"는 뜻 —
    이미 완성된 프로젝트라도 새 클립을 받아 다시 조립해야 하므로 True.
    """
    try:
        p = planner.load_project(pid)
    except Exception:
        return False
    st = planner.media_status(p)
    if incoming_clips and st["found"] > 0:
        return True
    if not st["ready"]:
        return True
    if planner.render_needs_update(p):
        return True
    return False


def retry_pending_render(pid: str) -> bool:
    """같은 묶음을 이미 처리했어도 재조립이 남아 있으면 다시 처리하게 한다(렌더 실패 복구)."""
    try:
        p = planner.load_project(pid)
    except Exception:
        return False
    return planner.render_needs_update(p) and not jobs.is_busy(pid)


def match_project(folder_name: str, projects: list[dict] | None = None,
                  has_clips: bool = False) -> tuple[str | None, str]:
    """폴더명 → 프로젝트 ID. (pid, 매칭 이유) 를 돌려준다.

    has_clips: 묶음에 .mp4 가 들어 있으면, 영상이 이미 완성된 프로젝트도 후보로 본다.
    """
    projects = projects if projects is not None else planner.list_projects()
    if not projects:
        return None, "프로젝트가 없습니다"
    name = unicodedata.normalize("NFC", folder_name).strip()
    norm = _norm(name)

    for p in projects:                                    # 1) 폴더명 == 프로젝트 ID
        if p["id"] == name:
            return p["id"], "폴더명이 프로젝트 ID와 같음"

    for p in projects:                                    # 2) 앞부분 일치(ep08_불닭)
        pid = p["id"]
        if len(norm) >= 3 and (_norm(pid).startswith(norm) or norm.startswith(_norm(pid))):
            return pid, "폴더명이 프로젝트 ID와 부분 일치"

    ep = _ep_number(name)                                 # 3) Ep 번호 일치
    if ep is not None:
        same = [p for p in projects if int(p.get("ep") or -1) == ep]
        if same:
            waiting = [p for p in same if _wants_assets(p["id"], incoming_clips=has_clips)] or same
            waiting.sort(key=lambda p: p.get("created_at") or "", reverse=True)
            return waiting[0]["id"], f"Ep.{ep} 번호 일치"

    active = (settings.load_settings().get("ui") or {}).get("active_project") or ""  # 4) 지금 열어 둔 프로젝트
    if active and any(p["id"] == active for p in projects) and _wants_assets(active, incoming_clips=has_clips):
        return active, "지금 열어 둔 프로젝트"

    waiting = [p for p in projects                                       # 5) 대기중 프로젝트가 하나뿐
               if _wants_assets(p["id"], incoming_clips=has_clips)]
    if len(waiting) == 1:
        return waiting[0]["id"], "대기중인 프로젝트가 하나뿐"
    return None, "매칭되는 프로젝트를 찾지 못했습니다"


# ------------------------------------------------------------------ 이미지 복사
def _sha1(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.iterdir())
            if p.is_file() and p.suffix.lower() in KF_EXT and not p.name.startswith(".")]


def _clips(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    out = [p for p in sorted(folder.iterdir())
           if p.is_file() and p.suffix.lower() in CLIP_EXT and not p.name.startswith(".")]
    sub = folder / "clips"
    if sub.is_dir():
        out.extend(p for p in sorted(sub.iterdir())
                   if p.is_file() and p.suffix.lower() in CLIP_EXT and not p.name.startswith("."))
    return out


def _folder_signature(files: list[Path]) -> str:
    h = hashlib.sha1()
    for p in files:
        st = p.stat()
        h.update(f"{p.name}:{st.st_size}:{st.st_mtime_ns};".encode())
    return h.hexdigest()


def inbox_folders(root: Path | None = None) -> list[Path]:
    root = Path(root or settings.assets_dir())
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and (_images(d) or _clips(d)):
            out.append(d)
    return out


def _dest_for(pid: str, cut_id: int, ext: str) -> Path:
    kf = planner.project_dir(pid) / "kf"
    kf.mkdir(parents=True, exist_ok=True)
    for n in planner.kf_names(cut_id):                    # 이미 있는 이름이 있으면 그 자리를 갱신
        p = kf / n
        if p.exists():
            return p
    return kf / f"c{cut_id:02d}{ext}"


def _dest_for_clip(pid: str, cut_id: int, ext: str) -> Path:
    clips = planner.project_dir(pid) / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    for n in planner.clip_names(cut_id):
        p = clips / n
        if p.exists():
            return p
    return clips / f"c{cut_id:02d}{ext}"


def receive_folder(folder: Path, pid: str, overwrite: bool | None = None) -> dict:
    """한 묶음 폴더의 이미지/클립을 projects/{pid}/kf·clips 로 복사(내용이 다를 때만 교체)."""
    overwrite = overwrite_on_change() if overwrite is None else overwrite
    project = planner.load_project(pid)
    n_cuts = len(project["cuts"])
    added, replaced, skipped, outside = [], [], [], []

    def _copy_group(files: list[Path], dest_fn) -> tuple[list[str], list[str]]:
        a: list[str] = []
        r: list[str] = []
        parsed = []
        for p in files:
            m = re.search(r"(\d+)", p.stem)
            parsed.append((int(m.group(1)) if m else None, p))
        if all(num is not None for num, _ in parsed):
            parsed.sort(key=lambda x: x[0])
        for idx, (num, src) in enumerate(parsed, start=1):
            cut_id = num if num and 1 <= num <= n_cuts else idx
            if cut_id > n_cuts:
                outside.append(src.name)
                continue
            dst = dest_fn(pid, cut_id, src.suffix.lower())
            if dst.exists():
                if not overwrite or _sha1(dst) == _sha1(src):
                    skipped.append(dst.name)
                    continue
                shutil.copy2(src, dst)
                replaced.append(f"{src.name}→{dst.name}")
                r.append(f"{src.name}→{dst.name}")
                continue
            shutil.copy2(src, dst)
            added.append(f"{src.name}→{dst.name}")
            a.append(f"{src.name}→{dst.name}")
        return a, r

    added_clips, replaced_clips = _copy_group(_clips(folder), _dest_for_clip)
    _copy_group(_images(folder), _dest_for)
    project = planner.load_project(pid)
    st = planner.media_status(project)
    return {"project_id": pid, "folder": folder.name, "added": added, "replaced": replaced,
            "added_clips": added_clips, "replaced_clips": replaced_clips,
            "skipped": skipped, "outside_cuts": outside, "media": st,
            "needs_render": planner.render_needs_update(project)}


# ------------------------------------------------------------------ 한 차례 수신
def receive_once(root: Path | None = None, do_pull: bool = True, do_render: bool = True) -> dict:
    root = Path(root or settings.APP_DIR)
    inbox_root = Path(root) / "assets_inbox" if Path(root).name != "assets_inbox" else Path(root)
    summary: dict = {"ok": True, "pull": None, "received": [], "rendered": [], "skipped": []}
    _last["last_check_at"] = datetime.now().isoformat(timespec="seconds")

    if do_pull:
        pull = git_pull(root)
        _last["last_pull_at"] = datetime.now().isoformat(timespec="seconds")
        _last["last_pull_ok"] = bool(pull["ok"])
        _last["last_pull_message"] = pull["message"]
        summary["pull"] = pull
        if pull["ok"] and pull.get("changed"):
            log(pull["message"])
            if any(c.startswith(("app/", "VERSION")) for c in pull["changed"]):
                log("새 프로그램 코드가 내려왔습니다. start.command 를 다시 실행하면 적용됩니다.")
        elif not pull["ok"] and not pull.get("skipped"):
            log(pull["message"])

    state = load_state()
    folders = inbox_folders(inbox_root)
    if not folders:
        return summary
    projects = planner.list_projects()
    for folder in folders:
        files = _images(folder) + _clips(folder)
        has_clips = bool(_clips(folder))
        sig = _folder_signature(files)
        rec = state["folders"].get(folder.name) or {}
        if rec.get("sig") == sig and rec.get("target"):
            # 이미 처리한 묶음. 다만 지난번에 재조립 요청이 밀렸으면(렌더 실패·작업 중) 다시 큐잉만 한다.
            pid0 = rec.get("target")
            if rec.get("pending_render") and do_render and not jobs.is_busy(pid0) and retry_pending_render(pid0):
                try:
                    jobs.run_render(pid0)
                    jobs.log_line(pid0, "🎬 미디어 변경분 재조립 재시도")
                    log(f"{pid0}: 재조립 재시도")
                    summary["rendered"].append(pid0)
                    rec["pending_render"] = False
                    state["folders"][folder.name] = rec
                    save_state(state)
                except Exception as e:
                    log(f"{pid0}: 재조립 재시도 실패 — {e}")
            continue                                     # 이미 처리한 묶음
        pid, why = match_project(folder.name, projects, has_clips=has_clips)
        if not pid:
            if rec.get("sig") != sig:
                log(f"'{folder.name}' — {why}. (폴더명을 projects/{'{ep_id}'} 와 맞추거나 프로젝트를 열어 두세요)")
                rec["sig"] = sig
                state["folders"][folder.name] = rec
                save_state(state)
            summary["skipped"].append(folder.name)
            continue
        try:
            res = receive_folder(folder, pid)
        except Exception as e:
            key = f"{folder.name}#{sig}"
            fails = int(_last["fail_counts"].get(key, 0)) + 1
            _last["fail_counts"][key] = fails
            log(f"'{folder.name}' → {pid} 복사 실패({fails}회): {e}")
            if fails >= 10:
                rec["sig"] = sig                            # 계속 실패하면 같은 묶음은 더 시도하지 않음
                state["folders"][folder.name] = rec
                save_state(state)
                log(f"'{folder.name}' 은 10회 실패로 이번 묶음 처리를 멈춥니다.")
            summary["ok"] = False
            continue
        new_names = res["added"] + res["replaced"]
        new_clips = res.get("added_clips", []) + res.get("replaced_clips", [])
        if new_names:
            jobs.log_line(pid, "🎉 케빈이 올린 " + ("클립" if new_clips else "이미지") + " 자동 수신 완료!")
            if res.get("added_clips") or res.get("replaced_clips"):
                detail = f"  · {folder.name} → clips/ : 새 클립 {len(res.get('added_clips', []))}개"
                if res.get("replaced_clips"):
                    detail += f", 교체 {len(res['replaced_clips'])}개"
                jobs.log_line(pid, detail)
            if res["added"] or res["replaced"]:
                detail = f"  · {folder.name} → kf/ : 새 이미지 {len(res['added'])}장"
                if res["replaced"]:
                    detail += f", 교체 {len(res['replaced'])}장"
                jobs.log_line(pid, detail)
            jobs.log_line(pid, f"  · 미디어 {res['media']['found']}/{res['media']['total']}컷 감지"
                               + (f" (클립 {res['media'].get('clips', 0)}컷)" if res['media'].get("clips") else ""))
            log(f"{pid} ← {folder.name}: 새 파일 {len(new_names)}개"
                + (f" (클립 {len(new_clips)}개)" if new_clips else ""))
        rec = {"sig": sig, "target": pid, "why": why,
               "finished_at": datetime.now().isoformat(timespec="seconds"),
               "pending_render": False,
               "files": [f.split("→")[0] for f in new_names]}

        st = res["media"]
        if do_render and res.get("needs_render"):
            if jobs.is_busy(pid):
                rec["pending_render"] = True                  # 작업 중이면 끝난 뒤 다시 시도
                log(f"{pid}: 렌더 중이라 재조립을 다음 차례로 미룹니다")
            else:
                had_video = planner.final_video(pid).exists()
                try:
                    jobs.run_render(pid)
                    if had_video:
                        jobs.log_line(pid, "🎬 클립/이미지 변경 감지 → 움직이는 버전으로 다시 조립 시작 (final.mp4 덮어씁니다)")
                        log(f"{pid}: 미디어 변경 감지 → 재조립 시작(final.mp4 덮어쓰기)")
                    else:
                        jobs.log_line(pid, f"이미지 {st['found']}/{st['total']} 준비 완료 → 영상 조립 자동 시작"
                                           + (f" (클립 {st.get('clips', 0)}컷)" if st.get("clips") else ""))
                        log(f"{pid}: 전 컷 준비 완료 → 영상 조립 자동 시작")
                    summary["rendered"].append(pid)
                    _last["last_render"] = list(summary["rendered"])
                except Exception as e:
                    rec["pending_render"] = True
                    jobs.log_line(pid, f"자동 조립 시작 실패: {e}")
                    log(f"{pid}: 자동 조립 시작 실패 — {e}")
        state["folders"][folder.name] = rec
        save_state(state)
        summary["received"].append({"project_id": pid, "folder": folder.name,
                                    "added": res["added"], "replaced": res["replaced"],
                                    "added_clips": res.get("added_clips", []),
                                    "replaced_clips": res.get("replaced_clips", [])})
        _last["last_receive_at"] = rec["finished_at"]
        _last["last_received"] = [f"{pid} ← {folder.name} {len(new_names)}개"]
    return summary


# ------------------------------------------------------------------ 워커
def _loop() -> None:
    log(f"자동 이미지 수신 시작 · {poll_seconds()}초마다 GitHub 확인")
    while not _stop.is_set():
        try:
            if enabled():
                receive_once()
        except Exception as e:                              # 어떤 오류도 서버를 죽이지 않는다
            log(f"자동 수신 오류(계속 진행): {e}")
        _stop.wait(poll_seconds())
    log("자동 이미지 수신 정지")


def start(force: bool = False) -> dict:
    global _thread
    if not (settings.APP_DIR / ".git").exists() and not force:
        _last["last_pull_message"] = "GitHub 연결(.git)이 없습니다."
        return status()
    with _lock:
        if _thread and _thread.is_alive():
            return status()
        _stop.clear()
        _thread = threading.Thread(target=_loop, daemon=True, name="tastypop-inbox")
        _thread.start()
    return status()


def stop() -> dict:
    global _thread
    _stop.set()
    _thread = None
    return status()


def set_enabled(on: bool) -> dict:
    settings.save_settings({"assets": {"auto_receive": bool(on)}})
    log("자동 이미지 수신 ON" if on else "자동 이미지 수신 OFF")
    if on:
        start()
    return status()


def status() -> dict:
    return {
        "enabled": enabled(),
        "has_git": (settings.APP_DIR / ".git").exists(),
        "running": bool(_thread and _thread.is_alive()),
        "interval": poll_seconds(),
        "inbox_root": str(settings.assets_dir()),
        "folders": [{"folder": d.name, "count": len(_images(d)), "clips": len(_clips(d))}
                    for d in inbox_folders()],
        "state": dict(_last),
        "log": list(_log)[-40:],
    }

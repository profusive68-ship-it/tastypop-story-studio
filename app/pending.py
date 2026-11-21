"""고정댓글 자동 게시 대기열.

유튜브는 '고정(pin)' API 를 제공하지 않는다. 그래서
- 즉시 공개 업로드: 업로드 직후 첫 댓글을 자동 게시 (핀만 앱/웹에서 1클릭)
- 예약/비공개 업로드: config/pending_comments.json 에 (video_id, 공개시각+90초, 댓글) 을 저장하고,
  백그라운드 워커가 공개 시각 이후 실제 공개 상태를 확인해 자동으로 댓글을 단다.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import settings

_FILE = settings.CONFIG_DIR / "pending_comments.json"
_lock = threading.RLock()
_thread: threading.Thread | None = None
_stop = threading.Event()

PIN_NOTE = "첫 댓글 자동 게시 완료 (상단 고정 핀만 유튜브 앱/웹에서 1클릭)"


def _load() -> list[dict]:
    if _FILE.exists():
        try:
            return json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(items: list[dict]) -> None:
    settings.ensure_dirs()
    _FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add(video_id: str, text: str, at: datetime, project_id: str | None = None) -> dict:
    with _lock:
        items = _load()
        entry = {
            "video_id": video_id,
            "text": text,
            "at": at.isoformat(timespec="seconds"),
            "project_id": project_id,
            "posted": False,
            "posted_at": None,
            "attempts": 0,
            "last_error": None,
        }
        items.append(entry)
        _save(items)
        return entry


def all_items() -> list[dict]:
    return _load()


def _post(item: dict) -> dict:
    from . import youtube

    try:
        st = youtube.video_status(item["video_id"])
        if st.get("privacy") != "public":
            item["last_error"] = f"아직 비공개({st.get('privacy')}) — 공개 후 재시도"
            item["attempts"] = int(item.get("attempts", 0)) + 1
            return item
    except Exception as e:
        item["attempts"] = int(item.get("attempts", 0)) + 1
        item["last_error"] = f"상태 확인 실패: {e}"
        return item

    try:
        res = youtube.post_comment(item["video_id"], item["text"])
        item["posted"] = True
        item["posted_at"] = datetime.now().isoformat(timespec="seconds")
        item["comment_id"] = res.get("id")
        item["last_error"] = None
    except Exception as e:
        item["attempts"] = int(item.get("attempts", 0)) + 1
        item["last_error"] = str(e)
    return item


def tick(now: datetime | None = None) -> list[dict]:
    """대기열을 점검해 공개된 영상에 댓글을 단다."""
    now = now or datetime.now()
    changed = []
    with _lock:
        items = _load()
        dirty = False
        for item in items:
            if item.get("posted"):
                continue
            try:
                at = datetime.fromisoformat(item["at"])
            except Exception:
                at = now
            if now < at:
                continue
            if int(item.get("attempts", 0)) >= 60:
                continue
            before = json.dumps(item, ensure_ascii=False, sort_keys=True)
            _post(item)
            if json.dumps(item, ensure_ascii=False, sort_keys=True) != before:
                dirty = True
                changed.append(item)
        if dirty:
            _save(items)
    return changed


def start_worker() -> None:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()

        def loop():
            while not _stop.is_set():
                try:
                    tick()
                except Exception:
                    pass
                _stop.wait(10)

        _thread = threading.Thread(target=loop, daemon=True, name="tastypop-comments")
        _thread.start()


def stop_worker() -> None:
    _stop.set()


def schedule_after_upload(video_id: str, text: str, publish_at: datetime | None,
                          project_id: str | None = None) -> dict:
    """업로드 결과에 맞춰 즉시 게시 또는 예약 대기열 등록."""
    from . import youtube

    if publish_at is None:
        try:
            res = youtube.post_comment(video_id, text)
            return {"mode": "immediate", "ok": True, "comment_id": res.get("id"),
                    "message": PIN_NOTE, "video_url": f"https://youtube.com/shorts/{video_id}"}
        except Exception as e:
            at = datetime.now() + timedelta(minutes=2)
            add(video_id, text, at, project_id)
            return {"mode": "queued", "ok": False, "error": str(e),
                    "message": "즉시 게시 실패 → 공개 확인 후 자동 재시도 대기열에 넣었습니다.",
                    "video_url": f"https://youtube.com/shorts/{video_id}"}
    at = publish_at - timedelta(seconds=10)
    add(video_id, text, at, project_id)
    return {"mode": "scheduled", "ok": True, "at": at.isoformat(timespec="seconds"),
            "message": (f"예약 공개 {publish_at:%m-%d %H:%M} 직전부터 10초 간격으로 공개 전환을 확인해 "
                        "공개되는 즉시 첫 댓글을 자동 등록합니다. (핀 고정만 앱/웹에서 1클릭)"),
            "video_url": f"https://youtube.com/shorts/{video_id}"}

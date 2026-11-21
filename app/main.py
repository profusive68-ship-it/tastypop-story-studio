"""TastyPop Studio 로컬 대시보드 서버 (http://localhost:8766)."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import (audio, copywriter, ffmpeg_tools, fonts, jobs, kling, llm, pending, planner,
               render, revise, scheduler, settings, sfx, strategy, topics, tts, youtube)

app = FastAPI(title="TastyPop Studio", version=settings.VERSION)
WEB = settings.WEB_DIR
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.on_event("startup")
def _startup() -> None:
    settings.ensure_dirs()
    try:
        sfx.ensure_library()
    except Exception as e:
        print("[sfx] 라이브러리 생성 실패:", e)
    scheduler.start()
    pending.start_worker()


# ------------------------------------------------------------------ 화면
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    env = ffmpeg_tools.check_environment()
    return {
        "ok": env.get("ok", False),
        "version": settings.VERSION,
        "env": env,
        "providers": llm.provider_status(),
        "youtube": youtube.status() if settings.youtube_token_path().exists() else {"ok": False, "token": False},
        "scheduler": scheduler.status(),
        "encoder": ffmpeg_tools.encoder_name(),
        "time": datetime.now().isoformat(timespec="seconds"),
        "content_types": planner.CONTENT_TYPES,
        "duration_presets": planner.DURATION_PRESETS,
        "squad": planner.SQUAD,
        "topic_count": sum(len(v) for v in topics.TOPICS.values()),
    }


# ------------------------------------------------------------------ 주제 추천
@app.get("/api/topics/categories")
def topic_categories() -> dict:
    return {"categories": topics.categories(), "presets": planner.DURATION_PRESETS}


@app.get("/api/topics/recommend")
def topic_recommend(category: str | None = None, count: int = 4, ai: bool = False) -> dict:
    if ai:
        return topics.ai_trends(count)
    items = topics.recommend(category, count)
    return {"ok": True, "source": "builtin", "items": items,
            "note": "내장 추천 DB (API 키 없이 동작)"}


@app.get("/api/topics/today")
def topic_today() -> dict:
    return {"ok": True, "items": topics.today_pick()}


# ------------------------------------------------------------------ 설정
@app.get("/api/settings")
def get_settings() -> dict:
    s = settings.load_settings()
    masked = json.loads(json.dumps(s))
    for k, v in masked.get("keys", {}).items():
        masked["keys"][k] = ("●●●●" + v[-4:]) if v else ""
    return masked


@app.post("/api/settings")
def post_settings(patch: dict = Body(default={})) -> dict:
    keys = (patch or {}).pop("keys", None) if isinstance(patch, dict) else None
    if keys:
        cur = settings.load_settings()["keys"]
        clean = {k: v for k, v in keys.items() if not str(v).startswith("●●●●")}
        settings.save_settings({"keys": {**cur, **clean}})
    if patch:
        settings.save_settings(patch)
    return {"ok": True, "settings": get_settings()}


# ------------------------------------------------------------------ 사운드
@app.get("/api/sfx")
def list_sfx() -> dict:
    return {"library": sfx.list_library(), "dir": str(settings.SFX_DIR)}


@app.post("/api/sfx/regenerate")
def regen_sfx() -> dict:
    made = sfx.ensure_library(force=True)
    return {"ok": True, "count": len(made)}


# ------------------------------------------------------------------ 프로젝트
@app.get("/api/projects")
def projects() -> dict:
    return {"projects": planner.list_projects()}


@app.post("/api/projects")
def create_project(body: dict = Body(default={})) -> dict:
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise HTTPException(400, "주제(음식/사연)를 입력해 주세요.")
    content_type = body.get("content_type") or "asmr3d"
    if content_type not in planner.CONTENT_TYPES:
        raise HTTPException(400, f"알 수 없는 콘텐츠 타입: {content_type}")
    mode = body.get("mode") or ("auto" if settings.key("gemini") else "semi")
    ep = body.get("ep")
    tsec = body.get("seconds")
    project = planner.create_project(
        topic=topic,
        content_type=content_type,
        mode=mode,
        target_seconds=float(tsec) if tsec else None,
        ep=int(ep) if ep else None,
        hook_text=body.get("hook") or None,
    )
    # 추천 카드에서 넘어온 효과음·옵션·업로드 방식 반영
    if body.get("sfx"):
        for i, c in enumerate(project["cuts"]):
            c["sfx"] = [body["sfx"][i % len(body["sfx"])]] + [s for s in c["sfx"] if s != body["sfx"][i % len(body["sfx"])]]
            c["sfx"] = c["sfx"][:3]
    if isinstance(body.get("options"), dict):
        project["options"].update({k: bool(v) for k, v in body["options"].items()})
    if body.get("upload_method"):
        project["upload_method"] = body["upload_method"]
    planner.save_project(project)
    jobs.log_line(project["id"], f"프로젝트 생성 · {project['content_label']} · {mode} 모드")
    if body.get("auto_start"):
        jobs.run_full(project["id"], auto_script=bool(body.get("auto_script")),
                      generate_videos=mode == "auto")
    return {"ok": True, "project": project}


def _proj(pid: str) -> dict:
    try:
        return planner.load_project(pid)
    except FileNotFoundError:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    p = _proj(pid)
    p["progress"] = jobs.get_progress(pid)
    p["files_present"] = {
        "video": (planner.project_dir(pid) / "out" / "final.mp4").exists(),
        "thumbnail": (planner.project_dir(pid) / "out" / "thumbnail.jpg").exists(),
        "copy": (planner.project_dir(pid) / "out" / "upload_copy.txt").exists(),
        "kf": len(list((planner.project_dir(pid) / "kf").glob("*"))),
        "clips": len(list((planner.project_dir(pid) / "clips").glob("*"))),
    }
    return p


@app.put("/api/projects/{pid}")
def update_project(pid: str, patch: dict = Body(default={})) -> dict:
    p = _proj(pid)
    for k in ("hook", "cta", "topic", "options", "upload_method"):
        if k in patch and isinstance(patch[k], dict) and isinstance(p.get(k), dict):
            p[k].update(patch[k])
        elif k in patch:
            p[k] = patch[k]
    if "cuts" in patch:
        for c in p["cuts"]:
            inc = next((x for x in patch["cuts"] if int(x.get("id", -1)) == c["id"]), None)
            if inc:
                if "dur" in inc:
                    c["dur"] = max(0.5, round(float(inc["dur"]), 2))
                if "enabled" in inc:
                    c["enabled"] = bool(inc["enabled"])
                if "sfx" in inc:
                    c["sfx"] = inc["sfx"]
    planner.save_project(p)
    planner.write_image_request(p)
    return {"ok": True, "project": p}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, confirm: bool = False) -> dict:
    if not confirm:
        raise HTTPException(400, "삭제하려면 confirm=true 가 필요합니다.")
    d = planner.project_dir(pid)
    if not d.exists():
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    trash = settings.PROJECTS_DIR / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / f"{pid}_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.move(str(d), str(dest))
    return {"ok": True, "moved_to": str(dest)}


@app.post("/api/projects/{pid}/produce")
def produce(pid: str, body: dict = Body(default={})) -> dict:
    p = _proj(pid)
    jobs.run_full(
        pid,
        generate_images=bool(body.get("images", p.get("mode") == "auto")),
        generate_videos=bool(body.get("videos", p.get("mode") == "auto")),
        auto_script=bool(body.get("script", False)),
        script_extra=body.get("extra", ""),
    )
    return {"ok": True, "queued": pid}


@app.post("/api/projects/{pid}/script")
def make_script(pid: str, body: dict = Body(default={})) -> dict:
    p = _proj(pid)
    try:
        text = llm.generate_text(planner.script_prompt(p, body.get("extra", "")), planner.SCRIPT_SYSTEM)
        script = llm.extract_json(text)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))
    planner.apply_script(p, script)
    jobs.log_line(pid, "AI 대본 생성·반영 완료")
    return {"ok": True, "script": script, "project": planner.load_project(pid)}


@app.post("/api/projects/{pid}/images")
def make_images(pid: str, body: dict = Body(default={})) -> dict:
    p = _proj(pid)

    def fn():
        p2 = planner.load_project(pid)
        jobs.prepare_media(p2, progress=lambda x, s: jobs.set_progress(pid, x, s),
                           log=lambda m: jobs.log_line(pid, m), generate_images=True, generate_videos=False)
        jobs.set_progress(pid, 100, "이미지 완료")

    jobs.enqueue(pid, fn, kind="images")
    return {"ok": True, "queued": pid}


@app.post("/api/projects/{pid}/render")
def do_render(pid: str) -> dict:
    _proj(pid)
    jobs.run_render(pid)
    return {"ok": True, "queued": pid}


@app.get("/api/projects/{pid}/progress")
def progress(pid: str) -> dict:
    return jobs.get_progress(pid)


@app.get("/api/projects/{pid}/media")
def media(pid: str, auto: bool = True) -> dict:
    """kf/ · clips/ 폴더를 자동 감지해 초록불 상태를 돌려준다.

    모든 컷이 준비되면(초록불) 자동으로 다음 단계(영상 조립)를 큐에 넣는다.
    """
    p = _proj(pid)
    st = planner.media_status(p)
    queued = False
    video = planner.project_dir(pid) / "out" / "final.mp4"
    if auto and st["ready"] and not video.exists() and not jobs.is_busy(pid):
        jobs.run_render(pid)
        jobs.log_line(pid, f"이미지 {st['found']}/{st['total']} 감지 → 영상 조립 자동 시작")
        queued = True
    st["queued"] = queued
    st["has_video"] = video.exists()
    return st


@app.post("/api/projects/{pid}/import")
def import_images(pid: str, body: dict = Body(default={})) -> dict:
    """assets_inbox/{묶음} 안의 이미지를 프로젝트 kf/ 로 한 번에 복사."""
    p = _proj(pid)
    folder = body.get("folder")
    root = settings.APP_DIR / "assets_inbox"
    if folder:
        src = root / folder
    else:
        folders = planner.inbox_folders()
        if not folders:
            raise HTTPException(400, "assets_inbox/ 안에 이미지 묶음이 없습니다. [🔄 최신 버전·이미지 받아오기] 를 먼저 실행하세요.")
        src = Path(folders[0]["path"])
    try:
        res = planner.import_images(pid, src, overwrite=bool(body.get("overwrite")))
    except Exception as e:
        raise HTTPException(400, str(e))
    jobs.log_line(pid, f"이미지 가져오기: {len(res['copied'])}개 ({src.name})")
    st = planner.media_status(p)
    if st["ready"] and not (planner.project_dir(pid) / "out" / "final.mp4").exists() and not jobs.is_busy(pid):
        jobs.run_render(pid)
        jobs.log_line(pid, "이미지 준비 완료 → 영상 조립 자동 시작")
        res["queued"] = True
    res["media"] = st
    return {"ok": True, **res}


@app.get("/api/inbox")
def inbox() -> dict:
    folders = planner.inbox_folders()
    return {"folders": folders, "root": str(settings.APP_DIR / "assets_inbox")}


@app.get("/api/inbox/image")
def inbox_image(folder: str, name: str):
    root = (settings.APP_DIR / "assets_inbox" / folder).resolve()
    base = (settings.APP_DIR / "assets_inbox").resolve()
    if base not in root.parents and root != base:
        raise HTTPException(400, "잘못된 경로입니다.")
    f = root / name
    if not f.exists() or f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(404, "이미지가 없습니다.")
    return FileResponse(str(f))


@app.get("/api/projects/{pid}/image_request")
def image_request(pid: str) -> Response:
    p = _proj(pid)
    f = planner.write_image_request(p)
    return Response(f.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


@app.get("/api/projects/{pid}/copy")
def get_copy(pid: str) -> dict:
    p = _proj(pid)
    if not p.get("youtube", {}).get("title"):
        copywriter.build_all(p)
        p = planner.load_project(pid)
    return {"youtube": p.get("youtube", {}), "instagram": p.get("instagram", {})}


@app.post("/api/projects/{pid}/revise")
def revise_project(pid: str, body: dict = Body(default={})) -> dict:
    """수정 지시 한 줄로 시나리오·타임라인 자동 재조정."""
    p = _proj(pid)
    instruction = (body.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "수정 지시를 입력해 주세요. 예: '3컷 더 빠르게, 치즈 소리 크게'")
    try:
        res = revise.revise(p, instruction)
    except Exception as e:
        raise HTTPException(400, str(e))
    jobs.log_line(pid, f"수정 지시 반영({res.get('engine')}): {instruction}")
    return res


@app.post("/api/projects/{pid}/package")
def package_project(pid: str, body: dict = Body(default={})) -> dict:
    """업로드 방식별 패키지: youtube(예약안내) / instagram(zip+체크리스트) / local(MP4+카피)."""
    p = _proj(pid)
    kind = body.get("kind") or p.get("upload_method") or "youtube"
    p["upload_method"] = kind
    planner.save_project(p)
    if kind == "instagram":
        f = copywriter.make_instagram_package(p)
        jobs.log_line(pid, f"인스타 릴스 패키지 생성: {f.name}")
        return {"ok": True, "kind": kind, "file": str(f), "checklist": p.get("instagram", {}).get("checklist", [])}
    if kind == "local":
        return {"ok": True, "kind": kind,
                "video": str(planner.project_dir(pid) / "out" / "final.mp4"),
                "copy": str(planner.project_dir(pid) / "out" / "upload_copy.txt"),
                "folder": str(planner.project_dir(pid) / "out")}
    when = youtube.schedule_time(hour=int(body.get("hour", settings.load_settings()["youtube"]["default_hour"])))
    return {"ok": True, "kind": "youtube", "suggested_time": when.isoformat(timespec="minutes"),
            "project": str(planner.project_dir(pid) / "out" / "final.mp4")}


@app.post("/api/projects/{pid}/copy")
def rebuild_copy(pid: str) -> dict:
    p = _proj(pid)
    data = copywriter.build_all(p)
    return {"ok": True, **data}


@app.post("/api/projects/{pid}/thumbnail")
def make_thumb(pid: str, body: dict = Body(default={})) -> dict:
    p = _proj(pid)
    f = copywriter.make_thumbnail(p, int(body.get("cut", 0)))
    if not f:
        raise HTTPException(400, "키프레임이 없어 썸네일을 만들 수 없습니다.")
    return {"ok": True, "file": str(f)}


@app.get("/api/projects/{pid}/file/{kind}")
def project_file(pid: str, kind: str):
    d = planner.project_dir(pid)
    mapping = {
        "video": d / "out" / "final.mp4",
        "thumbnail": d / "out" / "thumbnail.jpg",
        "copy": d / "out" / "upload_copy.txt",
        "instagram": d / "out" / "instagram_copy.txt",
        "qa": d / "out" / "qa_report.json",
        "audio": d / "out" / "audio_master.wav",
    }
    if kind == "package":
        pkgs = sorted((d / "out").glob("인스타_릴스_패키지_*.zip"))
        if not pkgs:
            raise HTTPException(404, "패키지가 없습니다.")
        return FileResponse(str(pkgs[-1]), filename=pkgs[-1].name)
    f = mapping.get(kind)
    if not f or not f.exists():
        raise HTTPException(404, "파일이 없습니다.")
    return FileResponse(str(f))


@app.get("/api/projects/{pid}/preview/{name}")
def preview_image(pid: str, name: str):
    d = planner.project_dir(pid)
    for sub in ("kf", "out", "work/overlays"):
        f = d / sub / name
        if f.exists() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return FileResponse(str(f))
    # kf1.png 같은 이름도 컷 번호로 찾아 준다
    m = json.loads("null")
    import re as _re

    mm = _re.search(r"(\d+)", name)
    if mm:
        try:
            p = planner.load_project(pid)
            cut = next((c for c in p["cuts"] if c["id"] == int(mm.group(1))), None)
            if cut:
                f, kind = planner.resolve_media(p, cut)
                if f and kind == "image":
                    return FileResponse(str(f))
        except Exception:
            pass
    raise HTTPException(404, "이미지가 없습니다.")


@app.get("/api/text_overlay")
def text_overlay(text: str, kind: str = "hook", size: int | None = None) -> Response:
    """훅/자막 미리보기 PNG (투명 배경 위 실제 오버레이)."""
    if kind == "safe":
        img = fonts.safe_area_guide()
    elif kind == "hook":
        img = fonts.hook_overlay(text, {**settings.load_settings()["render"], **({"hook_size": size} if size else {})})
    else:
        img = fonts.subtitle_overlay(text, size=size or 58)
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


# ------------------------------------------------------------------ 유튜브
@app.get("/api/yt/status")
def yt_status() -> dict:
    return youtube.status()


@app.post("/api/yt/login")
def yt_login() -> dict:
    def fn():
        try:
            youtube.authorize()
        except Exception as e:
            print("[youtube] 로그인 실패:", e)

    threading.Thread(target=fn, daemon=True).start()
    return {"ok": True, "message": "브라우저 창에서 유튜브 채널 로그인을 완료해 주세요."}


@app.post("/api/yt/upload")
def yt_upload(body: dict = Body(default={})) -> dict:
    pid = body.get("project_id")
    p = _proj(pid)
    video = planner.project_dir(pid) / "out" / "final.mp4"
    if not video.exists():
        raise HTTPException(400, "먼저 영상을 렌더해 주세요.")
    copy = p.get("youtube") or copywriter.build_all(p)["youtube"]
    when = None
    if body.get("publish_at"):
        try:
            when = datetime.fromisoformat(body["publish_at"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=datetime.now().astimezone().tzinfo)
        except Exception:
            raise HTTPException(400, "예약 시간 형식이 올바르지 않습니다(YYYY-MM-DDTHH:MM).")
    elif body.get("schedule_hour") is not None:
        when = youtube.schedule_time(hour=int(body["schedule_hour"]), minute=int(body.get("schedule_minute", 0)))
    try:
        res = youtube.upload(
            video, body.get("title") or copy.get("title", ""),
            body.get("description") or copy.get("description", ""),
            copy.get("tags", []), publish_at=when,
            privacy=body.get("privacy"), thumbnail=planner.project_dir(pid) / "out" / "thumbnail.jpg",
        )
    except youtube.YouTubeError as e:
        raise HTTPException(400, str(e))
    jobs.log_line(pid, f"유튜브 업로드: {res.get('url')} ({res.get('privacy')})")
    p.setdefault("uploads", []).append({**res, "at": datetime.now().isoformat(timespec="seconds"),
                                        "comment": comment})
    planner.save_project(p)
    return {"ok": True, **res, "comment": comment}


@app.get("/api/yt/videos")
def yt_videos() -> dict:
    try:
        return {"ok": True, "videos": youtube.channel_videos(50)}
    except Exception as e:
        return {"ok": False, "error": str(e), "videos": []}


@app.get("/api/yt/pending-comments")
def pending_comments() -> dict:
    return {"items": pending.all_items()}


@app.post("/api/yt/pending-comments/run")
def pending_comments_run() -> dict:
    return {"ok": True, "posted": pending.tick()}


# ------------------------------------------------------------------ 전략
@app.get("/api/strategy")
def get_strategy() -> dict:
    vids: list[dict] = []
    err = None
    try:
        if settings.youtube_token_path().exists():
            vids = youtube.channel_videos(50)
    except Exception as e:
        err = str(e)
    data = strategy.analyze(vids)
    if err:
        data["warning"] = f"유튜브 통계를 읽지 못했습니다: {err}"
    return data


@app.post("/api/strategy/{card_id}/approve")
def approve_card(card_id: str, body: dict = Body(default={})) -> dict:
    card = strategy.get_card(card_id)
    if not card:
        raise HTTPException(404, "제안 카드를 찾을 수 없습니다. [다시 분석] 을 눌러 주세요.")
    act = card.get("action", {})
    if card["kind"] == "strategy":
        settings.save_settings({"youtube": {"default_hour": act.get("upload_hour", 19)}})
        settings.save_settings({"scheduler": {"upload_hour": act.get("upload_hour", 19)}})
        p = planner.create_project(
            topic=act.get("topic") or "새 K-푸드",
            content_type=act.get("content_type", "asmr3d"),
            mode="auto" if settings.key("gemini") else "semi",
            target_seconds=float(act.get("seconds") or 18.0),
            hook_text=act.get("hook"),
        )
        if act.get("sfx"):
            for i, c in enumerate(p["cuts"]):
                c["sfx"] = [act["sfx"][i % len(act["sfx"])]][:1] + [s for s in c["sfx"] if s != act["sfx"][i % len(act["sfx"])]][:2]
        planner.save_project(p)
        jobs.run_full(p["id"], auto_script=bool(settings.key("gemini")))
        return {"ok": True, "project": p, "message": f"전략 적용: '{p['topic']}' {p['total_seconds']}초 · {act.get('upload_hour', 19)}시 편성으로 제작을 시작합니다."}
    if card["kind"] == "time":
        settings.save_settings({"youtube": {"default_hour": act["upload_hour"]}})
        settings.save_settings({"scheduler": {"upload_hour": act["upload_hour"]}})
        return {"ok": True, "message": f"기본 업로드 시간을 {act['upload_hour']:02d}:00 으로 저장했습니다."}
    if card["kind"] in ("topic", "drama"):
        p = planner.create_project(
            topic=act.get("topic") or "새 K-푸드",
            content_type=act.get("content_type", "asmr3d"),
            mode="auto" if settings.key("gemini") else "semi",
            ep=act.get("next_ep"),
        )
        jobs.run_full(p["id"], auto_script=bool(settings.key("gemini")))
        return {"ok": True, "project": p, "message": f"'{p['topic']}' 제작을 시작했습니다."}
    if card["kind"] == "bundle":
        try:
            res = jobs.bundle(act.get("bundle", []))
        except Exception as e:
            raise HTTPException(400, str(e))
        return {"ok": True, **res, "message": "주말 몰아보기 합본을 만들었습니다."}
    if card["kind"] == "finish":
        return {"ok": True, "message": "미완성 프로젝트를 [프로젝트] 탭에서 열어 마무리해 주세요.",
                "projects": act.get("projects", [])}
    if card["kind"] == "series":
        settings.save_settings({"content": {"ep_start": act.get("next_ep", 8)}})
        return {"ok": True, "message": f"다음 편 번호를 Ep.{act.get('next_ep')} 로 설정했습니다."}
    return {"ok": True, "message": "승인되었습니다.", "card": card}


# ------------------------------------------------------------------ 댓글
@app.get("/api/comments")
def get_comments(video_id: str | None = None) -> dict:
    try:
        rows = youtube.comments(video_id)
    except Exception as e:
        return {"ok": False, "error": str(e), "comments": []}
    for r in rows:
        if not r.get("replied"):
            d = youtube.draft_replies(r.get("text", ""))
            r["draft_ko"] = d["ko"]
            r["draft_en"] = d["en"]
            r["draft_lang"] = d["suggested"]
            r["draft"] = d[d["suggested"]]
    return {"ok": True, "comments": rows}


@app.post("/api/comments/reply")
def post_reply(body: dict = Body(default={})) -> dict:
    cid = body.get("comment_id")
    text = (body.get("text") or "").strip()
    if not cid or not text:
        raise HTTPException(400, "comment_id 와 답글 내용이 필요합니다.")
    try:
        return {"ok": True, **youtube.reply(cid, text)}
    except Exception as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ 스케줄러
@app.get("/api/scheduler")
def get_scheduler() -> dict:
    return scheduler.status()


@app.post("/api/scheduler")
def post_scheduler(body: dict = Body(default={})) -> dict:
    patch = {k: body[k] for k in ("enabled", "time", "content_type", "auto_upload", "upload_hour",
                                  "next_ep", "weekdays")
             if k in body}
    if "next_ep" in patch and patch["next_ep"] in ("", None):
        patch["next_ep"] = None
    settings.save_settings({"scheduler": patch})
    if patch.get("enabled"):
        scheduler.start()
    elif "enabled" in patch:
        scheduler.stop()
    return scheduler.status()


@app.post("/api/scheduler/launchd")
def launchd_install(body: dict = Body(default={})) -> dict:
    if not body.get("install", True):
        return {"ok": True, **scheduler.uninstall_launchd()}
    res = scheduler.install_launchd()
    return {"ok": bool(res.get("ok")), **res}


@app.post("/api/scheduler/run-now")
def scheduler_run_now() -> dict:
    """지금 1편 자동 제작(테스트용)."""
    try:
        res = scheduler.daily_job(force=True)
    except Exception as e:
        raise HTTPException(400, str(e))
    return res


# ------------------------------------------------------------------ 업데이트 / 검수
@app.get("/api/update/status")
def update_status() -> dict:
    root = settings.APP_DIR
    has_git = (root / ".git").exists()
    info = {"has_git": has_git, "version": settings.VERSION, "root": str(root)}
    if has_git:
        try:
            r = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                               capture_output=True, text=True, timeout=20)
            info["branch"] = r.stdout.strip()
            r2 = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%h %s"],
                                capture_output=True, text=True, timeout=20)
            info["commit"] = r2.stdout.strip()
            r3 = subprocess.run(["git", "-C", str(root), "remote", "-v"], capture_output=True, text=True, timeout=20)
            info["remote"] = r3.stdout.strip().splitlines()[:1]
        except Exception as e:
            info["error"] = str(e)
    return info


@app.post("/api/update")
def do_update() -> dict:
    """git pull --ff-only 로 최신 코드·이미지(assets_inbox)만 받아온다."""
    root = settings.APP_DIR
    if not (root / ".git").exists():
        raise HTTPException(400, "이 폴더는 GitHub 와 연결되어 있지 않습니다. README_맥북설치방법.txt 를 참고해 주세요.")
    try:
        r = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        raise HTTPException(500, f"업데이트 실패: {e}")
    ok = r.returncode == 0
    inbox_list = planner.inbox_folders()
    return {"ok": ok, "stdout": r.stdout.strip(), "stderr": r.stderr.strip(),
            "inbox": inbox_list,
            "message": (f"업데이트 완료 · 새 이미지 묶음 {len(inbox_list)}개" if ok and inbox_list
                        else ("업데이트 완료(새 이미지 없음)" if ok else "업데이트 실패(충돌 또는 네트워크)."))}


@app.get("/api/projects/{pid}/evidence")
def get_evidence(pid: str) -> dict:
    from . import evidence

    p = _proj(pid)
    return evidence.status(p)


@app.post("/api/projects/{pid}/evidence")
def build_evidence(pid: str) -> dict:
    from . import evidence

    p = _proj(pid)
    try:
        return {"ok": True, **evidence.build(p, log=lambda m: jobs.log_line(pid, m))}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/projects/{pid}/pronunciation")
def pronunciation(pid: str) -> dict:
    p = _proj(pid)
    wav_path = planner.project_dir(pid) / "out" / "audio_master.wav"
    if not wav_path.exists():
        raise HTTPException(400, "먼저 렌더해 주세요.")
    try:
        segs = tts.transcribe(wav_path)
    except Exception as e:
        raise HTTPException(400, str(e))
    heard = " ".join(s["text"] for s in segs)
    expected = [c["line"]["ko"] for c in p["cuts"] if c.get("line")]
    rows = tts.compare_pronunciation(expected, heard)
    return {"ok": True, "heard": heard, "rows": rows}


@app.post("/api/bundle")
def make_bundle(body: dict = Body(default={})) -> dict:
    try:
        return {"ok": True, **jobs.bundle(body.get("projects", []), body.get("title", ""))}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/open/{pid}")
def open_folder(pid: str) -> dict:
    d = planner.project_dir(pid)
    if not d.exists():
        raise HTTPException(404, "폴더가 없습니다.")
    if shutil.which("open"):
        subprocess.Popen(["open", str(d)])
    return {"ok": True, "path": str(d)}

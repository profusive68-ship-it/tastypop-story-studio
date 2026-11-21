"""업로드 카피 자동 생성: 유튜브(제목·설명·해시태그·고정댓글) + 인스타([Ep.N] 캡션·3지선다 투표·체크리스트)."""
from __future__ import annotations

from pathlib import Path

from . import fonts, planner, settings

HASHTAGS_BASE = [
    "#KFood", "#KoreanFood", "#ASMR", "#FoodAnimation", "#3DAnimation",
    "#TastyPopStory", "#케이푸드", "#음식애니메이션", "#mukbang", "#satisfying",
]


def _ep(project: dict) -> int:
    return int(project.get("ep") or 0)


def _tag(project: dict) -> str:
    return project.get("series_tag") or settings.load_settings()["content"].get("series_tag", "K-Food 3D")


def youtube_title(project: dict) -> str:
    """모바일 1줄 한·영 SEO 제목, 70자 이내, `| K-Food 3D Ep.N` 포함."""
    suffix = f" | {_tag(project)} Ep.{_ep(project)}"
    pri = (project.get("script", {}) or {}).get("youtube_title", "").strip()
    topic = project.get("topic", "")
    hook = project.get("hook", {}).get("text", "")
    candidates = [
        pri,
        f"{hook} {topic}, 3D K-Food ASMR" if hook else "",
        f"{topic} 3D ASMR | Would you eat this?",
        f"{topic} 3D 애니메이션 ASMR",
    ]
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        if c.endswith(suffix.strip()):
            title = c
        else:
            title = c + suffix
        title = title[:70].strip()
        if len(title) >= 12:
            return title
    return (topic + suffix)[:70]


def youtube_description(project: dict) -> str:
    ep = _ep(project)
    topic = project.get("topic", "")
    hook = project.get("hook", {}).get("text", "")
    is_drama = project.get("content_type") == "drama4"
    lines = []
    if is_drama:
        lines += [
            f"🍕 A 3D food squad drama — Ep.{ep}: {topic}",
            "Four K-food characters, one midnight kitchen, zero manners. English subtitles included.",
            "",
            f"🔥 3D 푸드 4인방 드라마 Ep.{ep} — {topic}",
            "새벽 2시 주방에서 벌어지는 4인방의 티키타카. 자막만 영어로, 소리는 그대로.",
            "",
            "⏱ Chapters / 타임스탬프",
            "00:00 Hook",
            f"00:00 ~ {project.get('total_seconds', 18)}s Full episode",
        ]
    else:
        lines += [
            f"🍜 {topic} — 3D ASMR in 18 seconds.",
            (f"Hook: {hook}" if hook else "Turn the sound on. That is the whole point."),
            "",
            f"🍜 {topic} — 18초 3D ASMR",
            "소리 켜고 보세요. 지글지글·바삭·치즈 늘어남을 그대로 담았습니다.",
            "",
            "⏱ 컷 리듬",
            "0.0s Hook → 0.9s 첫 컷 전환 → 중후반 대형 컷 → 루프",
        ]
    lines += [
        "",
        "🎬 AI disclosure / AI 고지",
        "This video was produced with AI-generated 3D animation, AI voice and synthesized sound design.",
        "본 영상은 AI로 제작된 3D 애니메이션과 AI 음성·합성 사운드를 사용했습니다.",
        "",
        "🔁 Seamless loop — the end is the beginning.",
        "♥ Like · ✈ Share · 🔖 Save for 2 AM",
        "",
        " ".join([f"#{_tag(project).replace(' ', '')}", f"#Ep{ep}"] + HASHTAGS_BASE),
    ]
    return "\n".join(lines)


def youtube_tags(project: dict) -> list[str]:
    topic = project.get("topic", "")
    return list(dict.fromkeys([
        topic, "K-Food", "Korean food", "3D animation", "food animation", "ASMR",
        "mukbang", "Tasty Pop Story", "케이푸드", "음식 애니메이션", "shorts", "asmr food",
    ]))


def youtube_pinned(project: dict) -> str:
    ep = _ep(project)
    topic = project.get("topic", "")
    if project.get("content_type") == "drama4":
        return (
            f"🎬 Ep.{ep} — 오늘 4인방 중 누가 제일 웃겼나요?\n"
            "① 피자설기(능글) ② 라면(다혈질) ③ 김밥 456(소심) ④ 핫도그(해맑)\n"
            "Which character made you laugh? Tell us in the comments 👇\n"
            f"#TastyPopStory #Ep{ep}"
        )
    return (
        f"🍜 Ep.{ep} — {topic}, 소리 켜고 보셨나요?\n"
        "다음 편에 보고 싶은 K-푸드를 댓글로 남겨주세요! (예: 김치찌개 / 붕어빵 / 마라탕)\n"
        "Which K-food should we animate next? Comment below 👇\n"
        f"#TastyPopStory #Ep{ep}"
    )


def instagram_caption(project: dict) -> str:
    ep = _ep(project)
    topic = project.get("topic", "")
    cfg = settings.load_settings()["instagram"]
    poll = cfg.get("poll_choices") or ["매운맛 도전", "치즈 폭탄", "다음 신메뉴"]
    is_drama = project.get("content_type") == "drama4"
    share = ("📩 DM this to the friend who would eat this at 2 AM 😳\n"
             "새벽 2시에 이거 먹을 친구에게 DM으로 보내기!")
    head = (f"[Ep.{ep}] 4인방 새벽 2시 주방 대소동 — {topic}" if is_drama
            else f"[Ep.{ep}] {topic} · 18초 3D ASMR")
    body = (
        "소리 켜고 보세요 🔥 새벽 2시에 이걸 보면 참을 수 없어요.\n"
        "Turn the sound on — 18 seconds of pure K-food ASMR."
        if not is_drama else
        "말 많은 4인방, 오늘도 새벽 2시에 주방 접수 완료 😂\n"
        "4 food characters, 1 midnight kitchen. English subtitles on."
    )
    return "\n".join([
        share,
        "",
        head,
        body,
        "",
        "🔖 Save for 2 AM cravings · ♥ Like · ✈ Share",
        "",
        f"👉 {poll[0]} vs {poll[1]} vs {poll[2]} — 여러분의 선택은? 댓글로!",
        "",
        " ".join([f"#{_tag(project).replace(' ', '')}", f"#Ep{ep}"] + HASHTAGS_BASE),
    ])


def instagram_pinned(project: dict) -> str:
    cfg = settings.load_settings()["instagram"]
    poll = cfg.get("poll_choices") or ["매운맛 도전", "치즈 폭탄", "다음 신메뉴"]
    return "\n".join([
        "🎯 오늘의 3지선다 (댓글로 번호만 남겨주세요!)",
        f"1️⃣ {poll[0]}",
        f"2️⃣ {poll[1]}",
        f"3️⃣ {poll[2]}",
        "",
        "Pick one 👇 1 / 2 / 3",
    ])


def instagram_checklist(project: dict) -> list[str]:
    cfg = settings.load_settings()["instagram"]
    items = list(cfg.get("checklist") or [])
    items += ["위치 태그: Jeju-do, South Korea", "고정댓글 → 댓글 길게 눌러 압정 고정"]
    return items


def build_all(project: dict) -> dict:
    data = {
        "youtube": {
            "title": youtube_title(project),
            "description": youtube_description(project),
            "tags": youtube_tags(project),
            "pinned": youtube_pinned(project),
        },
        "instagram": {
            "caption": instagram_caption(project),
            "pinned": instagram_pinned(project),
            "checklist": instagram_checklist(project),
        },
        "ai_disclosure": True,
    }
    project["youtube"] = data["youtube"]
    project["instagram"] = data["instagram"]
    planner.save_project(project)
    write_files(project, data)
    return data


def write_files(project: dict, data: dict | None = None) -> tuple[Path, Path]:
    pid = project["id"]
    d = planner.project_dir(pid) / "out"
    d.mkdir(parents=True, exist_ok=True)
    data = data or {"youtube": project.get("youtube", {}), "instagram": project.get("instagram", {})}
    yt = data.get("youtube", {})
    ig = data.get("instagram", {})
    yt_path = d / "upload_copy.txt"
    yt_path.write_text("\n".join([
        "=== 유튜브 제목 (복사해서 붙여넣기) ===",
        yt.get("title", ""),
        "",
        "=== 설명 ===",
        yt.get("description", ""),
        "",
        "=== 태그(쉼표 구분) ===",
        ", ".join(yt.get("tags", [])),
        "",
        "=== 고정댓글 ===",
        yt.get("pinned", ""),
        "",
    ]), encoding="utf-8")
    ig_path = d / "instagram_copy.txt"
    ig_path.write_text("\n".join([
        "=== 인스타 캡션 ===",
        ig.get("caption", ""),
        "",
        "=== 3지선다 고정댓글 ===",
        ig.get("pinned", ""),
        "",
        "=== 업로드 체크리스트 ===",
        *[f"[ ] {c}" for c in ig.get("checklist", [])],
        "",
    ]), encoding="utf-8")
    return yt_path, ig_path


def make_instagram_package(project: dict) -> Path:
    """인스타 릴스용 패키지: 영상 + 캡션 + 고정댓글 + 체크리스트 + 썸네일 → ZIP."""
    import shutil
    import zipfile

    d = planner.project_dir(project["id"])
    out = d / "out"
    pkg = out / "instagram_package"
    pkg.mkdir(parents=True, exist_ok=True)
    video = out / "final.mp4"
    if video.exists():
        shutil.copy2(video, pkg / f"Ep{project.get('ep')}_{planner.sanitize_filename(project.get('topic', ''))}.mp4")
    data = {"youtube": project.get("youtube", {}), "instagram": project.get("instagram", {})}
    if not data["instagram"].get("caption"):
        data = build_all(project)
    ig = data["instagram"]
    (pkg / "1_캡션.txt").write_text(ig.get("caption", ""), encoding="utf-8")
    (pkg / "2_고정댓글_3지선다.txt").write_text(ig.get("pinned", ""), encoding="utf-8")
    (pkg / "3_업로드_체크리스트.txt").write_text(
        "\n".join(f"[ ] {c}" for c in ig.get("checklist", [])), encoding="utf-8")
    thumb = out / "thumbnail.jpg"
    if thumb.exists():
        shutil.copy2(thumb, pkg / "4_썸네일.jpg")
    guide = (
        "인스타 릴스 업로드 순서\n"
        "1) 인스타 앱 → + → 릴스 → 위 MP4 선택\n"
        "2) 1_캡션.txt 내용 붙여넣기 (첫 줄 DM 공유 유도 문구 유지)\n"
        "3) 고급 설정: AI 정보 ON · 고화질 ON · Meta 음성 번역 OFF\n"
        "4) 위치: Jeju-do, South Korea\n"
        "5) 게시 후 2_고정댓글_3지선다.txt 를 댓글로 달고 길게 눌러 고정\n"
    )
    (pkg / "0_업로드_안내.txt").write_text(guide, encoding="utf-8")
    zip_path = out / f"인스타_릴스_패키지_Ep{project.get('ep')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pkg.iterdir()):
            z.write(f, f.name)
    return zip_path


def make_thumbnail(project: dict, cut_index: int = 0) -> Path | None:
    d = planner.project_dir(project["id"])
    kfs = sorted((d / "kf").glob("*.png")) + sorted((d / "kf").glob("*.jpg"))
    src = kfs[cut_index] if len(kfs) > cut_index else (kfs[0] if kfs else None)
    out = d / "out" / "thumbnail.jpg"
    hook = project.get("hook", {}).get("text", "")
    title = f"Ep.{_ep(project)} {project.get('topic', '')}"
    try:
        return fonts.thumbnail(src, title, hook, out)
    except Exception:
        return None

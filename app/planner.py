"""기획(플래너): 콘텐츠 타입별 컷 리듬·프로젝트 설계도(project.json)·이미지 요청서 생성.

채널 확정 연출 표준이 코드로 강제된다.
- 무대사 3D 음식 ASMR: 13.5~18초 / 9~10컷 / 0~3.3초 3컷 연타 + 중후반 대형 컷
- 4인방 캐릭터 숏폼 드라마: 16.5~21.5초 / 한국어 대사 + 영어 자막만
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import settings

DURATION_PRESETS = {
    "asmr3d": [13.5, 16.5, 18.0, 19.0],
    "drama4": [16.5, 18.0, 19.0, 21.5],
}

# 기본 ON: 이탈률 제로 & 저장/공유 공식
DEFAULT_OPTIONS = {
    "hook": True,          # 0.000~0.900초 영문 한 줄 훅 자막
    "flash": True,         # 0.000초 빛/임팩트
    "three_cut": True,     # 0~3.3초 3컷 연타(생성 시 리듬에 반영)
    "mid_device": True,    # 중간 이탈방지 장치(대형 컷 + 스우시)
    "wink_cta": True,      # 후반 좋아요 눈치 윙크 컷
    "cta": True,           # 마지막 1.5초 CTA
    "loop": True,          # 0.25초 무한루프
    "whoosh": True,        # 컷 전환 스우시
}

CONTENT_TYPES = {
    "asmr3d": {
        "label": "무대사 3D 음식 ASMR",
        "seconds": [13.5, 19.0],
        "cuts": [9, 10],
        "desc": "대사 0 · ASMR 사운드 · 컷 리듬 몰입",
    },
    "drama4": {
        "label": "4인방 캐릭터 숏폼 드라마",
        "seconds": [16.5, 21.5],
        "cuts": [8, 11],
        "desc": "한국어 대사 + 영어 자막만 · 성우별 피치/리버브",
    },
}

SQUAD = {
    "pizza": {"name": "피자설기", "role": "능글맞은 맏형", "voice": "ko-KR-HyunsuMultilingualNeural",
              "pitch": "-2Hz", "rate": "+3%", "color": "#F2B250"},
    "ramen": {"name": "라면", "role": "다혈질 둘째", "voice": "ko-KR-InJoonNeural",
              "pitch": "+2Hz", "rate": "+8%", "color": "#E2553B"},
    "gimbap": {"name": "김밥 456", "role": "소심한 막내", "voice": "ko-KR-SunHiNeural",
               "pitch": "+4Hz", "rate": "-4%", "color": "#4C8C4A"},
    "hotdog": {"name": "핫도그", "role": "해맑은 신입", "voice": "ko-KR-SunHiNeural",
               "pitch": "+9Hz", "rate": "+2%", "color": "#E8A33D"},
}

ASMR_STYLE = (
    "Pixar-style 3D animated food character short, cinematic soft studio lighting, "
    "glossy appetizing textures, shallow depth of field, ultra detailed, 9:16 vertical, "
    "subject centered inside the middle 78% safe area with generous margins, "
    "no text, no letters, no logo, no watermark, no border"
)


def ep_id(topic: str, ep: int | None = None) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "_", topic).strip("_")[:24] or "episode"
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    num = f"ep{int(ep):02d}_" if ep else ""
    return f"{num}{slug}_{stamp}"


def _round(x: float) -> float:
    return round(max(0.5, x), 2)


def build_rhythm(content_type: str, target_seconds: float, n_cuts: int | None = None) -> list[float]:
    """컷 리듬(초) 생성. 합은 정확히 target_seconds 가 된다."""
    ct = CONTENT_TYPES.get(content_type, CONTENT_TYPES["asmr3d"])
    lo, hi = ct["seconds"]
    T = min(max(target_seconds, lo), hi)
    if content_type == "drama4":
        n = n_cuts or 9
        n = max(8, min(11, n))
        first = [0.9]
        last = [1.8]
        body_n = max(1, n - 2)
        body_total = T - sum(first) - sum(last)
        # 대사 컷은 조금 길게, 사이사이 짧은 리액션 컷
        weights = []
        for i in range(body_n):
            weights.append(1.25 if i % 3 != 2 else 0.75)
        s = sum(weights)
        body = [_round(body_total * w / s) for w in weights]
        body[-1] = _round(body[-1] + (T - sum(first) - sum(last) - sum(body)))
        return first + body + last

    # 무대사 ASMR: 0~3.3초 3컷 연타 + 중후반 대형 컷 + 루프 리셋 엔딩
    n = n_cuts or 10
    n = max(9, min(10, n))
    first = [0.9, 1.0, 1.4]              # 3.3초
    last = [1.8]                          # 엔딩 윙크 / 루프 리셋
    big = 2.9                             # 중후반 대형 컷
    body_n = n - len(first) - len(last) - 1
    body_total = T - sum(first) - sum(last) - big
    base = body_total / max(body_n, 1)
    body = []
    for i in range(body_n):
        body.append(_round(base * (1.12 if i % 2 == 0 else 0.88)))
    # 대형 컷을 중후반(전체의 60~70% 지점)에 배치
    pos = max(1, min(len(body), int(round(body_n * 0.55))))
    body.insert(pos, _round(big))
    diff = T - (sum(first) + sum(body) + sum(last))
    body[-1] = _round(body[-1] + diff)
    return first + body + last


def create_project(
    topic: str,
    content_type: str = "asmr3d",
    mode: str = "semi",
    target_seconds: float | None = None,
    ep: int | None = None,
    hook_text: str | None = None,
    scenes: list[dict] | None = None,
    script: dict | None = None,
) -> dict:
    ct = CONTENT_TYPES.get(content_type, CONTENT_TYPES["asmr3d"])
    s = settings.load_settings()
    if ep is None:
        ep = int(s["content"].get("ep_start", 8))
    if target_seconds is None:
        target_seconds = 18.0 if content_type == "asmr3d" else 18.5
    durations = build_rhythm(content_type, target_seconds, len(scenes) if scenes else None)
    cuts = []
    for i, dur in enumerate(durations):
        sc = (scenes[i] if scenes and i < len(scenes) else {})
        cuts.append({
            "id": i + 1,
            "dur": round(dur, 2),
            "scene": sc.get("scene", ""),
            "motion": sc.get("motion", "slow push-in, appetizing steam"),
            "sfx": sc.get("sfx", ["crunch_bite"] if content_type == "asmr3d" else ["pop_bubble"]),
            "line": sc.get("line"),
            "kf": f"kf/c{i+1:02d}.png",
            "clip": f"clips/c{i+1:02d}.mp4",
            "enabled": True,
        })
    project = {
        "id": ep_id(topic, ep),
        "ep": int(ep),
        "topic": topic,
        "content_type": content_type,
        "content_label": ct["label"],
        "mode": mode,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "draft",
        "target_seconds": round(target_seconds, 2),
        "total_seconds": round(sum(d["dur"] for d in cuts), 2),
        "hook": {
            "text": hook_text or ("Would you eat this?" if content_type == "asmr3d" else "Only 4 of them. At 2 AM."),
            "start": 0.0,
            "end": 0.9,
        },
        "cta": {
            "like": s["content"].get("cta_like", "♥ Like · ✈ Share"),
            "save": s["content"].get("cta_save", "Save for 2 AM"),
            "seconds": 1.5,
        },
        "options": {
            **DEFAULT_OPTIONS,
            "flash": bool(s["content"].get("hook_flash", True)),
            "whoosh": bool(s["content"].get("cut_whoosh", True)),
        },
        "upload_method": "youtube",
        "cuts": cuts,
        "script": script or {},
        "voices": SQUAD if content_type == "drama4" else {},
        "series_tag": s["content"].get("series_tag", "K-Food 3D"),
        "youtube": {},
        "instagram": {},
        "render": {},
        "files": {},
        "log": [],
    }
    save_project(project)
    write_image_request(project)
    return project


def project_dir(pid: str) -> Path:
    return settings.PROJECTS_DIR / pid


def project_file(pid: str) -> Path:
    return project_dir(pid) / "project.json"


def save_project(project: dict) -> dict:
    project["updated_at"] = datetime.now().isoformat(timespec="seconds")
    project["total_seconds"] = round(sum(c["dur"] for c in project.get("cuts", []) if c.get("enabled", True)), 2)
    d = project_dir(project["id"])
    for sub in ("kf", "clips", "out", "voice", "logs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    return project


def load_project(pid: str) -> dict:
    p = project_file(pid)
    if not p.exists():
        raise FileNotFoundError(f"프로젝트를 찾을 수 없습니다: {pid}")
    return json.loads(p.read_text(encoding="utf-8"))


def list_projects() -> list[dict]:
    settings.ensure_dirs()
    out = []
    for p in sorted(settings.PROJECTS_DIR.glob("*/project.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "id": data.get("id", p.parent.name),
            "ep": data.get("ep"),
            "topic": data.get("topic", ""),
            "content_type": data.get("content_type", ""),
            "content_label": data.get("content_label", ""),
            "mode": data.get("mode", ""),
            "status": data.get("status", "draft"),
            "total_seconds": data.get("total_seconds"),
            "created_at": data.get("created_at"),
            "cuts": len(data.get("cuts", [])),
            "has_video": (p.parent / "out" / "final.mp4").exists(),
            "thumb": (p.parent / "out" / "thumbnail.jpg").exists(),
        })
    return out


def scene_prompts(project: dict) -> list[str]:
    """컷별 이미지 생성 프롬프트(영어). 안전영역·스타일 고정."""
    out = []
    for c in project["cuts"]:
        base = c.get("scene") or f"{project['topic']} hero moment"
        if project["content_type"] == "drama4":
            cast = ", ".join(f"{v['name']} ({v['role']})" for v in SQUAD.values())
            style = (
                f"Pixar-style 3D animated food character drama still, cast: {cast}, "
                "expressive eyes, cinematic rim light, cozy midnight kitchen background, "
                "9:16 vertical, characters inside the middle 78% safe area, no text, no logo, no watermark"
            )
        else:
            style = ASMR_STYLE
        out.append(f"{base}. {style}.")
    return out


def write_image_request(project: dict) -> Path:
    """0원 협업 모드: 사용자가 케빈에게 그대로 넘길 이미지 요청서."""
    d = project_dir(project["id"])
    lines = [
        f"# {project['topic']} — 이미지 요청서 ({project['content_label']})",
        f"- 프로젝트: {project['id']}",
        f"- 총 {len(project['cuts'])}컷 / {project['total_seconds']}초 / 1080x1920 (9:16)",
        "- 아래 순서대로 컷 이미지를 만들어 `kf/` 폴더에 `c01.png`, `c02.png` … 로 저장하세요.",
        "- 키프레임은 세로 9:16, 인물·음식은 화면 가운데 78% 안전영역 안에, 글자·로고·워터마크 없이.",
        "- 영상 클립을 직접 넣을 경우에는 `clips/` 폴더에 `c01.mp4` … 로 저장하면 그대로 조립됩니다.",
        "",
    ]
    prompts = scene_prompts(project)
    for i, (c, pr) in enumerate(zip(project["cuts"], prompts), 1):
        sub = ""
        if c.get("line"):
            ln = c["line"]
            speaker = SQUAD.get(ln.get("speaker", ""), {}).get("name", ln.get("speaker", ""))
            sub = f"  · 대사({speaker}): {ln.get('ko','')} / EN: {ln.get('en','')}"
        lines += [
            f"## CUT {i:02d} — {c['dur']}초",
            f"- 파일명: kf/c{i:02d}.png",
            f"- 장면: {c.get('scene') or '(장면 설명 없음)'}",
            f"- 움직임: {c.get('motion') or 'slow push-in'}",
            f"- 사운드: {', '.join(c.get('sfx') or [])}",
        ]
        if sub:
            lines.append(sub)
        lines += [f"- 프롬프트: {pr}", ""]
    p = d / "이미지_요청서.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


SCRIPT_SYSTEM = """너는 K-푸드 3D 애니메이션 쇼츠 전문 작가다.
반드시 JSON 만 출력한다. 설명·코드블록 없이 JSON 객체 하나만."""

SCRIPT_SCHEMA = {
    "topic": "음식 이름(한글)",
    "hook": "영문 한 줄 훅 (3~6단어, 의문형 권장, 예: Would you eat this?)",
    "cuts": [
        {
            "scene": "영어 이미지 프롬프트용 장면 묘사 (인물/음식/표정/구도)",
            "motion": "영어 카메라/움직임 묘사",
            "sfx": ["crunch_bite"],
            "line": {"speaker": "pizza|ramen|gimbap|hotdog", "ko": "한국어 대사", "en": "English subtitle"},
        }
    ],
    "youtube_title": "70자 이내 한·영 제목",
    "description": "한·영 설명 + 해시태그",
    "pinned_comment": "한·영 고정댓글",
}


def script_prompt(project: dict, extra: str = "") -> str:
    ct = project["content_type"]
    n = len(project["cuts"])
    durs = [c["dur"] for c in project["cuts"]]
    if ct == "asmr3d":
        guide = (
            "무대사 3D 음식 ASMR 쇼츠다. line 은 절대 만들지 마라(대사 0). "
            "0~3.3초는 3컷 연타로 시선을 붙잡고, 중후반에 대형 컷 1개로 몰입을 최대로 올린다. "
            "마지막 컷은 다음 편으로 자연스럽게 이어지는 여운(윙크·하트·재료 클로즈업)으로 끝낸다. "
            "사운드는 지글지글/바삭/치즈/후루룩/탄산/칼질/삐빅/스우시 중에서 고른다."
        )
    else:
        guide = (
            "4인방(피자설기=능글 맏형, 라면=다혈질 둘째, 김밥 456=소심 막내, 핫도그=해맑은 신입) "
            "3D 음식 캐릭터 숏폼 드라마다. 모든 컷에 line 을 넣고 한국어 대사는 짧고 웃기게(6~14자), "
            "en 은 자연스러운 영어 자막(2~6단어)으로 쓴다. 첫 0.9초 컷에 훅이 터지고, 마지막 컷은 하트 윙크 엔딩."
        )
    return f"""{guide}

주제: {project['topic']}
컷 수: {n}개, 컷별 길이(초): {durs}
시리즈: {project['series_tag']} Ep.{project['ep']}
오디언스: 한국·해외 동시 (한국어 대사 + 영어 자막, 영어 훅)
추가 요청: {extra or '없음'}

아래 JSON 스키마 그대로 출력:
{json.dumps(SCRIPT_SCHEMA, ensure_ascii=False, indent=2)}

조건:
- cuts 개수는 정확히 {n}개, 각 cut 의 scene/motion 은 영어.
- {"line 은 넣지 않는다." if ct == "asmr3d" else "모든 cut 에 line 을 넣는다(speaker 는 pizza/ramen/gimbap/hotdog 중 하나)."}
- youtube_title 은 70자 이내, 끝에 `| {project['series_tag']} Ep.{project['ep']}` 포함.
- description 은 영어 2~3줄 + 한국어 2~3줄 + 해시태그 8개 이상.
- 과장·미확인 사실 금지, 음식은 실제 K-푸드."
"""


def apply_script(project: dict, script: dict) -> dict:
    """LLM 대본을 프로젝트 컷에 반영(컷 수·길이는 표준 리듬 유지)."""
    cuts_in = script.get("cuts") or []
    for i, c in enumerate(project["cuts"]):
        if i < len(cuts_in):
            s = cuts_in[i]
            c["scene"] = s.get("scene") or c["scene"]
            c["motion"] = s.get("motion") or c["motion"]
            if s.get("sfx"):
                c["sfx"] = s["sfx"]
            if project["content_type"] == "drama4" and s.get("line"):
                c["line"] = s["line"]
            if project["content_type"] == "asmr3d":
                c["line"] = None
    if script.get("hook"):
        project["hook"]["text"] = script["hook"]
    if script.get("topic"):
        project["topic"] = script["topic"]
    project["script"] = {
        "youtube_title": script.get("youtube_title", ""),
        "description": script.get("description", ""),
        "pinned_comment": script.get("pinned_comment", ""),
        "raw": script,
    }
    save_project(project)
    write_image_request(project)
    return project


def sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    return re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()


# ---------------------------------------------------- 이미지·클립 자동 감지
KF_EXT = [".png", ".jpg", ".jpeg", ".webp"]
CLIP_EXT = [".mp4", ".mov", ".m4v", ".webm"]


def kf_names(cut_id: int) -> list[str]:
    """c01.png · kf01.png · kf1.png · 01.png 등 사용자가 어떤 이름으로 넣어도 인식."""
    names = []
    for stem in (f"c{cut_id:02d}", f"kf{cut_id:02d}", f"kf{cut_id}", f"{cut_id:02d}", str(cut_id)):
        for ext in KF_EXT:
            names.append(f"{stem}{ext}")
    return names


def clip_names(cut_id: int) -> list[str]:
    names = []
    for stem in (f"c{cut_id:02d}", f"kf{cut_id:02d}", f"kf{cut_id}", f"clip{cut_id}", f"{cut_id:02d}", str(cut_id)):
        for ext in CLIP_EXT:
            names.append(f"{stem}{ext}")
    return names


def resolve_media(project: dict, cut: dict) -> tuple[Path | None, str]:
    d = project_dir(project["id"])
    for n in clip_names(int(cut["id"])):
        p = d / "clips" / n
        if p.exists() and p.stat().st_size > 1000:
            return p, "clip"
    for n in kf_names(int(cut["id"])):
        p = d / "kf" / n
        if p.exists() and p.stat().st_size > 500:
            return p, "image"
    return None, "none"


def media_status(project: dict) -> dict:
    """kf/ · clips/ 자동 감지 → 초록불 여부."""
    rows = []
    for c in project["cuts"]:
        p, kind = resolve_media(project, c)
        rows.append({"id": c["id"], "found": p is not None, "kind": kind,
                     "file": p.name if p else "", "dur": c["dur"]})
    found = sum(1 for r in rows if r["found"])
    total = len(rows)
    return {
        "cuts": rows,
        "found": found,
        "total": total,
        "ready": found == total and total > 0,
        "can_render": found > 0,
        "missing": [r["id"] for r in rows if not r["found"]],
    }


def import_images(pid: str, src_dir: Path, overwrite: bool = False) -> dict:
    """assets_inbox 폴더의 kf1.png ~ kf10.png 를 프로젝트 kf/c01.png … 로 복사."""
    import shutil

    project = load_project(pid)
    d = project_dir(pid) / "kf"
    d.mkdir(parents=True, exist_ok=True)
    src = Path(src_dir)
    files = [p for p in sorted(src.iterdir()) if p.suffix.lower() in KF_EXT] if src.is_dir() else []
    if not files:
        raise FileNotFoundError(f"이미지가 없습니다: {src}")
    copied, skipped = [], []
    # 파일명에서 숫자를 뽑아 컷 번호로 매칭(kf3.png → 3, 3.png → 3), 없으면 순서대로
    parsed = []
    for p in files:
        m = re.search(r"(\d+)", p.stem)
        parsed.append((int(m.group(1)) if m else None, p))
    if all(n is not None for n, _ in parsed):
        parsed.sort(key=lambda x: x[0])
    for idx, (num, p) in enumerate(parsed, start=1):
        cut_id = num if num and 1 <= num <= len(project["cuts"]) else idx
        if cut_id > len(project["cuts"]):
            continue
        dst = d / f"c{cut_id:02d}{p.suffix.lower()}"
        if dst.exists() and not overwrite:
            skipped.append(dst.name)
            continue
        shutil.copy2(p, dst)
        copied.append(dst.name)
    st = media_status(project)
    return {"copied": copied, "skipped": skipped, "media": st, "project_id": pid}


def inbox_folders() -> list[dict]:
    """assets_inbox/ 안의 이미지 묶음 폴더 목록."""
    root = settings.APP_DIR / "assets_inbox"
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not d.is_dir() or d.name.startswith("."):
            continue
        imgs = [p for p in d.iterdir() if p.suffix.lower() in KF_EXT]
        if not imgs:
            continue
        out.append({"folder": d.name, "path": str(d), "count": len(imgs),
                    "files": sorted(p.name for p in imgs),
                    "updated": datetime.fromtimestamp(d.stat().st_mtime).isoformat(timespec="minutes")})
    return out

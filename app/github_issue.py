"""GitHub Issue 로 케빈에게 이미지·클립 작업을 요청한다.

세 가지 종류
- image_request  : 키프레임 이미지 처음 요청  (라벨 image-request,  target_inbox assets_inbox/{pid})
- image_revision : 이미 받은 컷의 이미지 수정   (라벨 image-revision, target_inbox assets_inbox/{pid})
- video_request  : 움직이는 클립(.mp4) 제작    (라벨 video-request,  target_inbox assets_inbox/{pid}/clips)

두 가지 길이 있다.
- 토큰(config/settings.json 의 keys.github_token)이 있으면
  POST /repos/{repo}/issues 로 이슈를 바로 만들고 이슈 URL 을 돌려준다.
- 토큰이 없으면 /issues/new?title=…&body=… URL 을 만들어 새 탭(사파리)으로 연다.
  사용자는 [Submit new issue] 한 번만 누르면 된다.

이슈 본문에는 항상 `target_inbox: …` 를 넣어
케빈이 정확한 폴더명으로 Push 할 수 있게 한다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import planner, settings

API_ROOT = "https://api.github.com"
WEB_ROOT = "https://github.com"

# GitHub 새 이슈 화면이 감당하는 URL 길이(안전선). 넘으면 본문을 줄여서 만든다.
# GitHub 새 이슈 화면은 URL 이 너무 길면 500(Whoops!)을 낸다.
# 한글은 URL 인코딩에서 3배로 늘어나, 실제로는 URL 약 4,200자 부근부터 실패가 보였다
# (4,203자 성공/실패가 갈리고 4,297자 실패) → 여유 있게 3,000자를 안전선으로 쓴다.
MAX_URL_CHARS = 3000
# URL 본문에 넣는 지시문 길이 상한(넘으면 잘라 쓰고, 전문은 [요청서 복사]에 담긴다).
URL_NOTE_CHARS = 400

# 종류별 기본값(설정 config/settings.json 의 github 섹션에서 바꿀 수 있다)
KINDS = {
    "image_request": {
        "label": "image-request", "prefix": "[이미지요청]",
        "inbox_suffix": "", "heading": "📤 키프레임 이미지 요청",
    },
    "image_revision": {
        "label": "image-revision", "prefix": "[이미지수정]",
        "inbox_suffix": "", "heading": "✏️ 이미지 수정 요청",
    },
    "video_request": {
        "label": "video-request", "prefix": "[영상제작]",
        "inbox_suffix": "/clips", "heading": "🎬 영상(클립) 제작 요청",
    },
}
DEFAULT_KIND = "image_request"


def _cfg() -> dict:
    return settings.load_settings().get("github") or {}


def kind_of(kind: str | None) -> str:
    k = (kind or DEFAULT_KIND).strip()
    aliases = {"revision": "image_revision", "image": "image_request",
               "video": "video_request", "clip": "video_request"}
    k = aliases.get(k, k)
    return k if k in KINDS else DEFAULT_KIND


def repo() -> str:
    cfg = _cfg()
    return (cfg.get("repo") or "profusive68-ship-it/tastypop-story-studio").strip().strip("/")


def label(kind: str | None = None) -> str:
    k = kind_of(kind)
    cfg = _cfg()
    key = {"image_request": "issue_label", "image_revision": "revision_label",
           "video_request": "video_label"}[k]
    return (cfg.get(key) or KINDS[k]["label"]).strip()


def title_prefix(kind: str | None = None) -> str:
    k = kind_of(kind)
    cfg = _cfg()
    key = {"image_request": "issue_title_prefix", "image_revision": "revision_title_prefix",
           "video_request": "video_title_prefix"}[k]
    return (cfg.get(key) or KINDS[k]["prefix"]).strip()


def token() -> str:
    return settings.github_token()


def target_inbox(ep_id: str, kind: str | None = None) -> str:
    """케빈이 파일을 올릴 정확한 폴더."""
    suffix = KINDS[kind_of(kind)]["inbox_suffix"]
    return f"assets_inbox/{ep_id}{suffix}"


def build_title(project: dict, kind: str | None = None) -> str:
    return f"{title_prefix(kind)} {project['id']} - {project.get('topic', '')}".strip()


def _cut_brief(project: dict, prompt: str, limit: int = 240) -> str:
    c_note = prompt.strip()
    if len(c_note) > limit:
        c_note = c_note[:limit].rstrip() + "…"
    return c_note


def build_body(project: dict, note: str = "", compact: bool = False,
               kind: str | None = None) -> str:
    """이슈 본문. compact=True 면 URL 길이 제한에 맞춘 요약본."""
    k = kind_of(kind)
    if k == "image_revision":
        return _body_revision(project, note, compact)
    if k == "video_request":
        return _body_video(project, note, compact)
    return _body_image(project, note, compact)


def _footer() -> str:
    return f"_자동 생성: {datetime.now():%Y-%m-%d %H:%M} · TastyPop Studio v{settings.VERSION}_"


def _body_image(project: dict, note: str = "", compact: bool = False) -> str:
    pid = project["id"]
    inbox = target_inbox(pid)
    n = len(project["cuts"])
    lines = [
        "## 📤 키프레임 이미지 요청 (TastyPop Studio 자동 생성)",
        "",
        f"target_inbox: {inbox}",
        "",
        f"- 프로젝트 ID(ep_id): `{pid}`  ← **이 값이 폴더명입니다**",
        f"- 올릴 위치: `{inbox}/` (저장소 최상위 `assets_inbox/` 아래)",
        f"- 올릴 파일: `kf1.png` ~ `kf{n}.png` (숫자 = 컷 번호) 또는 `c01.png` ~ `c{n:02d}.png`",
        f"- 형식: png/jpg/webp · 세로 9:16(1080x1920 권장) · 글자·로고·워터마크 없이",
        f"- 컷 수: {n}개 / 길이: {project.get('total_seconds')}초 / 시리즈: {project.get('series_tag')} "
        f"Ep.{project.get('ep')}",
        f"- 주제: {project.get('topic')}",
        "",
        "> 맥북은 이 폴더에 이미지가 들어오면 20초 안에 자동으로 내려받아 `kf/` 에 넣고,",
        "> 전 컷이 차는 순간 영상 조립까지 자동으로 시작합니다.",
        "",
    ]
    if note.strip():
        lines += ["### 요청 메모", note.strip(), ""]
    lines += ["### 컷별 요청", ""]
    prompts = planner.scene_prompts(project)
    for i, (c, pr) in enumerate(zip(project["cuts"], prompts), 1):
        scene = (c.get("scene") or "").strip()
        if compact:
            lines.append(f"{i:02d}. `kf{i}.png` · {c['dur']}초 · {scene[:110] or '장면 자유'}")
        else:
            lines += [
                f"**CUT {i:02d} — {c['dur']}초**",
                f"- 파일명: `kf{i}.png` (또는 `c{i:02d}.png`)",
                f"- 장면: {scene or '(장면 설명 없음)'}",
                f"- 움직임: {c.get('motion') or 'slow push-in'}",
                f"- 사운드: {', '.join(c.get('sfx') or [])}",
                f"- 프롬프트: {pr}",
                "",
            ]
    lines += ["---", _footer()]
    return "\n".join(lines)


def _body_revision(project: dict, note: str = "", compact: bool = False) -> str:
    """이미 받은 키프레임 중 일부를 고쳐 달라는 요청."""
    pid = project["id"]
    inbox = target_inbox(pid)
    n = len(project["cuts"])
    st = planner.media_status(project)
    have = [r["id"] for r in st["cuts"] if r["found"]]
    lines = [
        "## ✏️ 이미지 수정 요청 (TastyPop Studio 자동 생성)",
        "",
        f"target_inbox: {inbox}",
        "",
        f"- 프로젝트 ID(ep_id): `{pid}`",
        f"- 주제: {project.get('topic')} · {n}컷 / {project.get('total_seconds')}초",
        f"- 지금 받은 컷: {len(have)}/{n}" + (f" (`{', '.join('kf' + str(x) for x in have)}`)" if have else ""),
        f"- 수정한 이미지는 **같은 폴더에 같은 파일명**으로 다시 올려 주세요 (`kf3.png` 는 `kf3.png` 로).",
        f"- 파일명 규칙: `kf1.png` ~ `kf{n}.png` (숫자 = 컷 번호) 또는 `c01.png` ~ `c{n:02d}.png`",
        "- 형식: png/jpg/webp · 세로 9:16(1080x1920 권장) · 글자·로고·워터마크 없이",
        "",
        "> 맥북은 20초마다 이 폴더를 확인합니다. 내용이 바뀐 파일만 자동으로 교체하고,",
        "> 전 컷이 준비되면 영상까지 자동으로 다시 조립합니다(이미 만든 영상도 덮어씁니다).",
        "",
        "### 수정 지시",
        (note.strip() or "(수정 지시가 비어 있습니다 — 컷 번호와 원하는 변경을 적어 주세요)"),
        "",
        "### 지금 컷별 장면 (참고)",
    ]
    for i, c in enumerate(project["cuts"], 1):
        scene = (c.get("scene") or "").strip()
        if compact:
            lines.append(f"{i:02d}. `kf{i}.png` · {c['dur']}초 · {scene[:80] or '장면 자유'}")
        else:
            lines.append(f"{i:02d}. `kf{i}.png` · {c['dur']}초 · {scene or '장면 자유'} · 움직임: {c.get('motion') or 'slow push-in'}")
    lines += ["---", _footer()]
    return "\n".join(lines)


def _body_video(project: dict, note: str = "", compact: bool = False) -> str:
    """정지 이미지를 움직이는 클립(.mp4)으로 뽑아 달라는 요청."""
    pid = project["id"]
    inbox = target_inbox(pid, "video_request")
    n = len(project["cuts"])
    st = planner.media_status(project)
    lines = [
        "## 🎬 영상(클립) 제작 요청 (TastyPop Studio 자동 생성)",
        "",
        f"target_inbox: {inbox}",
        "",
        f"- 프로젝트 ID(ep_id): `{pid}`",
        f"- 주제: {project.get('topic')} · {project.get('content_label')}",
        f"- 컷 수: **{n}개** / 총 길이: {project.get('total_seconds')}초 / 시리즈: {project.get('series_tag')} Ep.{project.get('ep')}",
        f"- 지금 있는 키프레임(정지 이미지): {st['found']}/{n}컷 → 이 이미지들을 시작 프레임으로 씨앗(Kling image-to-video)으로 쓰면 됩니다.",
        f"- 올릴 위치: `{inbox}/` 에 `c01.mp4` ~ `c{n:02d}.mp4` (숫자 = 컷 번호, `kf1.mp4` 형식도 인식)",
        "- 형식: mp4(h.264) · 세로 9:16(1080x1920) · 무음(사운드는 맥북이 입힘) · 3~5초 길이면 충분(컷 길이만큼 잘라 씀)",
        "",
        "> 클립이 도착하면 맥북이 20초 안에 자동으로 `clips/` 에 넣고,",
        "> **정지 이미지로 만들어 둔 final.mp4 가 있어도 자동으로 다시 조립해 움직이는 버전으로 덮어씁니다.**",
        "",
    ]
    if note.strip():
        lines += ["### 요청 메모", note.strip(), ""]
    lines += ["### 컷별 클립 지시", ""]
    for i, c in enumerate(project["cuts"], 1):
        scene = (c.get("scene") or "").strip()
        motion = (c.get("motion") or "slow push-in").strip()
        sfx = ", ".join(c.get("sfx") or [])
        if compact:
            lines.append(f"{i:02d}. `c{i:02d}.mp4` · {c['dur']}초 · 움직임: {motion[:70]}")
        else:
            lines += [
                f"**CUT {i:02d} — {c['dur']}초**",
                f"- 파일명: `c{i:02d}.mp4` (또는 `kf{i}.mp4`)",
                f"- 시작 이미지: `kf{i}.png`",
                f"- 장면: {scene or '(장면 설명 없음)'}",
                f"- 움직임: {motion}",
                f"- 사운드(참고): {sfx}",
                "",
            ]
    lines += ["---", _footer()]
    return "\n".join(lines)


def build_url_body(project: dict, note: str = "", cut_lines: int | None = None,
                   kind: str | None = None) -> str:
    """주소(URL)에 담는 짧은 본문.

    GitHub 은 /issues/new 의 URL 이 길면 500(Whoops!)을 낸다. 한글은 URL 인코딩에서
    3배로 늘어나므로, 실제 확인한 한계(URL 약 4,200자 부근에서 실패)보다 훨씬 아래인
    MAX_URL_CHARS(3,000자)를 안전선으로 쓰고, 넘치면 컷 줄부터 덜어 낸다.
    """
    k = kind_of(kind)
    pid = project["id"]
    inbox = target_inbox(pid, k)
    n = len(project["cuts"])
    nb = note.strip()
    if len(nb) > URL_NOTE_CHARS:
        nb = nb[:URL_NOTE_CHARS].rstrip() + "…"

    if k == "image_request":
        lines = [
            "## 📤 키프레임 이미지 요청",
            f"target_inbox: {inbox}",
            f"- 올릴 곳: `{inbox}/` 에 `kf1.png` ~ `kf{n}.png` (숫자=컷 번호)",
            f"- {n}컷 / {project.get('total_seconds')}초 / 9:16 · 글자·로고·워터마크 없이",
            f"- 프로젝트: `{pid}`",
            f"- 주제: {project.get('topic')}",
        ]
        if nb:
            lines.append(f"- 메모: {nb[:200]}")
        lines += ["", "### 컷별 장면"]
        prompts = planner.scene_prompts(project)
        rows = []
        for i, (c, pr) in enumerate(zip(project["cuts"], prompts), 1):
            scene = (c.get("scene") or "").strip() or pr.split(".")[0].strip()
            rows.append(f"{i:02d} · kf{i}.png · {c['dur']}초 · {scene[:110]}")
        keep = len(rows) if cut_lines is None else max(0, min(cut_lines, len(rows)))
        lines += rows[:keep]
        if keep < len(rows):
            lines.append(f"… 그 외 {len(rows) - keep}컷은 [📋 요청서 복사] 참고")
        return "\n".join(lines)

    rows = []
    for i, c in enumerate(project["cuts"], 1):
        scene = (c.get("scene") or "").strip()
        motion = (c.get("motion") or "slow push-in").strip()
        if k == "image_revision":
            rows.append(f"{i:02d} · kf{i}.png · {c['dur']}초 · {scene[:70] or '장면 자유'}")
        else:
            rows.append(f"{i:02d} · c{i:02d}.mp4 · {c['dur']}초 · {motion[:60]}")
    keep = len(rows) if cut_lines is None else max(0, min(cut_lines, len(rows)))

    if k == "image_revision":
        head = [
            "## ✏️ 이미지 수정 요청",
            f"target_inbox: {inbox}",
            f"- 프로젝트: `{pid}` · {n}컷 / {project.get('total_seconds')}초",
            f"- 수정한 이미지는 **같은 폴더에 같은 파일명**으로 다시 올려 주세요 (`kf1.png` ~ `kf{n}.png`).",
            "",
            "### 수정 지시",
            nb or "(지시 없음 — 앱에서 다시 적어 주세요)",
            "",
            "### 지금 컷별 장면",
        ]
    else:
        head = [
            "## 🎬 영상(클립) 제작 요청",
            f"target_inbox: {inbox}",
            f"- 프로젝트: `{pid}` · **{n}컷** / {project.get('total_seconds')}초 · {project.get('series_tag')} Ep.{project.get('ep')}",
            f"- 올릴 파일: `c01.mp4` ~ `c{n:02d}.mp4` (mp4 · 9:16 · 무음) · 시작 이미지 `kfN.png`",
            f"- 클립이 도착하면 정지 이미지로 만든 final.mp4 도 자동으로 다시 조립됩니다.",
            "",
        ]
        if nb:
            head += ["### 요청 메모", nb, ""]
        head += ["### 컷별 움직임"]
    out = head + rows[:keep]
    if keep < len(rows):
        out.append(f"… 그 외 {len(rows) - keep}컷은 [📋 요청서 복사] 참고")
    return "\n".join(out)


def _minimal_url_body(project: dict, note: str, kind: str | None = None) -> str:
    """URL 이 아무리 넘쳐도 target_inbox 와 지시만은 남기는 최소 본문."""
    k = kind_of(kind)
    pid = project["id"]
    inbox = target_inbox(pid, k)
    what = "이미지 수정" if k == "image_revision" else "영상(클립) 제작"
    lines = [f"## {'✏️' if k == 'image_revision' else '🎬'} {what} 요청",
             f"target_inbox: {inbox}",
             f"- 프로젝트: `{pid}` · {len(project['cuts'])}컷 / {project.get('total_seconds')}초",
             f"- 주제: {project.get('topic')}"]
    if note.strip():
        lines += ["", "### 지시", note.strip()[:400]]
    lines += ["", "… 컷별 상세는 앱의 [📋 요청서 복사]를 붙여넣어 주세요."]
    return "\n".join(lines)


def _make_url(title: str, body: str, kind: str | None = None) -> str:
    base = f"{WEB_ROOT}/{repo()}/issues/new"
    return (f"{base}?title={quote(title, safe='')}&body={quote(body, safe='')}"
            f"&labels={quote(label(kind), safe='')}")


def issue_url(project: dict, title: str, note: str = "", kind: str | None = None) -> str:
    """토큰 없이 쓰는 '1클릭 Submit' URL.

    URL 이 MAX_URL_CHARS 를 넘으면 컷 줄을 2개씩 덜어 내고, 그래도 넘치면
    target_inbox + 지시만 담은 최소 본문으로 줄인다(지시 내용은 절대 잃지 않는다).
    """
    n = len(project["cuts"])
    keep = n
    while True:
        url = _make_url(title, build_url_body(project, note, cut_lines=keep, kind=kind), kind)
        if len(url) <= MAX_URL_CHARS or keep <= 0:
            break
        keep = max(0, keep - 2)
    if len(url) <= MAX_URL_CHARS:
        return url
    for cut in (600, 300, 150, 60):
        url = _make_url(title, _minimal_url_body(project, note[:cut], kind), kind)
        if len(url) <= MAX_URL_CHARS:
            return url
    return _make_url(title, f"target_inbox: {target_inbox(project['id'], kind)}", kind)


def new_issue_url(title: str, body: str, labels: list[str] | None = None,
                  kind: str | None = None) -> str:
    """이미 만들어진 본문을 담는 URL — 길면 잘라 낸다(프로젝트를 모를 때용)."""
    url = _make_url(title, body, kind)
    if len(url) <= MAX_URL_CHARS:
        return url
    for limit in (2400, 1600, 1000, 500):
        short = body[:limit].rstrip() + "\n\n… (본문이 길어 줄였습니다. 앱의 [📋 케빈에게 이미지 요청하기] > [요청서 복사]를 쓰세요.)"
        url = _make_url(title, short, kind)
        if len(url) <= MAX_URL_CHARS:
            return url
    return _make_url(title, "요청서가 길어 본문을 담지 못했습니다. 앱의 [요청서 복사]를 쓰세요.", kind)


def _post_issue(title: str, body: str, tok: str, labels: list[str]) -> dict:
    """GitHub REST 로 이슈 생성. 실패는 예외 대신 dict 로 돌려준다."""
    import requests

    url = f"{API_ROOT}/repos/{repo()}/issues"
    headers = {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "TastyPop-Studio",
    }
    payload: dict = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    try:
        r = requests.post(url, data=json.dumps(payload), headers=headers, timeout=25)
    except Exception as e:
        return {"ok": False, "status": 0, "error": f"네트워크 오류: {e}", "labels_omitted": False}
    if r.status_code in (200, 201):
        try:
            data = r.json()
        except Exception:
            data = {}
        return {"ok": True, "status": r.status_code, "issue": data,
                "url": data.get("html_url", ""), "number": data.get("number"),
                "labels_omitted": False}
    # 라벨이 저장소에 없으면 422 가 온다 → 라벨 없이 한 번 더
    text = (r.text or "")[:400]
    if r.status_code == 422 and labels:
        payload.pop("labels", None)
        try:
            r2 = requests.post(url, data=json.dumps(payload), headers=headers, timeout=25)
        except Exception as e:
            return {"ok": False, "status": r.status_code, "error": f"{text} / 재시도 실패: {e}",
                    "labels_omitted": False}
        if r2.status_code in (200, 201):
            data = r2.json()
            return {"ok": True, "status": r2.status_code, "issue": data,
                    "url": data.get("html_url", ""), "number": data.get("number"),
                    "labels_omitted": True}
        text = (r2.text or "")[:400]
    return {"ok": False, "status": r.status_code, "error": text, "labels_omitted": False}


def create(project: dict, note: str = "", kind: str | None = None) -> dict:
    """프로젝트 → 이슈(또는 1클릭 URL). 토큰 원문은 절대 응답에 넣지 않는다.

    kind: image_request(기본) | image_revision | video_request
    note: 수정 지시·요청 메모(그대로 이슈 본문에 들어간다)
    """
    k = kind_of(kind)
    title = build_title(project, k)
    body = build_body(project, note, kind=k)
    labels = [label(k)]
    inbox = target_inbox(project["id"], k)
    try:                                     # 요청서 txt 도 최신 내용으로 맞춰 둔다
        request_text = planner.write_image_request(project).read_text(encoding="utf-8")
    except Exception:
        request_text = body
    if k != "image_request":
        request_text = body
    done = {
        "image_request": "케빈이 이슈를 보고 이미지를 올립니다.",
        "image_revision": "케빈이 이슈를 보고 고친 이미지를 같은 이름으로 다시 올립니다.",
        "video_request": "케빈이 Kling 크레딧으로 클립(.mp4)을 뽑아 올립니다(맥북이 자동으로 받아 다시 조립).",
    }[k]
    tok = token()
    if tok:
        res = _post_issue(title, body, tok, labels)
        if res.get("ok"):
            return {
                "ok": True, "mode": "api", "kind": k, "url": res["url"], "number": res.get("number"),
                "title": title, "body": body, "copy_text": request_text,
                "target_inbox": inbox, "labels": labels,
                "labels_omitted": bool(res.get("labels_omitted")),
                "message": f"깃허브 이슈를 만들었습니다 (#{res.get('number')}). {done}",
            }
        # 토큰이 잘못됐거나 권한이 없으면 URL 방식으로 자동 전환
        reason = res.get("error", "")
        return {
            "ok": False, "mode": "api_failed", "kind": k, "title": title, "body": body,
            "copy_text": request_text, "target_inbox": inbox, "labels": labels,
            "url": issue_url(project, title, note, k),
            "error": reason,
            "message": ("토큰으로 이슈를 만들지 못했습니다(권한/토큰 확인 필요). "
                        "대신 1클릭 제출 화면을 열었습니다."),
        }
    return {
        "ok": True, "mode": "url", "kind": k, "title": title, "body": body, "copy_text": request_text,
        "target_inbox": inbox, "labels": labels, "url": issue_url(project, title, note, k),
        "message": ("토큰이 없어 1클릭 제출 화면을 열었습니다. "
                    "깃허브에서 [Submit new issue] 한 번만 누르면 케빈에게 전달됩니다."),
    }


def status() -> dict:
    tok = token()
    cfg = settings.load_settings().get("github") or {}
    return {
        "repo": repo(),
        "repo_url": f"{WEB_ROOT}/{repo()}",
        "token": bool(tok),
        "token_tail": tok[-4:] if tok else "",
        "issue_label": label("image_request"),
        "revision_label": label("image_revision"),
        "video_label": label("video_request"),
        "title_prefix": title_prefix("image_request"),
        "revision_title_prefix": title_prefix("image_revision"),
        "video_title_prefix": title_prefix("video_request"),
        "issues_url": f"{WEB_ROOT}/{repo()}/issues",
        "new_issue_url": f"{WEB_ROOT}/{repo()}/issues/new",
        "notes": cfg.get("notes", ""),
    }

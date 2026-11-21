"""GitHub Issue 로 케빈에게 키프레임 이미지를 요청한다.

두 가지 길이 있다.
- 토큰(config/settings.json 의 keys.github_token)이 있으면
  POST /repos/{repo}/issues 로 이슈를 바로 만들고 이슈 URL 을 돌려준다.
- 토큰이 없으면 /issues/new?title=…&body=… URL 을 만들어 새 탭(사파리)으로 연다.
  사용자는 [Submit new issue] 한 번만 누르면 된다.

이슈 본문에는 항상 `target_inbox: assets_inbox/{ep_id}` 를 넣어
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
# 실제 확인한 한계: URL 6,313자 OK · 6,513자 실패 → 안전선은 6,000자.
MAX_URL_CHARS = 6000


def repo() -> str:
    cfg = settings.load_settings().get("github") or {}
    return (cfg.get("repo") or "profusive68-ship-it/tastypop-story-studio").strip().strip("/")


def label() -> str:
    cfg = settings.load_settings().get("github") or {}
    return (cfg.get("issue_label") or "image-request").strip()


def title_prefix() -> str:
    cfg = settings.load_settings().get("github") or {}
    return (cfg.get("issue_title_prefix") or "[이미지요청]").strip()


def token() -> str:
    return settings.github_token()


def target_inbox(ep_id: str) -> str:
    """케빈이 이미지를 올릴 정확한 폴더."""
    return f"assets_inbox/{ep_id}"


def build_title(project: dict) -> str:
    return f"{title_prefix()} {project['id']} - {project.get('topic', '')}".strip()


def _cut_brief(project: dict, prompt: str, limit: int = 240) -> str:
    c_note = prompt.strip()
    if len(c_note) > limit:
        c_note = c_note[:limit].rstrip() + "…"
    return c_note


def build_body(project: dict, note: str = "", compact: bool = False) -> str:
    """이슈 본문. compact=True 면 URL 길이 제한에 맞춘 요약본."""
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
    lines += [
        "---",
        f"_자동 생성: {datetime.now():%Y-%m-%d %H:%M} · TastyPop Studio v{settings.VERSION}_",
    ]
    return "\n".join(lines)


def build_url_body(project: dict, note: str = "", cut_lines: int | None = None) -> str:
    """주소(URL)에 담는 짧은 본문. 컷마다 장면 한 줄은 남긴다."""
    pid = project["id"]
    inbox = target_inbox(pid)
    n = len(project["cuts"])
    lines = [
        "## 📤 키프레임 이미지 요청",
        f"target_inbox: {inbox}",
        f"- 올릴 곳: `{inbox}/` 에 `kf1.png` ~ `kf{n}.png` (숫자=컷 번호)",
        f"- {n}컷 / {project.get('total_seconds')}초 / 9:16 · 글자·로고·워터마크 없이",
        f"- 프로젝트: `{pid}`",
        f"- 주제: {project.get('topic')}",
    ]
    if note.strip():
        lines.append(f"- 메모: {note.strip()[:200]}")
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


def _make_url(title: str, body: str) -> str:
    base = f"{WEB_ROOT}/{repo()}/issues/new"
    return (f"{base}?title={quote(title, safe='')}&body={quote(body, safe='')}"
            f"&labels={quote(label(), safe='')}")


def issue_url(project: dict, title: str, note: str = "") -> str:
    """토큰 없이 쓰는 '1클릭 Submit' URL.

    GitHub 은 URL 이 약 6,400자를 넘으면 500 을 낸다. 넘치면 마지막 컷 줄부터 덜어 낸다.
    """
    keep = len(project["cuts"])
    url = _make_url(title, build_url_body(project, note))
    while len(url) > MAX_URL_CHARS and keep > 0:
        keep = max(0, keep - 2)
        url = _make_url(title, build_url_body(project, note, cut_lines=keep))
    return url


def new_issue_url(title: str, body: str, labels: list[str] | None = None) -> str:
    """이미 만들어진 본문을 담는 URL — 길면 잘라 낸다(프로젝트를 모를 때용)."""
    url = _make_url(title, body)
    if len(url) <= MAX_URL_CHARS:
        return url
    for limit in (2400, 1600, 1000, 500):
        short = body[:limit].rstrip() + "\n\n… (본문이 길어 줄였습니다. 앱의 [📋 케빈에게 이미지 요청하기] > [요청서 복사]를 쓰세요.)"
        url = _make_url(title, short)
        if len(url) <= MAX_URL_CHARS:
            return url
    return _make_url(title, "요청서가 길어 본문을 담지 못했습니다. 앱의 [요청서 복사]를 쓰세요.")


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


def create(project: dict, note: str = "") -> dict:
    """프로젝트 → 이슈(또는 1클릭 URL). 토큰 원문은 절대 응답에 넣지 않는다."""
    title = build_title(project)
    body = build_body(project, note)
    labels = [label()]
    inbox = target_inbox(project["id"])
    try:                                     # 요청서 txt 도 최신 내용으로 맞춰 둔다
        request_text = planner.write_image_request(project).read_text(encoding="utf-8")
    except Exception:
        request_text = body
    tok = token()
    if tok:
        res = _post_issue(title, body, tok, labels)
        if res.get("ok"):
            return {
                "ok": True, "mode": "api", "url": res["url"], "number": res.get("number"),
                "title": title, "body": body, "copy_text": request_text,
                "target_inbox": inbox, "labels": labels,
                "labels_omitted": bool(res.get("labels_omitted")),
                "message": f"깃허브 이슈를 만들었습니다 (#{res.get('number')}). 케빈이 이슈를 보고 이미지를 올립니다.",
            }
        # 토큰이 잘못됐거나 권한이 없으면 URL 방식으로 자동 전환
        reason = res.get("error", "")
        return {
            "ok": False, "mode": "api_failed", "title": title, "body": body, "copy_text": request_text,
            "target_inbox": inbox, "labels": labels,
            "url": issue_url(project, title, note),
            "error": reason,
            "message": ("토큰으로 이슈를 만들지 못했습니다(권한/토큰 확인 필요). "
                        "대신 1클릭 제출 화면을 열었습니다."),
        }
    return {
        "ok": True, "mode": "url", "title": title, "body": body, "copy_text": request_text,
        "target_inbox": inbox, "labels": labels, "url": issue_url(project, title, note),
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
        "issue_label": label(),
        "title_prefix": title_prefix(),
        "issues_url": f"{WEB_ROOT}/{repo()}/issues",
        "new_issue_url": f"{WEB_ROOT}/{repo()}/issues/new",
        "notes": cfg.get("notes", ""),
    }

"""v1.2.0 신규 기능 검증 — 컷별 이미지 미리보기·수정요청·영상제작요청·클립 수신 후 재조립.

네트워크를 쓰지 않는다(깃허브는 requests.post / 1클릭 URL 문자열만 확인).
ffmpeg 실제 렌더도 하지 않는다 — 재조립 "큐잉"까지만 검증한다.

- GET  /api/projects/{pid}/kf/{cut_id}   : 실제 kf 이미지(캐시방지 헤더)
- GET  /api/projects/{pid}/kf           : 갤러리 목록(mtime·url)
- POST /api/projects/{pid}/github-issue : kind=image_revision / video_request
- inbox_watch : 정지 이미지로 final.mp4 가 이미 있어도 clips/ 가 나중에 도착하면 재조립 큐잉
- 화면(index.html / app.js)에 갤러리·버튼·안내 문구 연결

실행:
    cd TastyPop_Studio
    python3 tests/test_v12_features.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app import github_issue, inbox_watch, jobs, planner, settings  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
TESTS: list[tuple[str, callable]] = []


def check(name: str):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def _run(name: str, fn) -> bool:
    try:
        detail = fn() or ""
        CHECKS.append((name, True, str(detail)))
        print(f"✅ {name} — {detail}")
        return True
    except Exception as e:
        import traceback

        CHECKS.append((name, False, f"{e}\n{traceback.format_exc()[-700:]}"))
        print(f"❌ {name}: {e}")
        return False


def _project(topic: str, ep: int) -> dict:
    return planner.create_project(topic=topic, content_type="asmr3d", mode="semi",
                                  target_seconds=13.5, ep=ep)


def _png(path: Path, seed: int = 1) -> Path:
    """실제 키프레임 비슷한 크기(>500바이트)의 PNG."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", (180, 320), os.urandom(180 * 320 * 3)).save(path)
    return path


def _mp4(path: Path, size: int = 4000) -> Path:
    """받은 클립처럼 보이는 더미 파일(내용 검사가 아니라 크기로만 통과)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(size))
    return path


def _fill_kf(pid: str, folder: Path | None = None) -> list[Path]:
    p = planner.load_project(pid)
    made = []
    for c in p["cuts"]:
        target = (folder / f"kf{c['id']}.png") if folder else (planner.project_dir(pid) / "kf" / f"c{c['id']:02d}.png")
        made.append(_png(target))
    return made


def _cleanup(pid: str, *tmps: Path) -> None:
    shutil.rmtree(planner.project_dir(pid), ignore_errors=True)
    for t in tmps:
        shutil.rmtree(t, ignore_errors=True)


def _clear_state(folder_name: str) -> None:
    st = inbox_watch.load_state()
    st["folders"].pop(folder_name, None)
    inbox_watch.save_state(st)


def _fake_render(stamp: bytes = b"image-version"):
    """실제 렌더 대신 final.mp4 를 쓰고 미디어 지문을 남긴다(실제 파이프라인의 마지막 동작과 같음)."""
    def run(pid: str):
        jobs._jobs.setdefault(pid, {})["status"] = "done"
        p = planner.load_project(pid)
        final = planner.final_video(pid)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(stamp + bytes(4096))
        p.setdefault("render", {})
        p["render"]["media_signature"] = planner.media_signature(p)
        p["render"]["clips"] = planner.media_status(p).get("clips", 0)
        p["status"] = "rendered"
        planner.save_project(p)
        return {"job": pid, "status": "queued"}
    return run


# ------------------------------------------------------------------ 미리보기 엔드포인트
@check("GET /kf/{cut_id} — 실제 kf 이미지 + 캐시방지 헤더")
def kf_endpoint():
    from fastapi.testclient import TestClient

    from app.main import app

    settings.save_settings({"assets": {"auto_receive": False}})
    p = _project("미리보기 엔드포인트", 91)
    settings.save_settings({"ui": {"active_project": ""}})
    try:
        _fill_kf(p["id"])
        c = TestClient(app)
        r = c.get(f"/api/projects/{p['id']}/kf/1")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("image/"), r.headers
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n", "PNG 가 아님"
        assert "no-cache" in r.headers.get("cache-control", ""), r.headers.get("cache-control")
        assert r.headers.get("etag"), "ETag 없음"
        # ?v=mtime 쿼리(캐시 방지)도 같은 파일
        r2 = c.get(f"/api/projects/{p['id']}/kf/1", params={"v": 12345})
        assert r2.status_code == 200 and r2.content == r.content

        # kf1.png 처럼 kfN 이름이어도 찾는다
        other = planner.project_dir(p["id"]) / "kf" / "kf2.png"
        (planner.project_dir(p["id"]) / "kf" / "c02.png").rename(other)
        assert c.get(f"/api/projects/{p['id']}/kf/2").status_code == 200

        # 없는 컷 / 아직 안 받은 컷
        assert c.get(f"/api/projects/{p['id']}/kf/999").status_code == 404
        (planner.project_dir(p["id"]) / "kf" / "c03.png").unlink()
        assert c.get(f"/api/projects/{p['id']}/kf/3").status_code == 404

        # 갤러리 목록: mtime·url(?v=) 포함
        g = c.get(f"/api/projects/{p['id']}/kf").json()
        row = next(x for x in g["cuts"] if x["id"] == 1)
        assert row["found"] and row["kind"] == "image" and row["mtime"] > 0, row
        assert f"/api/projects/{p['id']}/kf/1?v={row['mtime']}" == row["url"], row["url"]
        missing = [x["id"] for x in g["cuts"] if not x["found"]]
        assert missing == [3], missing
        return f"200 PNG · ETag · ?v= · 갤러리 {g['found']}/{g['total']}컷"
    finally:
        _cleanup(p["id"])


# ------------------------------------------------------------------ 이슈 종류
@check("kind=image_revision — [이미지수정] 제목·라벨·target_inbox·지시 본문")
def revision_issue_url():
    p = _project("수정요청 테스트", 92)
    old = settings.load_settings()["keys"].get("github_token", "")
    try:
        settings.save_settings({"keys": {"github_token": ""}})
        inst = "3번 컷 감자 표정 더 놀라게, 9번 태극기 더 크게"
        res = github_issue.create(p, note=inst, kind="image_revision")
        assert res["mode"] == "url" and res["ok"], res
        assert res["kind"] == "image_revision", res.get("kind")
        assert res["target_inbox"] == f"assets_inbox/{p['id']}", res["target_inbox"]
        assert res["labels"] == ["image-revision"], res["labels"]
        title = unquote(res["url"].split("title=")[1].split("&body=")[0])
        body = unquote(res["url"].split("body=")[1].split("&labels=")[0])
        assert title.startswith("[이미지수정] ") and p["id"] in title, title
        assert f"target_inbox: assets_inbox/{p['id']}" in body, body[:200]
        assert inst in body, "수정 지시가 본문에 없음"
        assert "labels=image-revision" in res["url"], res["url"][-120:]
        assert len(res["url"]) <= github_issue.MAX_URL_CHARS, len(res["url"])
        return f"{title[:44]} · URL {len(res['url'])}자"
    finally:
        settings.save_settings({"keys": {"github_token": old}})
        _cleanup(p["id"])


@check("kind=video_request — [영상제작] 제목·라벨·clips 폴더·컷 수·모션")
def video_issue_url():
    p = _project("영상제작 요청", 93)
    old = settings.load_settings()["keys"].get("github_token", "")
    try:
        settings.save_settings({"keys": {"github_token": ""}})
        res = github_issue.create(p, note="7번 컷은 더 천천히", kind="video_request")
        assert res["mode"] == "url" and res["ok"], res
        assert res["target_inbox"] == f"assets_inbox/{p['id']}/clips", res["target_inbox"]
        assert res["labels"] == ["video-request"], res["labels"]
        title = unquote(res["url"].split("title=")[1].split("&body=")[0])
        body = unquote(res["url"].split("body=")[1].split("&labels=")[0])
        assert title.startswith("[영상제작] ") and p["id"] in title, title
        assert f"assets_inbox/{p['id']}/clips" in body, body[:300]
        assert f"**{len(p['cuts'])}컷**" in body, body[:400]
        assert "c01.mp4" in body and "### 컷별 움직임" in body, "컷별 파일명/움직임 없음"
        assert "움직임" in body and "7번 컷은 더 천천히" in body, "모션/메모 없음"
        assert "final.mp4" in body and "다시 조립" in body, "재조립 안내 없음"
        assert len(res["url"]) <= github_issue.MAX_URL_CHARS, len(res["url"])
        return f"{title[:40]} · target {res['target_inbox']} · URL {len(res['url'])}자"
    finally:
        settings.save_settings({"keys": {"github_token": old}})
        _cleanup(p["id"])


@check("토큰 있음 — 종류별 라벨/제목으로 이슈 API 생성(모의)")
def issue_api_kinds():
    import requests

    p = _project("API 종류 테스트", 94)
    old = settings.load_settings()["keys"].get("github_token", "")
    real_post = requests.post
    seen: list[dict] = []

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        seen.append(json.loads(data) if data else {})

        class R:
            status_code = 201
            text = ""

            @staticmethod
            def json():
                return {"number": 11, "html_url": "https://github.com/x/y/issues/11"}
        return R()

    try:
        settings.save_settings({"keys": {"github_token": "ghp_dummy1234"}})
        requests.post = fake_post
        r1 = github_issue.create(p, note="2번 컷 더 밝게", kind="image_revision")
        r2 = github_issue.create(p, kind="video_request")
        assert r1["mode"] == "api" and r2["mode"] == "api", (r1.get("mode"), r2.get("mode"))
        assert seen[0]["labels"] == ["image-revision"], seen[0].get("labels")
        assert seen[1]["labels"] == ["video-request"], seen[1].get("labels")
        assert seen[0]["title"].startswith("[이미지수정]"), seen[0]["title"]
        assert seen[1]["title"].startswith("[영상제작]"), seen[1]["title"]
        assert "2번 컷 더 밝게" in seen[0]["body"]
        assert f"assets_inbox/{p['id']}/clips" in seen[1]["body"]
        return "라벨 image-revision / video-request · 제목 접두어 통과"
    finally:
        requests.post = real_post
        settings.save_settings({"keys": {"github_token": old}})
        _cleanup(p["id"])


@check("github-issue 엔드포인트 — kind 분기 + 수정 지시 필수")
def endpoint_kinds():
    from fastapi.testclient import TestClient

    from app.main import app

    settings.save_settings({"assets": {"auto_receive": False}})
    p = _project("엔드포인트 종류", 95)
    old = settings.load_settings()["keys"].get("github_token", "")
    try:
        settings.save_settings({"keys": {"github_token": ""}, "ui": {"active_project": ""}})
        c = TestClient(app)
        bad = c.post(f"/api/projects/{p['id']}/github-issue", json={"kind": "image_revision", "instruction": "  "})
        assert bad.status_code == 400, bad.text
        r = c.post(f"/api/projects/{p['id']}/github-issue",
                   json={"kind": "image_revision", "instruction": "5번 컷 표정 더 놀라게"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["kind"] == "image_revision" and d["labels"] == ["image-revision"], d
        assert d["target_inbox"] == f"assets_inbox/{p['id']}", d["target_inbox"]
        v = c.post(f"/api/projects/{p['id']}/github-issue", json={"kind": "video_request"}).json()
        assert v["kind"] == "video_request" and v["labels"] == ["video-request"], v
        assert v["target_inbox"].endswith("/clips"), v["target_inbox"]
        # 기본값은 기존 이미지 요청 그대로
        i = c.post(f"/api/projects/{p['id']}/github-issue", json={}).json()
        assert i["kind"] == "image_request" and i["labels"] == ["image-request"], i
        st = c.get("/api/github/status").json()
        assert st["revision_label"] == "image-revision" and st["video_label"] == "video-request", st
        return "400(지시 없음) · revision/video/default 모두 통과"
    finally:
        settings.save_settings({"keys": {"github_token": old}, "ui": {"active_project": ""}})
        _cleanup(p["id"])


# ------------------------------------------------------------------ 클립 도착 → 재조립
@check("클립(.mp4) 도착 → 영상이 이미 있어도 재조립 큐잉 (final.mp4 덮어쓰기)")
def clip_arrival_rebuild():
    p = _project("클립 재조립 테스트", 96)
    pid = p["id"]
    n = len(p["cuts"])
    tmp = Path(tempfile.mkdtemp(prefix="tp_clip_"))
    folder = tmp / "assets_inbox" / pid
    _clear_state(pid)
    real_render, real_busy, real_log = jobs.run_render, jobs.is_busy, jobs.log_line
    rendered: list[str] = []
    logged: list[str] = []
    try:
        jobs.is_busy = lambda x: False
        jobs.log_line = lambda x, m: (logged.append(m), real_log(x, m))
        jobs.run_render = _fake_render(b"image-version")
        # 1) 정지 이미지만 도착 → 첫 조립
        _fill_kf(pid, folder)
        r1 = inbox_watch.receive_once(root=tmp, do_pull=False, do_render=True)
        assert len(r1["received"]) == 1 and r1["rendered"] == [pid], r1
        final = planner.final_video(pid)
        assert final.exists(), "첫 조립(final.mp4)이 만들어지지 않음"
        first_bytes = final.read_bytes()
        assert planner.media_status(planner.load_project(pid))["clips"] == 0
        assert planner.render_needs_update(planner.load_project(pid)) is False

        # 2) 같은 묶음 재확인 → 중복 조립 없음
        r2 = inbox_watch.receive_once(root=tmp, do_pull=False, do_render=True)
        assert r2["received"] == [] and r2["rendered"] == [], r2

        # 3) 클립이 나중에 도착 → 반드시 재조립
        clips_n = 3
        for i in range(1, clips_n + 1):
            _mp4(folder / "clips" / f"c{i:02d}.mp4")
        jobs.run_render = _fake_render(b"clip-version")
        proj = planner.load_project(pid)
        assert planner.media_status(proj)["clips"] == 0, "복사 전인데 클립이 보임"
        r3 = inbox_watch.receive_once(root=tmp, do_pull=False, do_render=True)
        assert len(r3["received"]) == 1, r3
        got = r3["received"][0]
        assert got["project_id"] == pid and len(got["added_clips"]) == clips_n, got
        assert r3["rendered"] == [pid], f"클립 도착 후 재조립 트리거 안 됨: {r3}"
        assert final.read_bytes() != first_bytes, "final.mp4 가 덮어쓰이지 않음"
        st = planner.media_status(planner.load_project(pid))
        assert st["clips"] == clips_n and st["ready"], st
        assert any("다시 조립" in m or "재조립" in m for m in logged), logged[-6:]
        return f"이미지 {n}컷 → 조립 1회 → 클립 {clips_n}개 도착 → 재조립 1회(덮어쓰기 확인)"
    finally:
        jobs.run_render, jobs.is_busy, jobs.log_line = real_render, real_busy, real_log
        _cleanup(pid, tmp)


@check("클립이 이미지만 대체해도 미디어 지문이 바뀌어 재조립 필요 판정")
def signature_changes():
    p = _project("지문 테스트", 97)
    pid = p["id"]
    try:
        _fill_kf(pid)
        proj = planner.load_project(pid)
        sig_img = planner.media_signature(proj)
        final = planner.final_video(pid)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"x" * 2048)
        proj.setdefault("render", {})["media_signature"] = sig_img
        planner.save_project(proj)
        assert planner.render_needs_update(planner.load_project(pid)) is False
        # 한 컷을 클립으로 교체
        _mp4(planner.project_dir(pid) / "clips" / "c03.mp4")
        proj = planner.load_project(pid)
        sig_clip = planner.media_signature(proj)
        assert sig_clip != sig_img, "클립으로 바뀌었는데 지문이 같음"
        assert planner.render_needs_update(proj) is True, "지문이 달라졌는데 재조립 판정 안 됨"
        # 지문이 없는 예전 프로젝트: 영상보다 새 파일이면 재조립
        p2 = planner.load_project(pid)
        p2["render"] = {}
        planner.save_project(p2)
        import os
        os.utime(planner.project_dir(pid) / "clips" / "c03.mp4", None)
        assert planner.render_needs_update(planner.load_project(pid)) is True
        return "이미지→클립 교체 시 지문 변경 · 재조립 판정 통과"
    finally:
        _cleanup(pid)


@check("완성된 프로젝트에도 새 클립 묶음을 매칭(clips 포함 폴더)")
def match_finished_with_clips():
    p = _project("완성본 클립 매칭", 98)
    pid = p["id"]
    tmp = Path(tempfile.mkdtemp(prefix="tp_match_"))
    try:
        _fill_kf(pid)
        proj = planner.load_project(pid)
        final = planner.final_video(pid)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"x" * 2048)
        proj.setdefault("render", {})["media_signature"] = planner.media_signature(proj)
        planner.save_project(proj)
        assert inbox_watch._needs_images(pid) is False, "이미 완성인데 대기로 보임"
        # 폴더명이 pid 와 다르고 Ep 번호만 같은 경우(클립이 들어 있음)
        folder = tmp / "assets_inbox" / f"Ep.{p['ep']}_클립"
        for i in range(1, len(p["cuts"]) + 1):
            _mp4(folder / "clips" / f"kf{i}.mp4")
        _clear_state(folder.name)
        projects = planner.list_projects()
        pid_m, why = inbox_watch.match_project(folder.name, projects, has_clips=True)
        assert pid_m == pid, (pid_m, why)
        pid_no, _ = inbox_watch.match_project(folder.name, projects, has_clips=False)
        assert pid_no in (None, pid), pid_no
        assert inbox_watch._wants_assets(pid, incoming_clips=True) is True
        return f"has_clips=True → {why} · 완성본도 클립 수신 대상"
    finally:
        _cleanup(pid, tmp)


@check("화면 연결 — 갤러리·수정요청·영상제작 버튼과 안내 문구")
def ui_wiring():
    from fastapi.testclient import TestClient

    from app.main import app

    settings.save_settings({"assets": {"auto_receive": False}})
    try:
        c = TestClient(app)
        home = c.get("/").text
        for probe in ("pdKfGallery", "✏️ 케빈에게 이미지 수정 요청", "🎬 케빈에게 영상(클립) 제작 요청",
                      "이미지를 다 확인하셨으면 눌러주세요", "Kling 구독 크레딧", "5분 내 자동 감지"):
            assert probe in home, f"화면에 '{probe}' 없음"
        js = c.get("/static/app.js").text
        for probe in ("renderKfGallery", "image_revision", "video_request", "pickCut",
                      "/kf/", "checkMedia"):
            assert probe in js, f"app.js 에 '{probe}' 없음"
        css = c.get("/static/style.css").text
        assert ".kfgallery" in css and ".kfcard" in css, "갤러리 스타일 없음"
        return "index.html · app.js · style.css 연결 확인"
    finally:
        pass


def run_all() -> str:
    before = len(CHECKS)
    for name, fn in TESTS:
        _run(name, fn)
    mine = CHECKS[before:]
    bad = [n for n, ok, _ in mine if not ok]
    assert not bad, f"실패: {bad}"
    return f"{len(mine)}개 항목 통과"


def main() -> int:
    print("=== v1.2.0 이미지 미리보기·수정요청·영상제작요청·클립 재조립 검증 ===")
    for name, fn in TESTS:
        _run(name, fn)
    bad = [c for c in CHECKS if not c[1]]
    print("\n=== 결과:", "전부 통과 ✅" if not bad else f"{len(bad)}개 실패 ❌", "===")
    for n, ok, d in CHECKS:
        print(("✅" if ok else "❌"), n, "—", (d.splitlines() or [""])[0])
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""깃허브 이미지 요청(이슈) + assets_inbox 자동 수신 검증.

네트워크를 쓰지 않는다.
- 이슈 본문/URL(토큰 없음·있음·라벨 422 재시도)은 requests.post 를 가짜로 바꿔 검증
- 폴더→프로젝트 매칭, kf/ 복사, 자동 조립 트리거, 중복 방지는 임시 폴더로 검증
- git pull --ff-only 는 임시 저장소(로컬 bare remote)로 검증 — 충돌해도 예외 없이 실패해야 한다

실행:
    cd TastyPop_Studio
    .venv/bin/python tests/test_github_inbox.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app import github_issue, inbox_watch, jobs, planner, settings  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []
TESTS: list[tuple[str, callable]] = []


def check(name: str):
    """검사 함수를 목록에 등록만 한다(import 시 실행 금지)."""
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


def _png(path: Path, color: tuple[int, int, int]) -> Path:
    """실제 키프레임 비슷한 크기(>500바이트)의 PNG."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.frombytes("RGB", (180, 320), os.urandom(180 * 320 * 3))
    im.save(path)
    return path


# ------------------------------------------------------------------ 이슈
@check("이슈 본문에 target_inbox 명시")
def issue_body():
    p = _project("이슈 본문 테스트", 71)
    try:
        body = github_issue.build_body(p, note="치즈가 살아 움직이는 톤으로")
        title = github_issue.build_title(p)
        assert f"target_inbox: assets_inbox/{p['id']}" in body, body[:300]
        assert title.startswith("[이미지요청] ") and p["id"] in title and p["topic"] in title, title
        req = planner.write_image_request(p).read_text(encoding="utf-8")
        assert f"target_inbox: assets_inbox/{p['id']}" in req, "이미지_요청서.txt 에 target_inbox 없음"
        assert len(body) > 800
        return f"{len(body)}자 · {title[:40]}"
    finally:
        jobs.log_line(p["id"], "test")
        import shutil

        shutil.rmtree(planner.project_dir(p["id"]), ignore_errors=True)


@check("토큰 없음 → 1클릭 제출 URL")
def issue_url_mode():
    p = _project("URL 모드 테스트", 72)
    old = settings.load_settings()["keys"].get("github_token", "")
    try:
        settings.save_settings({"keys": {"github_token": ""}})
        res = github_issue.create(p)
        assert res["mode"] == "url" and res["ok"], res
        url = res["url"]
        assert url.startswith("https://github.com/profusive68-ship-it/tastypop-story-studio/issues/new?"), url[:120]
        assert "title=" in url and "body=" in url and "labels=image-request" in url
        assert len(url) <= github_issue.MAX_URL_CHARS, len(url)
        assert res["copy_text"].startswith("# "), "복사용 요청서가 비었습니다"
        return f"URL {len(url)}자"
    finally:
        settings.save_settings({"keys": {"github_token": old}})
        import shutil

        shutil.rmtree(planner.project_dir(p["id"]), ignore_errors=True)


@check("토큰 있음 → 이슈 API 생성(모의)")
def issue_api_mode():
    import requests

    p = _project("API 모드 테스트", 73)
    old = settings.load_settings()["keys"].get("github_token", "")
    calls: list[dict] = []
    real_post = requests.post

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        payload = json.loads(data) if data else {}
        calls.append({"url": url, "payload": payload, "headers": headers or {}})

        class R:
            status_code = 201
            text = ""

            @staticmethod
            def json():
                return {"number": 42, "html_url": "https://github.com/profusive68-ship-it/tastypop-story-studio/issues/42"}
        return R()

    try:
        settings.save_settings({"keys": {"github_token": "ghp_testtoken1234"}})
        requests.post = fake_post
        res = github_issue.create(p)
        assert res["mode"] == "api" and res["ok"], res
        assert res["url"].endswith("/issues/42"), res["url"]
        assert res["number"] == 42
        c = calls[0]
        assert c["url"] == "https://api.github.com/repos/profusive68-ship-it/tastypop-story-studio/issues", c["url"]
        assert c["payload"]["labels"] == ["image-request"], c["payload"].get("labels")
        assert c["headers"]["Authorization"] == "Bearer ghp_testtoken1234"
        assert "ghp_testtoken1234" not in json.dumps({k: v for k, v in res.items() if k != "body"}), "응답에 토큰 노출"
        return "POST /repos/…/issues · 라벨 image-request"
    finally:
        requests.post = real_post
        settings.save_settings({"keys": {"github_token": old}})
        import shutil

        shutil.rmtree(planner.project_dir(p["id"]), ignore_errors=True)


@check("라벨 없음(422) → 라벨 빼고 재시도")
def issue_label_retry():
    import requests

    p = _project("라벨 재시도", 74)
    old = settings.load_settings()["keys"].get("github_token", "")
    real_post = requests.post
    seen: list[dict] = []

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        payload = json.loads(data) if data else {}
        seen.append(payload)
        if "labels" in payload:
            class R422:
                status_code = 422
                text = '{"message":"Validation Failed","errors":[{"code":"invalid"}]}'

                @staticmethod
                def json():
                    return {}
            return R422()

        class R201:
            status_code = 201
            text = ""

            @staticmethod
            def json():
                return {"number": 7, "html_url": "https://github.com/x/y/issues/7"}
        return R201()

    try:
        settings.save_settings({"keys": {"github_token": "ghp_x"}})
        requests.post = fake_post
        res = github_issue.create(p)
        assert res["ok"] and res["labels_omitted"], res
        assert len(seen) == 2 and "labels" not in seen[1]
        return "422 후 라벨 없이 성공"
    finally:
        requests.post = real_post
        settings.save_settings({"keys": {"github_token": old}})
        import shutil

        shutil.rmtree(planner.project_dir(p["id"]), ignore_errors=True)


# ------------------------------------------------------------------ 매칭
@check("폴더명 ↔ 프로젝트 매칭 규칙")
def matching():
    a = _project("매칭 불닭", 81)
    b = _project("매칭 김치", 82)
    try:
        projects = planner.list_projects()
        pid, why = inbox_watch.match_project(a["id"], projects)
        assert pid == a["id"], (pid, why)
        pid2, _ = inbox_watch.match_project(a["id"].split("_2026")[0], projects)   # 부분 일치
        assert pid2 == a["id"], pid2
        pid3, why3 = inbox_watch.match_project("Ep.81 화요일", projects)
        assert pid3 == a["id"], (pid3, why3)
        pid4, _ = inbox_watch.match_project("Ep.82_김치찌개", projects)
        assert pid4 == b["id"], pid4
        pid5, why5 = inbox_watch.match_project("zzz_아무거나", projects)
        assert pid5 is None and "찾지 못" in why5, (pid5, why5)
        return "정확/부분/Ep번호/불일치 통과"
    finally:
        import shutil

        for p in (a, b):
            shutil.rmtree(planner.project_dir(p["id"]), ignore_errors=True)


# ------------------------------------------------------------------ 수신
@check("assets_inbox → kf/ 자동 복사 + 조립 트리거")
def auto_receive():
    p = _project("자동 수신 테스트", 83)
    n = len(p["cuts"])
    tmp = Path(tempfile.mkdtemp(prefix="tp_inbox_"))
    root = tmp
    folder = root / "assets_inbox" / p["id"]
    for i in range(1, n + 1):
        _png(folder / f"kf{i}.png", (40 * i % 255, 90, 160))
    rendered: list[str] = []
    real_render, real_busy = jobs.run_render, jobs.is_busy
    real_log_line = jobs.log_line
    logged: list[str] = []

    def fake_log(pid, msg):
        logged.append(msg)
        real_log_line(pid, msg)

    try:
        jobs.is_busy = lambda pid: False
        jobs.run_render = lambda pid: rendered.append(pid) or {"job": pid, "status": "queued"}
        jobs.log_line = fake_log

        res1 = inbox_watch.receive_once(root=root, do_pull=False, do_render=True)
        assert len(res1["received"]) == 1, res1
        rec = res1["received"][0]
        assert rec["project_id"] == p["id"] and len(rec["added"]) == n, rec
        assert rendered == [p["id"]], f"조립 트리거 안 됨: {rendered}"
        assert any("🎉 케빈이 올린 이미지 자동 수신 완료!" in m for m in logged), logged[:5]
        st = planner.media_status(planner.load_project(p["id"]))
        assert st["ready"], st
        assert (planner.project_dir(p["id"]) / "kf" / "c01.png").exists()

        # 같은 묶음을 다시 확인 → 중복 복사 없음
        res2 = inbox_watch.receive_once(root=root, do_pull=False, do_render=True)
        assert res2["received"] == [], res2

        # 내용이 바뀐 이미지를 다시 올리면 그 컷만 교체
        _png(folder / "kf3.png", (255, 0, 0))
        res3 = inbox_watch.receive_once(root=root, do_pull=False, do_render=False)
        assert res3["received"] and len(res3["received"][0]["replaced"]) == 1, res3
        assert "kf3.png→c03.png" in res3["received"][0]["replaced"], res3["received"][0]
        return f"{n}컷 복사 · 중복 0 · 교체 1 · 조립 트리거 1"
    finally:
        jobs.run_render, jobs.is_busy, jobs.log_line = real_render, real_busy, real_log_line
        import shutil

        shutil.rmtree(planner.project_dir(p["id"]), ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


@check("컷 수를 넘는 이미지는 무시")
def receive_extra_files():
    p = _project("컷 초과 테스트", 84)
    n = len(p["cuts"])
    tmp = Path(tempfile.mkdtemp(prefix="tp_inbox2_"))
    folder = tmp / "assets_inbox" / p["id"]
    for i in range(1, n + 4):
        _png(folder / f"kf{i}.png", (10, 10, 200))
    try:
        res = inbox_watch.receive_once(root=tmp, do_pull=False, do_render=False)
        rec = res["received"][0]
        assert len(rec["added"]) == n, rec
        copied = list((planner.project_dir(p["id"]) / "kf").glob("c*.png"))
        assert len(copied) == n, [x.name for x in copied]
        return f"{n}컷만 복사"
    finally:
        import shutil

        shutil.rmtree(planner.project_dir(p["id"]), ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ git
def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)


@check("git pull --ff-only 안전 동작(성공·충돌 모두 예외 없음)")
def pull_safety():
    tmp = Path(tempfile.mkdtemp(prefix="tp_git_"))
    remote = tmp / "remote.git"
    a, b = tmp / "A", tmp / "B"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(a)], check=True, capture_output=True)
    for repo in (a,):
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
    (a / "app").mkdir()
    (a / "file.txt").write_text("1\n", encoding="utf-8")
    _git(a, "add", "-A")
    _git(a, "commit", "-q", "-m", "init")
    _git(a, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(b)], check=True, capture_output=True)
    _git(b, "config", "user.email", "t@t")
    _git(b, "config", "user.name", "t")
    try:
        r1 = inbox_watch.git_pull(b)
        assert r1["ok"], r1

        (a / "file.txt").write_text("2\n", encoding="utf-8")
        _git(a, "commit", "-qam", "update")
        _git(a, "push", "-q", "origin", "HEAD:main")
        r2 = inbox_watch.git_pull(b)
        assert r2["ok"] and "file.txt" in r2["changed"], r2
        assert (b / "file.txt").read_text(encoding="utf-8") == "2\n"

        (a / "file.txt").write_text("3\n", encoding="utf-8")
        _git(a, "commit", "-qam", "update2")
        _git(a, "push", "-q", "origin", "HEAD:main")
        (b / "file.txt").write_text("로컬 수정\n", encoding="utf-8")     # 추적 파일 로컬 수정 → pull 거부
        r3 = inbox_watch.git_pull(b)
        assert r3["ok"] is False and r3["message"], r3
        r4 = inbox_watch.git_pull(tmp / "없는폴더")                          # .git 없음 → skip
        assert r4["skipped"], r4
        return f"성공/변경감지/충돌로그({r3['message'][:28]}…)/미연결 통과"
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ 엔드포인트
@check("FastAPI 엔드포인트(/api/github/status·github-issue·watch)")
def endpoints():
    from fastapi.testclient import TestClient

    from app.main import app

    old_assets = settings.load_settings()["assets"]
    settings.save_settings({"assets": {"auto_receive": False}})          # 시험 중 워커 정지
    p = _project("엔드포인트 테스트", 85)
    try:
        c = TestClient(app)
        g = c.get("/api/github/status").json()
        assert g["repo"] == "profusive68-ship-it/tastypop-story-studio" and g["issue_label"] == "image-request"
        assert "token" in g and "token_tail" in g

        r = c.post(f"/api/projects/{p['id']}/github-issue", json={})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["mode"] in ("url", "api", "api_failed"), d
        assert f"assets_inbox/{p['id']}" == d["target_inbox"], d["target_inbox"]
        assert d["url"].startswith("https://github.com/"), d["url"]

        w = c.get("/api/watch").json()
        assert set(["enabled", "has_git", "running", "interval", "log"]).issubset(w), w.keys()
        assert w["interval"] >= 5
        t = c.post("/api/watch", json={"on": True}).json()
        assert t["enabled"] is True
        t2 = c.post("/api/watch", json={"on": False}).json()
        assert t2["enabled"] is False
        a = c.post("/api/watch/active", json={"pid": p["id"]}).json()
        assert a["active_project"] == p["id"]

        home = c.get("/").text
        assert "케빈에게 이미지 요청하기" in home and "자동 이미지 수신" in home, "새 버튼이 화면에 없음"
        assert "5분 이내에 자동 감지" in home, "안내 문구 없음"
        js = c.get("/static/app.js").text
        assert "github-issue" in js and "toggleWatch" in js, "프론트 연결 없음"
        return "status/github-issue/watch/active + 화면 버튼·안내 문구 통과"
    finally:
        settings.save_settings({"assets": old_assets, "ui": {"active_project": ""}})
        import shutil

        shutil.rmtree(planner.project_dir(p["id"]), ignore_errors=True)


def run_all() -> str:
    """selftest.py 에서 호출하는 진입점. 실패가 있으면 AssertionError."""
    before = len(CHECKS)
    for name, fn in TESTS:
        _run(name, fn)
    mine = CHECKS[before:]
    bad = [n for n, ok, _ in mine if not ok]
    assert not bad, f"실패: {bad}"
    return f"{len(mine)}개 항목 통과"


def main() -> int:
    print("=== 깃허브 이미지 요청 · 자동 수신 검증 ===")
    for name, fn in TESTS:
        _run(name, fn)
    bad = [c for c in CHECKS if not c[1]]
    print("\n=== 결과:", "전부 통과 ✅" if not bad else f"{len(bad)}개 실패 ❌", "===")
    for n, ok, d in CHECKS:
        print(("✅" if ok else "❌"), n, "—", (d.splitlines() or [""])[0])
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())

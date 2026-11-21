"""TastyPop Studio 자체 검사.

1) 모든 파이썬 파일 구문 검사(py_compile)
2) FastAPI 엔드포인트 응답 검사
3) 더미 컷 3개로 실제 렌더 → 1080x1920/30fps · -12.5 LUFS · 무음 0 · 0.900초 훅 소멸 검증

실행:
    cd TastyPop_Studio
    .venv/bin/python tests/selftest.py           # 전체
    .venv/bin/python tests/selftest.py --fast    # 렌더는 짧게(3컷 5.4초)
"""
from __future__ import annotations

import argparse
import compileall
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> bool:
    try:
        detail = fn() or ""
        RESULTS.append((name, True, str(detail)))
        print(f"✅ {name} {detail}")
        return True
    except Exception as e:
        RESULTS.append((name, False, f"{e}\n{traceback.format_exc()[-800:]}"))
        print(f"❌ {name}: {e}")
        return False


def test_compile() -> str:
    ok = compileall.compile_dir(str(ROOT / "app"), quiet=1, force=True)
    compileall.compile_file(str(ROOT / "tests" / "selftest.py"), quiet=1, force=True)
    assert ok, "구문 오류가 있습니다."
    return "app/ 전체 통과"


def test_sfx() -> str:
    from app import sfx

    made = sfx.ensure_library()
    ok = [k for k, v in made.items() if v]
    assert len(ok) >= 15, f"사운드 생성 부족: {len(ok)}"
    p = made["crunch_bite"]
    assert p.exists() and p.stat().st_size > 2000
    return f"{len(ok)}종 생성"


def test_api() -> str:
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    h = c.get("/api/health").json()
    assert "version" in h and h["env"]["items"], "health 실패"
    cats = c.get("/api/topics/categories").json()
    assert len(cats["categories"]) >= 6
    rec = c.get("/api/topics/recommend?count=3").json()
    assert len(rec["items"]) == 3
    ai = c.get("/api/topics/recommend?ai=true&count=3").json()
    assert len(ai["items"]) >= 1
    proj = c.post("/api/projects", json={"topic": "테스트 떡볶이", "content_type": "asmr3d",
                                         "mode": "semi", "seconds": 13.5, "ep": 90}).json()["project"]
    pid = proj["id"]
    detail = c.get(f"/api/projects/{pid}").json()
    assert detail["cuts"] and detail["total_seconds"] > 0
    rv = c.post(f"/api/projects/{pid}/revise", json={"instruction": "3컷 더 빠르게, 치즈 소리 크게"}).json()
    assert rv["ok"] and rv["applied"], "수정 지시 실패"
    cut3 = [x for x in rv["project"]["cuts"] if x["id"] == 3][0]
    assert "cheese_stretch" in cut3["sfx"] and cut3.get("sfx_gain", 0) > 0
    img = c.get("/api/text_overlay?text=Would%20you%20eat%20this%3F&kind=hook")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    cp = c.get(f"/api/projects/{pid}/copy").json()
    assert cp["youtube"]["title"] and "Ep.90" in cp["youtube"]["title"]
    assert "[Ep.90]" in cp["instagram"]["caption"]
    s = c.post("/api/settings", json={"render": {"hook_size": 66}}).json()
    assert s["ok"]
    # 테스트 프로젝트 정리
    c.delete(f"/api/projects/{pid}?confirm=true")
    return "health/추천/생성/수정지시/오버레이/카피/설정 통과"


def _dummy_images(pid: str, n: int) -> None:
    from app import planner

    d = planner.project_dir(pid) / "kf"
    d.mkdir(parents=True, exist_ok=True)
    colors = [(210, 90, 40), (240, 180, 60), (60, 140, 90), (200, 60, 60), (90, 90, 200)]
    for i in range(n):
        im = Image.new("RGB", (864, 1536), colors[i % len(colors)])
        dr = ImageDraw.Draw(im)
        for y in range(0, 1536, 24):
            dr.line([(0, y), (864, y)], fill=tuple(min(255, c + 25) for c in colors[i % len(colors)]), width=2)
        dr.ellipse([264, 500, 600, 900], fill=(255, 235, 200))
        im.save(d / f"c{i+1:02d}.png")


def test_render(fast: bool = True) -> str:
    from app import jobs, planner, render

    project = planner.create_project(topic="렌더 자동검사", content_type="asmr3d", mode="semi",
                                     target_seconds=13.5, ep=99)
    pid = project["id"]
    cuts = [
        {"id": 1, "dur": 0.9, "scene": "hook dish close up", "sfx": ["flash_impact"], "enabled": True},
        {"id": 2, "dur": 2.0, "scene": "cheese pull", "sfx": ["cheese_stretch"], "enabled": True},
        {"id": 3, "dur": 2.5, "scene": "final wink", "sfx": ["heart_pop"], "enabled": True},
    ]
    project["cuts"] = cuts
    project["hook"] = {"text": "Would you eat this?", "start": 0.0, "end": 0.9}
    project["cta"] = {"like": "♥ Like · ✈ Share", "save": "Save for 2 AM", "seconds": 1.0}
    planner.save_project(project)
    _dummy_images(pid, len(cuts))
    _dummy_images(pid, len(cuts)) if False else None
    out = render.render(project)
    rep = out["report"]
    (ROOT / "tests" / "last_qa_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    assert rep["width"] == 1080 and rep["height"] == 1920, f"해상도 {rep['width']}x{rep['height']}"
    assert abs(rep["fps"] - 30) < 0.6, f"fps {rep['fps']}"
    assert rep["has_audio"] and rep["audio_codec"] == "aac"
    assert abs(float(rep["lufs"]) - (-12.5)) <= 1.0, f"LUFS {rep['lufs']}"
    assert rep["silence"]["ok"], f"무음 구간 {rep['silence']}"
    assert rep["hook"]["hook_visible_before"], f"훅 자막이 0.3초에 안 보임 {rep['hook']}"
    assert rep["hook"]["hook_gone_after"], f"훅 자막이 0.9초 뒤에도 남음 {rep['hook']}"
    jobs.log_line(pid, "자체검사 렌더 완료")
    return f"{rep['duration']}초 / {rep['lufs']} LUFS / 훅 소멸 {rep['hook']['before_std']}→{rep['hook']['after_std']}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", default=True)
    ap.add_argument("--skip-render", action="store_true")
    args = ap.parse_args()
    print("=== TastyPop Studio 자체 검사 ===")
    check("파이썬 구문 검사", test_compile)
    check("ASMR 사운드 라이브러리", test_sfx)
    check("FastAPI 엔드포인트", test_api)
    if not args.skip_render:
        check("렌더 파이프라인(더미 3컷)", lambda: test_render(args.fast))
    bad = [r for r in RESULTS if not r[1]]
    print("\n=== 결과:", "전부 통과 ✅" if not bad else f"{len(bad)}개 실패 ❌", "===")
    for n, ok, d in RESULTS:
        print(("✅" if ok else "❌"), n, "—", d.splitlines()[0] if d else "")
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""수익창출 보호용 원본 증빙 백업.

매 편마다 projects/{ep_id}/evidence/ 에
- 키프레임 원본 · Kling/영상 원본 클립 · 타임라인 JSON(project.json 스냅샷)
- CC0 사운드 출처 목록 · AI 고지문 · 파일 해시 목록(manifest.json)
을 자동 보관한다. 나중에 유튜브 수익 창출 심사·저작권 이의 제기 때 그대로 제출할 수 있다.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from . import planner, settings


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sfx_sources(project: dict) -> list[dict]:
    from . import sfx

    names = set()
    for c in project["cuts"]:
        names.update(c.get("sfx") or [])
    names.update({"sizzle_grill", "pot_boil", "flash_impact", "whoosh_cut", "heart_pop", "beep_scale"})
    if project["content_type"] == "drama4":
        names.add("music_bed_warm")
    out = []
    for n in sorted(names):
        meta = sfx.LIBRARY.get(n)
        p = settings.SFX_DIR / f"{n}.wav"
        out.append({
            "name": n,
            "desc": meta[0] if meta else "",
            "license": "CC0 (자체 합성 — 외부 음원 미사용)",
            "generator": "app/sfx.py (numpy additive synthesis)",
            "file": f"sfx_library/{n}.wav",
            "sha256": _sha256(p) if p.exists() else None,
        })
    return out


def build(project: dict, log=None) -> dict:
    pid = project["id"]
    d = planner.project_dir(pid)
    ev = d / "evidence"
    (ev / "keyframes").mkdir(parents=True, exist_ok=True)
    (ev / "clips").mkdir(parents=True, exist_ok=True)

    copied = []
    for p in sorted((d / "kf").iterdir()) if (d / "kf").exists() else []:
        if p.is_file():
            shutil.copy2(p, ev / "keyframes" / p.name)
            copied.append(f"keyframes/{p.name}")
    for p in sorted((d / "clips").iterdir()) if (d / "clips").exists() else []:
        if p.is_file():
            shutil.copy2(p, ev / "clips" / p.name)
            copied.append(f"clips/{p.name}")

    timeline = {
        "project_id": pid,
        "ep": project.get("ep"),
        "topic": project.get("topic"),
        "content_type": project.get("content_type"),
        "created_at": project.get("created_at"),
        "total_seconds": project.get("total_seconds"),
        "hook": project.get("hook"),
        "cta": project.get("cta"),
        "options": project.get("options"),
        "cuts": project.get("cuts"),
        "revisions": project.get("revisions", []),
        "render_report": project.get("render", {}),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }
    (ev / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    (ev / "sound_sources.json").write_text(
        json.dumps(_sfx_sources(project), ensure_ascii=False, indent=2), encoding="utf-8")
    (ev / "ai_disclosure.txt").write_text(
        "AI 제작 고지 / AI Disclosure\n"
        "이 영상은 AI 이미지 생성 + AI 영상화(Kling) + AI 음성(edge-tts) + 자체 합성 사운드로 제작되었습니다.\n"
        "This video was produced using AI image generation, AI video generation, AI voice and synthesized audio.\n",
        encoding="utf-8")
    (ev / "README.txt").write_text(
        "증빙 백업 안내\n"
        "- keyframes/ : 컷별 키프레임 원본\n"
        "- clips/     : 영상화 원본 클립(있다면)\n"
        "- timeline.json : 컷 길이·훅·CTA·수정 이력 전체\n"
        "- sound_sources.json : 사용한 모든 사운드의 출처·라이선스(CC0)\n"
        "- ai_disclosure.txt : AI 고지 문구\n"
        "- manifest.json : 파일별 SHA-256 (원본 무결성 증명)\n",
        encoding="utf-8")

    files = []
    for f in sorted(ev.rglob("*")):
        if f.is_file() and f.name != "manifest.json":
            files.append({"path": str(f.relative_to(ev)), "bytes": f.stat().st_size, "sha256": _sha256(f)})
    manifest = {
        "project_id": pid,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "version": settings.VERSION,
        "files": files,
        "note": "유튜브 수익 창출 심사·저작권 대응용 원본 백업",
    }
    (ev / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    status = {
        "ok": True,
        "path": "evidence",
        "files": len(files),
        "bytes": sum(f["bytes"] for f in files),
        "built_at": manifest["generated_at"],
    }
    project["evidence"] = status
    planner.save_project(project)
    if log:
        log(f"증빙 백업 완료: evidence/ ({len(files)}개 파일, {status['bytes']/1e6:.1f}MB)")
    return status


def status(project: dict) -> dict:
    d = planner.project_dir(project["id"]) / "evidence"
    manifest = d / "manifest.json"
    if not manifest.exists():
        return {"ok": False, "files": 0, "path": "evidence"}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return {"ok": True, "files": len(data.get("files", [])), "built_at": data.get("generated_at"),
                "path": "evidence", "items": [f["path"] for f in data.get("files", [])][:40]}
    except Exception:
        return {"ok": False, "files": 0, "path": "evidence"}

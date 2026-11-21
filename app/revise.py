"""'수정 지시' 한 줄로 시나리오·컷 타임라인 자동 재조정.

예: "3컷 더 빠르게, 치즈 늘어나는 소리 크게" / "전체 19초로 늘리고 마지막 컷 길게"
- AI 키가 있으면 LLM 이 컷별 장면·자막까지 고쳐 준다.
- 키가 없어도 규칙 기반으로 컷 길이·효과음 볼륨·훅 문구는 항상 조정된다.
"""
from __future__ import annotations

import json
import re

from . import llm, planner, settings

SFX_WORDS = {
    "치즈": "cheese_stretch", "늘어": "cheese_stretch", "늘어남": "cheese_stretch",
    "바삭": "crunch_big", "크런치": "crunch_bite", "깨물": "crunch_bite",
    "지글": "sizzle_grill", "불판": "sizzle_grill", "구이": "sizzle_grill",
    "튀김": "sizzle_fry", "튀기": "sizzle_fry",
    "후루룩": "slurp_noodle", "면": "slurp_noodle", "국물": "slurp_noodle",
    "탄산": "fizz_soda", "콜라": "fizz_soda", "사이다": "fizz_soda",
    "칼": "knife_chop", "썰": "knife_chop",
    "삐빅": "beep_scale", "체중계": "beep_scale",
    "끓": "pot_boil", "보글": "pot_boil",
    "김": "steam_hiss", "증기": "steam_hiss",
    "스우시": "whoosh_cut", "붐": "boom_impact",
}

RULES_NOTE = "AI 키 없이 규칙 기반으로 조정했습니다. 장면 묘사·대사 문장까지 바꾸려면 [설정]에서 AI 키를 넣어 주세요."


def _target_cuts(instruction: str, cuts: list[dict]) -> list[dict]:
    ids = [c["id"] for c in cuts]
    picked: list[int] = []
    for a, b in re.findall(r"(\d+)\s*[~\-]\s*(\d+)\s*컷", instruction):
        lo, hi = sorted((int(a), int(b)))
        picked += [i for i in ids if lo <= i <= hi]
    for n in re.findall(r"(\d+)\s*번?\s*컷", instruction):
        if int(n) in ids:
            picked.append(int(n))
    if picked:
        return [c for c in cuts if c["id"] in set(picked)]
    if any(k in instruction for k in ("마지막", "엔딩", "끝")):
        return [cuts[-1]]
    if any(k in instruction for k in ("첫", "처음", "오프닝", "훅")):
        return cuts[:3]
    return cuts


def _renormalize(cuts: list[dict], target: float | None) -> None:
    if not target:
        return
    cur = sum(c["dur"] for c in cuts if c.get("enabled", True))
    if cur <= 0:
        return
    k = target / cur
    for c in cuts:
        if c.get("enabled", True):
            c["dur"] = round(min(max(c["dur"] * k, 0.5), 4.5), 2)
    diff = round(target - sum(c["dur"] for c in cuts if c.get("enabled", True)), 2)
    if abs(diff) >= 0.01:
        idx = max(range(len(cuts)), key=lambda i: cuts[i]["dur"])
        cuts[idx]["dur"] = round(cuts[idx]["dur"] + diff, 2)


def rule_based(project: dict, instruction: str) -> dict:
    cuts = project["cuts"]
    applied: list[str] = []
    inst = instruction.strip()

    # 1) 전체 길이
    m = re.search(r"(\d+(?:\.\d+)?)\s*초", inst)
    target = float(m.group(1)) if m else None
    if target:
        lo, hi = planner.CONTENT_TYPES[project["content_type"]]["seconds"]
        target = min(max(target, lo), hi)
        _renormalize(cuts, target)
        project["target_seconds"] = target
        applied.append(f"전체 길이 {target}초로 재조정")

    # 2) 컷 속도
    picked = _target_cuts(inst, cuts)
    if any(k in inst for k in ("빠르", "짧게", "줄여", "압축")):
        factor = 0.82
        mm = re.search(r"(\d+(?:\.\d+)?)\s*배", inst)
        if mm:
            factor = 1.0 / max(float(mm.group(1)), 0.3)
        for c in picked:
            c["dur"] = round(max(c["dur"] * factor, 0.5), 2)
        applied.append(f"C{','.join(str(c['id']) for c in picked)} 더 빠르게({int(factor*100)}%)")
    elif any(k in inst for k in ("느리", "천천", "길게", "늘려")):
        factor = 1.22
        mm = re.search(r"(\d+(?:\.\d+)?)\s*배", inst)
        if mm:
            factor = max(float(mm.group(1)), 0.5)
        for c in picked:
            c["dur"] = round(min(c["dur"] * factor, 4.5), 2)
        applied.append(f"C{','.join(str(c['id']) for c in picked)} 더 느리게({int(factor*100)}%)")

    # 3) 효과음 볼륨·추가
    louder = any(k in inst for k in ("크게", "볼륨", "강하게", "키워"))
    quieter = any(k in inst for k in ("작게", "줄여", "조용"))
    touched = []
    for word, sfx_name in SFX_WORDS.items():
        if word in inst:
            for c in picked:
                names = list(c.get("sfx") or [])
                gain = float(c.get("sfx_gain", 0.0))
                if louder and not quieter:
                    if sfx_name not in names:
                        names.append(sfx_name)
                    gain = min(gain + 6.0, 12.0)
                elif quieter:
                    gain = max(gain - 6.0, -18.0)
                else:
                    if sfx_name not in names:
                        names.append(sfx_name)
                c["sfx"] = names[:3]
                c["sfx_gain"] = round(gain, 1)
                touched.append(c["id"])
    if touched:
        applied.append(f"C{','.join(str(x) for x in sorted(set(touched)))} 효과음 "
                       + ("크게" if louder and not quieter else ("작게" if quieter else "추가")))

    # 4) 훅 문구
    hm = re.search(r"훅[^\"'“”]*[\"'“”]([^\"'“”]{3,60})[\"'“”]", inst)
    if hm:
        project["hook"]["text"] = hm.group(1).strip()
        applied.append(f"훅 문구 변경: {project['hook']['text']}")

    # 5) 무음/플래시 토글
    opts = project.setdefault("options", {})
    if "플래시" in inst and any(k in inst for k in ("끄", "빼", "없")):
        opts["flash"] = False
        applied.append("0.000초 플래시 임팩트 OFF")
    if "플래시" in inst and any(k in inst for k in ("켜", "넣", "추가")):
        opts["flash"] = True
        applied.append("0.000초 플래시 임팩트 ON")
    if "루프" in inst and any(k in inst for k in ("끄", "빼")):
        opts["loop"] = False
        applied.append("무한루프 OFF")
    if "루프" in inst and any(k in inst for k in ("켜", "넣", "살려")):
        opts["loop"] = True
        applied.append("무한루프 ON")
    if "cta" in inst.lower() or "씨티에이" in inst:
        if any(k in inst for k in ("끄", "빼", "없")):
            opts["cta"] = False
            applied.append("CTA OFF")

    _renormalize(cuts, target)
    project["revisions"] = (project.get("revisions") or []) + [{
        "at": planner.datetime.now().isoformat(timespec="seconds"),
        "instruction": instruction, "applied": applied, "engine": "rule",
    }]
    planner.save_project(project)
    planner.write_image_request(project)
    return {"ok": True, "engine": "rule", "applied": applied or ["적용할 규칙을 찾지 못했습니다. 예: '3컷 더 빠르게, 치즈 소리 크게'"],
            "note": RULES_NOTE, "project": project}


AI_PROMPT = """현재 쇼츠 프로젝트다. 아래 '수정 지시'를 반영해 컷을 다시 써라.
반드시 JSON 만 출력한다.

[프로젝트]
{project}

[수정 지시]
{instruction}

[출력 스키마]
{{"hook": {{"text": "영문 한 줄 훅"}},
  "cuts": [{{"id": 1, "dur": 1.0, "scene": "영어 장면 묘사", "sfx": ["crunch_bite"], "sfx_gain": 3.0,
            "line": {{"speaker": "pizza|ramen|gimbap|hotdog", "ko": "한국어 대사", "en": "English subtitle"}}}}],
  "note": "무엇을 바꿨는지 한 줄"}}

조건:
- cuts 의 id 는 기존 컷 번호를 그대로 유지하고 개수도 {n}개 그대로.
- dur 합은 {total}초에 맞춘다(각 컷 0.5~4.5초).
- 무대사 모드면 line 을 넣지 않는다.
- sfx_gain 은 -12~+12 dB 범위.
"""


def ai_revise(project: dict, instruction: str) -> dict:
    compact = {
        "content_type": project["content_type"],
        "topic": project["topic"],
        "hook": project["hook"],
        "cuts": [{k: c[k] for k in ("id", "dur", "scene", "sfx", "line") if k in c} for c in project["cuts"]],
    }
    prompt = AI_PROMPT.format(project=json.dumps(compact, ensure_ascii=False, indent=1),
                              instruction=instruction, n=len(project["cuts"]),
                              total=project.get("total_seconds") or sum(c["dur"] for c in project["cuts"]))
    text = llm.generate_text(prompt, "JSON 만 출력한다.")
    data = llm.extract_json(text)
    applied = []
    if data.get("hook", {}).get("text"):
        project["hook"]["text"] = data["hook"]["text"]
        applied.append(f"훅: {project['hook']['text']}")
    by_id = {c["id"]: c for c in project["cuts"]}
    for inc in data.get("cuts", []):
        c = by_id.get(int(inc.get("id", -1)))
        if not c:
            continue
        if inc.get("dur"):
            c["dur"] = round(min(max(float(inc["dur"]), 0.5), 4.5), 2)
        if inc.get("scene"):
            c["scene"] = inc["scene"]
        if inc.get("sfx"):
            c["sfx"] = inc["sfx"][:3]
        if inc.get("sfx_gain") is not None:
            c["sfx_gain"] = round(min(max(float(inc["sfx_gain"]), -12), 12), 1)
        if project["content_type"] == "drama4" and inc.get("line"):
            c["line"] = inc["line"]
    _renormalize(project["cuts"], project.get("target_seconds"))
    project["revisions"] = (project.get("revisions") or []) + [{
        "at": planner.datetime.now().isoformat(timespec="seconds"),
        "instruction": instruction, "applied": applied or [data.get("note", "AI 재조정")], "engine": "ai",
    }]
    planner.save_project(project)
    planner.write_image_request(project)
    return {"ok": True, "engine": "ai", "applied": applied, "note": data.get("note", "AI가 시나리오를 다시 썼습니다."),
            "project": project}


def revise(project: dict, instruction: str) -> dict:
    """AI 키가 있으면 AI, 아니면 규칙 기반. AI 실패 시 규칙 기반으로 폴백."""
    if not instruction.strip():
        raise ValueError("수정 지시를 입력해 주세요.")
    provider = settings.load_settings()["models"].get("script_provider", "gemini")
    if settings.key(provider):
        try:
            return ai_revise(project, instruction)
        except Exception as e:
            res = rule_based(project, instruction)
            res["note"] = f"AI 수정 실패({e}) → 규칙 기반으로 조정했습니다."
            return res
    return rule_based(project, instruction)

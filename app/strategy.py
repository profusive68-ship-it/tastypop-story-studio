"""채널 전략 분석: 업로드 시간·사연(주제) 종류·묶음(합본) 전략을 제안하고, 승인하면 바로 진행."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import planner, settings, topics

KST = timezone(timedelta(hours=9))
CARDS_FILE = settings.CONFIG_DIR / "strategy_cards.json"

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def _load_cards() -> list[dict]:
    if CARDS_FILE.exists():
        try:
            return json.loads(CARDS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_cards(cards: list[dict]) -> None:
    settings.ensure_dirs()
    CARDS_FILE.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")


def _hour_buckets(videos: list[dict]) -> list[dict]:
    buckets: dict[int, dict] = {}
    for v in videos:
        try:
            dt = datetime.fromisoformat(v["published"].replace("Z", "+00:00")).astimezone(KST)
        except Exception:
            continue
        b = buckets.setdefault(dt.hour, {"hour": dt.hour, "n": 0, "views": 0})
        b["n"] += 1
        b["views"] += int(v.get("views", 0))
    out = []
    for b in buckets.values():
        b["avg"] = round(b["views"] / max(b["n"], 1), 1)
        out.append(b)
    return sorted(out, key=lambda x: x["avg"], reverse=True)


def _topic_keywords(videos: list[dict]) -> list[dict]:
    words: dict[str, dict] = {}
    for v in videos:
        for w in re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", v.get("title", "")):
            if w.lower() in {"kfood", "k-food", "asmr", "3d", "shorts", "tastypop", "story", "ep"}:
                continue
            d = words.setdefault(w, {"word": w, "n": 0, "views": 0})
            d["n"] += 1
            d["views"] += int(v.get("views", 0))
    out = []
    for d in words.values():
        if d["n"] >= 1:
            d["avg"] = round(d["views"] / d["n"], 1)
            out.append(d)
    return sorted(out, key=lambda x: (x["avg"], x["views"]), reverse=True)[:10]


def _duration_seconds(iso: str) -> float:
    import re as _re

    m = _re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0.0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def _category_of(title: str) -> str | None:
    """제목 키워드로 음식 카테고리를 추정."""
    for cat, items in topics.TOPICS.items():
        for topic, _hook, _concept, _sfx in items:
            words = [w for w in topic.replace("+", " ").split() if len(w) >= 2]
            if any(w in title for w in words[:2]):
                return cat
    keywords = {
        "bunsik": ["떡볶이", "라면", "불닭", "마라", "짜장", "분식", "김치찌개", "부대"],
        "cheese_night": ["치즈", "핫도그", "치킨", "야식", "삼겹살", "감자"],
        "winter": ["붕어빵", "호떡", "고구마", "어묵", "팥죽", "김치전", "떡국", "탕후루"],
        "convenience": ["편의점", "삼각김밥", "도시락", "컵밥"],
        "dessert": ["약과", "빙수", "케이크", "아이스크림", "유자", "경단", "디저트"],
        "squad": ["4인방", "피자설기", "김밥 456", "주방", "드라마"],
    }
    for cat, ws in keywords.items():
        if any(w in title for w in ws):
            return cat
    return None


def _category_stats(videos: list[dict]) -> list[dict]:
    stats: dict[str, dict] = {}
    for v in videos:
        cat = _category_of(v.get("title", ""))
        if not cat:
            continue
        d = stats.setdefault(cat, {"category": cat, "n": 0, "views": 0, "secs": []})
        d["n"] += 1
        d["views"] += int(v.get("views", 0))
        sec = _duration_seconds(v.get("duration", ""))
        if sec:
            d["secs"].append(sec)
    out = []
    for d in stats.values():
        name = next((c["name"] for c in topics.CATEGORIES if c["id"] == d["category"]), d["category"])
        out.append({"category": d["category"], "name": name, "n": d["n"],
                    "avg": round(d["views"] / max(d["n"], 1), 1),
                    "avg_seconds": round(sum(d["secs"]) / len(d["secs"]), 1) if d["secs"] else None})
    return sorted(out, key=lambda x: x["avg"], reverse=True)


def _best_length(videos: list[dict]) -> float:
    secs = [(_duration_seconds(v.get("duration", "")), int(v.get("views", 0))) for v in videos]
    secs = [(s, w) for s, w in secs if 5 <= s <= 65]
    if not secs:
        return 18.0
    # 조회수 가중 평균 길이 → 13.5~19초 범위로 제한
    total_w = sum(w for _s, w in secs) or 1
    avg = sum(s * w for s, w in secs) / total_w
    return round(min(max(avg, 13.5), 19.0) * 2) / 2


def analyze(videos: list[dict] | None = None, days: int = 30) -> dict:
    """유튜브 영상 통계(없으면 로컬 프로젝트) 로 채널 상태를 분석."""
    videos = videos or []
    projects = planner.list_projects()
    hours = _hour_buckets(videos) if videos else []
    topic_stats = _topic_keywords(videos) if videos else []
    cat_stats = _category_stats(videos) if videos else []
    best_len = _best_length(videos) if videos else 18.0
    rendered = [p for p in projects if p.get("has_video")]
    pending = [p for p in projects if not p.get("has_video")]

    best_hour = hours[0]["hour"] if hours else int(settings.load_settings()["youtube"].get("default_hour", 19))
    golden = settings.load_settings()["youtube"].get("golden_window", ["19:00", "20:30"])
    best_cat = cat_stats[0] if cat_stats else None

    cards: list[dict] = []

    # 0) 종합 전략 — 한 번에 제작까지 가는 카드
    if best_cat:
        pick = topics.recommend(best_cat["category"], count=1)[0]
        cards.append({
            "id": "strategy",
            "kind": "strategy",
            "icon": "🏆",
            "title": f"{best_cat['name']} · {best_len}초 · {best_hour:02d}:00 편성",
            "reason": (f"'{best_cat['name']}' 카테고리 평균 조회수 {best_cat['avg']:.0f}회(표본 {best_cat['n']}편), "
                       f"평균 길이 {best_cat['avg_seconds'] or best_len}초 → {best_len}초로 제작 추천. "
                       f"업로드는 {best_hour:02d}시대가 가장 반응이 좋았습니다. 추천 주제: {pick['topic']}"),
            "action": {"topic": pick["topic"], "hook": pick["hook"], "sfx": pick["sfx"],
                       "content_type": pick["content_type"], "seconds": best_len, "upload_hour": best_hour},
            "button": "✅ 이 전략으로 바로 제작하기",
        })
    else:
        pick = topics.today_pick()[0]
        cards.append({
            "id": "strategy",
            "kind": "strategy",
            "icon": "🏆",
            "title": f"첫 전략: {pick['topic']} · 18초 · 19:00 편성",
            "reason": "아직 채널 데이터가 적어, 내장 추천 DB의 반응 좋은 조합으로 첫 편을 제안합니다. "
                      "영상이 쌓이면 카테고리·길이·시간대를 데이터로 다시 추천합니다.",
            "action": {"topic": pick["topic"], "hook": pick["hook"], "sfx": pick["sfx"],
                       "content_type": pick["content_type"], "seconds": 18.0, "upload_hour": 19},
            "button": "✅ 이 전략으로 바로 제작하기",
        })

    cards.append({
        "id": "time",
        "kind": "time",
        "icon": "⏰",
        "title": f"업로드 시간: 매일 {best_hour:02d}:00 예약",
        "reason": (f"최근 영상 {len(videos)}개 중 {best_hour:02d}시대 평균 조회수가 가장 높습니다."
                   if hours else f"아직 채널 데이터가 적어 골든타임({golden[0]}~{golden[1]}) 기준으로 제안합니다."),
        "action": {"upload_hour": best_hour, "upload_minute": 0},
        "button": "이 시간으로 예약 적용",
    })

    if topic_stats:
        t = topic_stats[0]
        cards.append({
            "id": "topic",
            "kind": "topic",
            "icon": "🍽️",
            "title": f"다음 주제: {t['word']} 편성",
            "reason": f"제목 키워드 '{t['word']}' 평균 조회수 {t['avg']:.0f}회 (표본 {t['n']}편).",
            "action": {"topic": t["word"], "content_type": "asmr3d"},
            "button": "이 주제로 제작 시작",
        })
    else:
        cards.append({
            "id": "topic",
            "kind": "topic",
            "icon": "🍽️",
            "title": "다음 주제: 매운 떡볶이/불닭 계열",
            "reason": "한국·해외 동시 노출이 잘 되는 매운맛 소재 + 치즈 훅 조합을 추천합니다.",
            "action": {"topic": "불닭 치즈 떡볶이", "content_type": "asmr3d"},
            "button": "이 주제로 제작 시작",
        })

    cards.append({
        "id": "drama",
        "kind": "drama",
        "icon": "🎭",
        "title": "4인방 드라마 주 2회 (화·토)",
        "reason": "무대사 ASMR 사이에 스토리형을 섞으면 체류·공유 지표가 올라갑니다. 영어 자막 유지.",
        "action": {"content_type": "drama4", "weekdays": [1, 5], "topic": "새벽 2시 주방 대소동"},
        "button": "드라마 편성 승인",
    })

    if len(rendered) >= 2:
        cards.append({
            "id": "bundle",
            "kind": "bundle",
            "icon": "📦",
            "title": "주말 몰아보기 합본 (완성편 2~3개)",
            "reason": f"완성된 편이 {len(rendered)}개 있습니다. 주말 롱폼 합본으로 시청시간을 늘릴 수 있습니다.",
            "action": {"bundle": [p["id"] for p in rendered[:3]]},
            "button": "합본 만들기",
        })

    if pending:
        cards.append({
            "id": "finish",
            "kind": "finish",
            "icon": "🚧",
            "title": f"미완성 프로젝트 {len(pending)}개 마무리",
            "reason": "이미지/영상만 넣으면 바로 렌더됩니다: " + ", ".join(p["topic"] or p["id"] for p in pending[:3]),
            "action": {"projects": [p["id"] for p in pending[:3]]},
            "button": "목록 열기",
        })

    cards.append({
        "id": "series",
        "kind": "series",
        "icon": "🔢",
        "title": f"다음 편 번호: Ep.{max([p.get('ep') or 0 for p in projects] + [7]) + 1}",
        "reason": "시리즈 번호를 이어가면 채널 재방문· binge 시청에 유리합니다.",
        "action": {"next_ep": max([p.get("ep") or 0 for p in projects] + [7]) + 1},
        "button": "번호 그대로 사용",
    })

    result = {
        "analyzed_at": datetime.now(KST).isoformat(timespec="seconds"),
        "videos": len(videos),
        "hours": hours,
        "topics": topic_stats,
        "categories": cat_stats,
        "best_length": best_len,
        "projects": len(projects),
        "rendered": len(rendered),
        "cards": cards,
    }
    _save_cards(cards)
    return result


def get_card(card_id: str) -> dict | None:
    return next((c for c in _load_cards() if c["id"] == card_id), None)

"""카테고리별 K-푸드 주제 추천 DB + (키가 있으면) AI 트렌드 추천.

API 키가 없어도 60개 이상의 내장 추천이 항상 동작한다.
"""
from __future__ import annotations

import json
import random
from datetime import datetime

from . import llm, settings

CATEGORIES = [
    {"id": "bunsik", "icon": "🌶️", "name": "K-분식 · 매운맛", "desc": "떡볶이·라면·불닭·마라 — 해외 반응 좋은 매운맛"},
    {"id": "cheese_night", "icon": "🧀", "name": "치즈 · 야식 ASMR", "desc": "치즈 늘어남·새벽 2시 야식 — 소리 중심"},
    {"id": "winter", "icon": "❄️", "name": "계절 · 겨울 간식", "desc": "붕어빵·호떡·군고구마 — 감성·따뜻함"},
    {"id": "convenience", "icon": "🏪", "name": "편의점 꿀조합", "desc": "편의점 재료 조합 — 저장·공유 잘 되는 실용"},
    {"id": "dessert", "icon": "🍡", "name": "K-디저트 · 전통음식", "desc": "약과·호떡·떡·빙수 — 비주얼·문화 소개"},
    {"id": "squad", "icon": "🎭", "name": "4인방 캐릭터 · 시사 패러디", "desc": "피자설기·라면·김밥·핫도그 드라마"},
]

# 카테고리별 추천: (주제, 영문 훅, 한 줄 컨셉, 추천 ASMR 사운드)
TOPICS: dict[str, list[tuple[str, str, str, list[str]]]] = {
    "bunsik": [
        ("불닭 치즈 떡볶이", "Can you handle Lv.999?", "치즈가 매운맛을 덮는 0.9초 훅 → 떡볶이 3컷 연타", ["cheese_stretch", "slurp_noodle", "sizzle_grill"]),
        ("로제 떡볶이 크림 폭탄", "Would you eat this at 2 AM?", "크림 소스가 넘치는 클로즈업 → 면 후루룩", ["sauce_pour", "slurp_noodle", "fizz_soda"]),
        ("짜파게티 + 라면 반반", "Half black, half red?", "두 봉지 반반 조합, 젓가락으로 비비는 소리", ["slurp_noodle", "sauce_pour", "crunch_bite"]),
        ("마라탕 3단계 맵기", "Which level can you survive?", "1·2·3단계 맵기 비교 컷", ["pot_boil", "sizzle_grill", "slurp_noodle"]),
        ("컵라면 계란 2개", "The 3 a.m. classic", "끓는 물 → 계란 투하 → 김 모락모락", ["pot_boil", "steam_hiss", "slurp_noodle"]),
        ("쫄면 양념 비비기", "How spicy is too spicy?", "양념 범벅 손맛 클로즈업", ["slurp_noodle", "sauce_pour", "crunch_bite"]),
        ("김치찌개 끓이는 밤", "Grandma's 2 a.m. recipe", "보글보글 김치찌개 + 밥 한 공기", ["pot_boil", "steam_hiss", "slurp_noodle"]),
        ("부대찌개 라면 사리", "This is a K-army stew", "햄·소시지·라면 사리 조합", ["pot_boil", "sizzle_grill", "slurp_noodle"]),
        ("냉모밀 초겨울 반전", "Cold noodles in winter?", "차가운 면 vs 김이 오르는 육수 대비", ["slurp_noodle", "steam_hiss", "knife_chop"]),
        ("떡볶이 순대 튀김 세트", "The holy trinity of bunsik", "분식 3종 세트 한 상", ["sizzle_fry", "crunch_big", "sauce_pour"]),
    ],
    "cheese_night": [
        ("치즈 폭탄 핫도그", "How far will it stretch?", "모차렐라가 1m 늘어나는 슬로모션", ["cheese_stretch", "crunch_bite", "sizzle_fry"]),
        ("새벽 2시 치킨 + 맥주", "2 AM, fried chicken o'clock", "치킨 한 조각 + 탄산 캔 따는 소리", ["crunch_big", "fizz_soda", "sizzle_fry"]),
        ("불고기 치즈 감자튀김", "Cheese over fries, yes?", "감자튀김 위 치즈 폭포", ["cheese_stretch", "sizzle_fry", "sauce_pour"]),
        ("라면 위 슬라이스 치즈", "One slice changes everything", "라면 위 치즈가 녹아내리는 0.9초", ["slurp_noodle", "cheese_stretch", "pot_boil"]),
        ("삼겹살 불판 야식", "The sound of 2 AM", "불판 지글지글 + 쌈 싸기", ["sizzle_grill", "crunch_bite", "knife_chop"]),
        ("치즈 김밥 3종", "Cheese in gimbap? Trust me", "치즈 늘어나는 김밥 단면", ["cheese_stretch", "knife_chop", "crunch_bite"]),
        ("핫도그 치즈 밧줄", "Cheese rope battle", "4인방 치즈 줄다리기 소재", ["cheese_stretch", "pop_bubble", "boom_impact"]),
        ("떡볶이 치즈 그라탕", "Bake it. Thank me later", "오븐에서 꺼내는 치즈 그라탕", ["cheese_stretch", "steam_hiss", "sizzle_grill"]),
        ("감자 치즈 범벅", "Every bite pulls", "치즈 감자 범벅 클로즈업", ["cheese_stretch", "crunch_bite", "pop_bubble"]),
        ("야식 라면 계란 치즈", "The 3-step upgrade", "라면→계란→치즈 3단 업그레이드", ["pot_boil", "cheese_stretch", "slurp_noodle"]),
    ],
    "winter": [
        ("붕어빵 겨울 간식", "Red bean or custard?", "붕어빵 반 갈라 단면 + 김이 모락", ["crunch_bite", "steam_hiss", "pop_bubble"]),
        ("호떡 뒤집기", "Careful, it's hot", "호떡 뒤집는 소리 + 흑설탕 터짐", ["sizzle_grill", "pop_bubble", "steam_hiss"]),
        ("군고구마 쪼개기", "Winter in one bite", "군고구마 갈라지는 김", ["crunch_bite", "steam_hiss", "sizzle_grill"]),
        ("어묵 국물 한 컵", "One cup of winter", "어묵 꼬치 + 김이 오르는 국물", ["pot_boil", "steam_hiss", "slurp_noodle"]),
        ("팥죽 새알심", "Solstice in a bowl", "동지 팥죽 + 새알심", ["pot_boil", "slurp_noodle", "steam_hiss"]),
        ("김치전 노릇노릇", "Rainy day, kimchi pancake", "기름에 지지는 김치전", ["sizzle_fry", "crunch_big", "knife_chop"]),
        ("떡국 한 그릇", "New year in 18 seconds", "떡국 끓이는 새해 감성", ["pot_boil", "slurp_noodle", "steam_hiss"]),
        ("뻥튀기 기계 앞", "The loudest snack", "뻥! 터지는 순간 슬로모션", ["boom_impact", "crunch_big", "pop_bubble"]),
        ("겨울 딸기 탕후루", "Frozen, then crunch", "탕후루 유리 코팅 깨지는 소리", ["crunch_big", "fizz_soda", "pop_bubble"]),
        ("계란빵 김 모락", "Street corner, winter", "계란빵 단면 클로즈업", ["steam_hiss", "crunch_bite", "pop_bubble"]),
    ],
    "convenience": [
        ("편의점 라면 + 삼각김밥", "The 4,000 won dinner", "라면·삼각김밥·바나나우유 조합", ["pot_boil", "crunch_bite", "fizz_soda"]),
        ("편의점 컵밥 3종", "3 cups, 1 minute", "컵밥 3종 비교", ["sauce_pour", "crunch_bite", "pop_bubble"]),
        ("편의점 아이스크림 샌드", "Weird combo, works", "아이스크림+과자 조합", ["crunch_bite", "fizz_soda", "crunch_big"]),
        ("편의점 치킨 + 치즈볼", "5,900 won hack", "전자레인지 데우는 소리부터", ["beep_scale", "cheese_stretch", "crunch_big"]),
        ("편의점 초코우유 라면", "Would you try this?", "우유 라면 반전 조합", ["pot_boil", "slurp_noodle", "fizz_soda"]),
        ("편의점 김밥 3단", "Roll it yourself", "김밥 마는 손놀림", ["knife_chop", "crunch_bite", "pop_bubble"]),
        ("편의점 핫바 + 맥주", "Late night starter pack", "핫바 한 입 + 캔 따기", ["crunch_big", "fizz_soda", "sizzle_fry"]),
        ("편의점 떡볶이 컵", "Microwave in 90 seconds", "비커 떡볶이 조리 과정", ["beep_scale", "pot_boil", "sauce_pour"]),
        ("편의점 도시락 5찬", "Lunch box tour", "도시락 뚜껑 여는 순간", ["pop_bubble", "crunch_bite", "sauce_pour"]),
        ("편의점 젤리 5종 리뷰", "Which one is the best?", "젤리 5종 씹는 소리", ["pop_bubble", "crunch_bite", "fizz_soda"]),
    ],
    "dessert": [
        ("약과 꿀 폭탄", "K-dessert, 700 years old", "약과 부서지는 소리 + 꿀", ["crunch_big", "pop_bubble", "steam_hiss"]),
        ("인절미 팥빙수", "Shaved ice, Korean style", "우유 얼음 갈리는 소리", ["crunch_big", "fizz_soda", "pop_bubble"]),
        ("호떡 꿀 터짐", "Sweet lava inside", "호떡 반 갈라 꿀 흐르는 컷", ["pop_bubble", "crunch_bite", "sizzle_grill"]),
        ("핫초코 마시멜로", "Melt it, then sip", "마시멜로 녹는 클로즈업", ["steam_hiss", "slurp_noodle", "pop_bubble"]),
        ("떡 케이크 만들기", "No oven, all rice cake", "떡 케이크 데코 과정", ["knife_chop", "pop_bubble", "crunch_bite"]),
        ("흑임자 아이스크림", "Black sesame, trust me", "아이스크림 스쿱 소리", ["crunch_bite", "fizz_soda", "pop_bubble"]),
        ("꿀 타래 약과", "How long can it stretch?", "꿀 타래 늘어나는 슬로모션", ["cheese_stretch", "pop_bubble", "crunch_big"]),
        ("동지 팥죽 디저트", "Winter solstice bowl", "팥죽 위 새알심", ["pot_boil", "slurp_noodle", "steam_hiss"]),
        ("유자차 김 모락", "One cup of calm", "유자차 붓는 소리", ["steam_hiss", "sauce_pour", "pop_bubble"]),
        ("경단 3색", "Colorful rice balls", "경단 굴리는 손놀림", ["pop_bubble", "crunch_bite", "knife_chop"]),
    ],
    "squad": [
        ("새벽 2시 주방 탈출", "Only 4 of them. At 2 AM.", "냉장고 문 틈 4인방 빼꼼 → 주방 접수", ["pop_bubble", "boom_impact", "whoosh_cut"]),
        ("치즈 줄다리기 대전", "Who gets the last cheese?", "라면 vs 핫도그 치즈 밧줄", ["cheese_stretch", "boom_impact", "pop_bubble"]),
        ("체중계 앞 4인방", "The scale never lies", "체중계 삐빅 → 도망가는 4인방", ["beep_scale", "whoosh_cut", "heart_pop"]),
        ("편의점 신메뉴 쟁탈전", "One left. Four hungry.", "마지막 하나를 두고 눈치싸움", ["pop_bubble", "boom_impact", "crunch_bite"]),
        ("김밥 456의 첫 출근", "Day one at the kitchen", "소심 막내의 첫 등장", ["pop_bubble", "heart_pop", "whoosh_cut"]),
        ("핫도그의 거짓말", "He said he wasn't hungry", "해맑은 거짓말 → 들키는 순간", ["pop_bubble", "boom_impact", "crunch_bite"]),
        ("피자설기의 야식 회의", "Emergency meeting, 2 AM", "맏형의 능글 회의", ["pop_bubble", "whoosh_cut", "heart_pop"]),
        ("라면의 분노 조절", "Count to three... or not", "다혈질 라면 참기 훈련", ["boom_impact", "pot_boil", "pop_bubble"]),
        ("4인방 아침 회의", "Who ate the last one?", "아침 주방 범인 찾기", ["knife_chop", "pop_bubble", "beep_scale"]),
        ("신입 등장 떡밥", "A new member is coming", "문틈 실루엣 → 다음 편 예고", ["whoosh_cut", "heart_pop", "boom_impact"]),
    ],
}


def _all_items() -> list[dict]:
    out = []
    for cat in CATEGORIES:
        for topic, hook, concept, sfx in TOPICS.get(cat["id"], []):
            out.append({"category": cat["id"], "category_name": cat["name"], "icon": cat["icon"],
                        "topic": topic, "hook": hook, "concept": concept, "sfx": sfx,
                        "content_type": "drama4" if cat["id"] == "squad" else "asmr3d"})
    return out


def categories() -> list[dict]:
    return [dict(c, count=len(TOPICS.get(c["id"], []))) for c in CATEGORIES]


def recommend(category: str | None = None, count: int = 4, exclude: list[str] | None = None) -> list[dict]:
    items = _all_items()
    if category and category != "all":
        items = [i for i in items if i["category"] == category]
    exclude = set(exclude or [])
    items = [i for i in items if i["topic"] not in exclude] or items
    random.shuffle(items)
    return items[:count]


def today_pick() -> list[dict]:
    """요일별 자동 편성: 매일 같은 카테고리를 돌려 준다."""
    idx = datetime.now().weekday() % len(CATEGORIES)
    cat = CATEGORIES[idx]["id"]
    return recommend(cat, count=3)


def ai_trends(count: int = 5) -> dict:
    """API 키가 있을 때: 최신 K-푸드 트렌드 기반 추천. 실패하면 내장 DB 로 폴백."""
    provider = settings.load_settings()["models"].get("script_provider", "gemini")
    has_key = bool(settings.key(provider))
    if not has_key:
        return {"ok": False, "source": "builtin", "items": recommend(None, count),
                "note": "AI 키가 없어 내장 추천 DB 로 제안했습니다."}
    prompt = f"""2026년 11월 기준 한국·해외에서 반응이 좋은 K-푸드 쇼츠 주제 {count}개를 추천해라.
조건: 3D 음식 애니메이션 ASMR 또는 4인방 캐릭터 드라마로 만들 수 있는 것.
JSON 만 출력: {{"items":[{{"topic":"한글 주제","hook":"영문 한 줄 훅","concept":"한 줄 연출 컨셉","category":"bunsik|cheese_night|winter|convenience|dessert|squad","content_type":"asmr3d|drama4"}}]}}"""
    try:
        text = llm.generate_text(prompt, "JSON 만 출력한다.", provider=provider)
        data = llm.extract_json(text)
        items = data.get("items", [])[:count]
        for i in items:
            i.setdefault("content_type", "drama4" if i.get("category") == "squad" else "asmr3d")
            i.setdefault("icon", "✨")
            i["category_name"] = next((c["name"] for c in CATEGORIES if c["id"] == i.get("category")), "AI 추천")
        return {"ok": True, "source": "ai", "items": items, "note": "AI 최신 트렌드 추천"}
    except Exception as e:
        return {"ok": False, "source": "builtin", "items": recommend(None, count),
                "note": f"AI 추천 실패({e}) — 내장 추천으로 대체했습니다."}


def apply_recommendation(topic_item: dict) -> dict:
    return {
        "topic": topic_item.get("topic"),
        "hook": topic_item.get("hook"),
        "content_type": topic_item.get("content_type", "asmr3d"),
        "sfx": topic_item.get("sfx") or [],
    }

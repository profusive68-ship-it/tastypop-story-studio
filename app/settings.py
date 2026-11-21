"""TastyPop Studio - 설정·경로 관리.

코드(app/)와 사용자 데이터(config/, projects/)를 완전히 분리한다.
새 버전을 덮어씌워도 API 키·로그인·만든 영상은 그대로 남는다.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent          # 패키지 루트
APP_SUB = APP_DIR / "app"
WEB_DIR = APP_SUB / "web"
CONFIG_DIR = APP_DIR / "config"
PROJECTS_DIR = APP_DIR / "projects"
SFX_DIR = APP_DIR / "sfx_library"
FONT_DIR = APP_DIR / "fonts"
CACHE_DIR = CONFIG_DIR / "cache"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

_lock = threading.RLock()


def read_version() -> str:
    try:
        return (APP_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return "1.0"


VERSION = read_version()

DEFAULTS: dict = {
    "keys": {
        "openai": "",
        "anthropic": "",
        "gemini": "",
        "kling_access_key": "",
        "kling_secret_key": "",
        "kling_api_key": "",
    },
    "models": {
        # 대본(시나리오) 생성 모델
        "script_provider": "gemini",              # gemini | openai | anthropic
        "script_model_gemini": "gemini-2.5-flash",
        "script_model_openai": "gpt-4.1",
        "script_model_anthropic": "claude-sonnet-4-5",
        # 이미지(키프레임) 생성 모델 우선순위 - 앞에서부터 시도
        "image_models": [
            "gemini-2.5-flash-image",
            "gemini-2.0-flash-preview-image-generation",
            "imagen-4.0-generate-001",
        ],
        # 영상화(Kling) 모델
        "kling_model": "kling-v2-1",
        "kling_mode": "std",                       # std | pro
        "kling_endpoint": "https://api.klingai.com/v1/videos/image2video",
    },
    "render": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "lufs": -12.5,
        "true_peak": -1.5,
        "loop_crossfade": 0.25,
        "hook_start": 0.0,
        "hook_end": 0.9,
        "hook_size": 66,
        "hook_y": 210,
        "cta_seconds": 1.5,
        "safe_area": 0.78,
        "encoder": "auto",                         # auto | h264_videotoolbox | libx264
        "crf": 18,
        "bitrate": "8M",
        "kenburns": True,
    },
    "content": {
        "default_type": "asmr3d",
        "series_tag": "K-Food 3D",
        "ep_start": 8,
        "hook_flash": True,
        "cut_whoosh": True,
        "cta_like": "♥ Like · ✈ Share",
        "cta_save": "Save for 2 AM",
        "language_style": "en",                    # 훅·CTA 는 영문 한 줄
    },
    "youtube": {
        "client_secret": "",                       # config/client_secret.json 경로(비우면 기본)
        "token_file": "",                          # 기본 config/youtube_token.json
        "channel_id": "",
        "default_privacy": "private",              # private | unlisted | public
        "default_hour": 19,
        "default_minute": 0,
        "default_second": 0,
        "golden_window": ["19:00", "20:30"],
        "category_id": "24",
        "made_for_kids": False,
        "auto_ai_disclosure": True,
    },
    "instagram": {
        "account": "@tastypop_story",
        "checklist": ["AI 정보 ON", "고화질 ON", "Meta 음성 번역 OFF"],
        "poll_choices": ["매운맛 도전", "치즈 폭탄", "다음 신메뉴"],
    },
    "scheduler": {
        "enabled": False,
        "time": "07:00",
        "weekdays": [0, 1, 2, 3, 4, 5, 6],
        "content_type": "asmr3d",
        "auto_upload": False,
        "upload_hour": 19,
        "next_ep": None,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8766,
    },
    "ui": {
        "last_tab": "home",
    },
}


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, PROJECTS_DIR, SFX_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    with _lock:
        ensure_dirs()
        data = {}
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        return _deep_merge(DEFAULTS, data)


def save_settings(patch: dict) -> dict:
    """부분 업데이트(깊은 병합) 후 저장."""
    with _lock:
        cur = load_settings()
        merged = _deep_merge(cur, patch or {})
        ensure_dirs()
        SETTINGS_FILE.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(SETTINGS_FILE, 0o600)
        except Exception:
            pass
        return merged


def key(name: str) -> str:
    """API 키 조회: 설정 파일 우선, 없으면 환경변수."""
    env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "kling_access_key": "KLING_ACCESS_KEY",
        "kling_secret_key": "KLING_SECRET_KEY",
        "kling_api_key": "KLING_API_KEY",
    }.get(name, name.upper())
    s = load_settings()
    return (s["keys"].get(name) or os.environ.get(env) or "").strip()


def project_dir(ep_id: str) -> Path:
    return PROJECTS_DIR / ep_id


def client_secret_path() -> Path:
    s = load_settings()["youtube"]
    p = (s.get("client_secret") or "").strip()
    if p:
        q = Path(os.path.expanduser(p))
        return q if q.is_absolute() else APP_DIR / q
    return CONFIG_DIR / "client_secret.json"


def youtube_token_path() -> Path:
    s = load_settings()["youtube"]
    p = (s.get("token_file") or "").strip()
    if p:
        q = Path(os.path.expanduser(p))
        return q if q.is_absolute() else APP_DIR / q
    return CONFIG_DIR / "youtube_token.json"

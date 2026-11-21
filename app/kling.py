"""Kling AI 이미지→영상(I2V) API 연동.

인증: Access Key/Secret Key → HS256 JWT (외부 PyJWT 의존 없이 표준 라이브러리로 서명)
흐름: POST /videos/image2video → task_id → GET /videos/image2video/{id} 폴링 → mp4 다운로드
API 키가 없으면 KlingError 로 알리고, 0원 협업 모드(clips/ 폴더)로 진행하면 된다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import requests

from . import settings


class KlingError(RuntimeError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt(ak: str, sk: str) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": ak, "exp": now + 1800, "nbf": now - 5}
    signing = (_b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
               _b64url(json.dumps(payload, separators=(",", ":")).encode()))
    sig = hmac.new(sk.encode(), signing.encode(), hashlib.sha256).digest()
    return signing + "." + _b64url(sig)


def _headers() -> dict:
    s = settings.load_settings()
    api_key = settings.key("kling_api_key")
    if api_key:  # API Key 단독 인증(2026년 신규 방식)
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    ak, sk = settings.key("kling_access_key"), settings.key("kling_secret_key")
    if not (ak and sk):
        raise KlingError("Kling 키가 없습니다. [설정] 탭에서 Access Key/Secret Key(또는 API Key)를 입력해 주세요.")
    return {"Authorization": f"Bearer {_jwt(ak, sk)}", "Content-Type": "application/json"}


def _endpoint() -> str:
    s = settings.load_settings()["models"]
    return (s.get("kling_endpoint") or "https://api.klingai.com/v1/videos/image2video").rstrip("/")


def create_task(image_path: Path, prompt: str, duration: int = 5, cfg_scale: float = 0.5,
                negative: str = "blurry, distorted, text, watermark, logo") -> str:
    settings_s = settings.load_settings()["models"]
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    body = {
        "model_name": settings_s.get("kling_model", "kling-v2-1"),
        "image": b64,
        "prompt": prompt[:2000],
        "negative_prompt": negative,
        "duration": str(int(duration)),
        "mode": settings_s.get("kling_mode", "std"),
        "cfg_scale": float(cfg_scale),
    }
    try:
        r = requests.post(_endpoint(), headers=_headers(), json=body, timeout=180)
    except requests.RequestException as e:
        raise KlingError(f"Kling 네트워크 오류: {e}") from e
    data = {}
    try:
        data = r.json()
    except Exception:
        pass
    if r.status_code >= 400 or data.get("code") not in (0, None):
        raise KlingError(f"Kling 요청 실패({r.status_code}): {r.text[:400]}")
    tid = (data.get("data") or {}).get("task_id")
    if not tid:
        raise KlingError(f"Kling task_id 없음: {json.dumps(data)[:300]}")
    return tid


def wait_task(task_id: str, timeout: int = 900, on_progress=None) -> str:
    url = f"{_endpoint()}/{task_id}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(url, headers=_headers(), timeout=60)
            data = r.json()
        except Exception as e:
            time.sleep(5)
            continue
        d = data.get("data") or {}
        st = d.get("task_status")
        if on_progress:
            on_progress(st, d.get("task_status_msg", ""))
        if st == "succeed":
            videos = (d.get("task_result") or {}).get("videos") or []
            if not videos:
                raise KlingError("Kling 성공 응답에 영상이 없습니다.")
            return videos[0]["url"]
        if st == "failed":
            raise KlingError(f"Kling 생성 실패: {d.get('task_status_msg', '')}")
        time.sleep(6)
    raise KlingError("Kling 응답 시간 초과(15분). 잠시 후 다시 시도해 주세요.")


def download(url: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    except requests.RequestException as e:
        raise KlingError(f"영상 다운로드 실패: {e}") from e
    return out_path


def image_to_video(image_path: Path, prompt: str, out_path: Path, duration: int = 5, on_progress=None) -> Path:
    tid = create_task(image_path, prompt, duration=duration)
    if on_progress:
        on_progress("submitted", tid)
    url = wait_task(tid, on_progress=on_progress)
    return download(url, out_path)

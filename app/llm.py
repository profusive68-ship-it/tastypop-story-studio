"""LLM 연동: 대본(OpenAI·Claude·Gemini) + 키프레임 이미지(Gemini).

API 키가 없으면 명확한 한국어 오류를 돌려주고, 0원 협업 모드로 계속 진행할 수 있게 한다.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import requests

from . import settings

TIMEOUT = 180


class LLMError(RuntimeError):
    pass


def _post(url: str, *, headers: dict, payload: dict, timeout: int = TIMEOUT) -> dict:
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise LLMError(f"네트워크 오류: {e}") from e
    if r.status_code >= 400:
        detail = r.text[:600]
        raise LLMError(f"API 오류 {r.status_code}: {detail}")
    try:
        return r.json()
    except Exception:
        raise LLMError(f"응답을 해석할 수 없습니다: {r.text[:300]}")


# ------------------------------------------------------------------ 텍스트
def gemini_text(prompt: str, system: str = "", model: str | None = None) -> str:
    k = settings.key("gemini")
    if not k:
        raise LLMError("Gemini API 키가 없습니다. [설정] 탭에서 입력해 주세요.")
    s = settings.load_settings()
    primary = model or s["models"]["script_model_gemini"]
    candidates = []
    for m in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite", primary, "gemini-3.8-flash"):
        if m and m not in candidates:
            candidates.append(m)
    payload: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                     "generationConfig": {"temperature": 0.9, "maxOutputTokens": 4096}}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    last_err: Exception | None = None
    data = None
    for cand in candidates:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{cand}:generateContent?key={k}"
        try:
            data = _post(url, headers={"Content-Type": "application/json"}, payload=payload, timeout=12)
            break
        except LLMError as e:
            last_err = e
            msg = str(e)
            if any(t in msg for t in ("503", "429", "404", "NOT_FOUND", "no longer available",
                                      "UNAVAILABLE", "overloaded", "RESOURCE_EXHAUSTED",
                                      "timed out", "Timeout", "네트워크 오류")):
                continue
            raise
    if data is None:
        raise last_err or LLMError("Gemini 호출 실패")
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except Exception:
        raise LLMError(f"Gemini 응답 형식 오류: {json.dumps(data)[:300]}")


def openai_text(prompt: str, system: str = "", model: str | None = None) -> str:
    k = settings.key("openai")
    if not k:
        raise LLMError("OpenAI API 키가 없습니다. [설정] 탭에서 입력해 주세요.")
    s = settings.load_settings()
    model = model or s["models"]["script_model_openai"]
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    data = _post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
        payload={"model": model, "messages": msgs, "temperature": 0.9,
                 "response_format": {"type": "json_object"}},
    )
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        raise LLMError(f"OpenAI 응답 형식 오류: {json.dumps(data)[:300]}")


def anthropic_text(prompt: str, system: str = "", model: str | None = None) -> str:
    k = settings.key("anthropic")
    if not k:
        raise LLMError("Claude API 키가 없습니다. [설정] 탭에서 입력해 주세요.")
    s = settings.load_settings()
    model = model or s["models"]["script_model_anthropic"]
    data = _post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": k, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        payload={"model": model, "max_tokens": 4096,
                 "system": system or "JSON 만 출력한다.",
                 "messages": [{"role": "user", "content": prompt}]},
    )
    try:
        return "".join(b.get("text", "") for b in data["content"]).strip()
    except Exception:
        raise LLMError(f"Claude 응답 형식 오류: {json.dumps(data)[:300]}")


def generate_text(prompt: str, system: str = "", provider: str | None = None) -> str:
    s = settings.load_settings()
    provider = provider or s["models"].get("script_provider", "gemini")
    fn = {"gemini": gemini_text, "openai": openai_text, "anthropic": anthropic_text}.get(provider)
    if not fn:
        raise LLMError(f"알 수 없는 대본 제공자: {provider}")
    return fn(prompt, system)


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    raise LLMError("대본 JSON 을 해석하지 못했습니다. 다시 시도해 주세요.")


# ------------------------------------------------------------------ 이미지
def _save_b64(b64: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(b64))
    return out_path


def gemini_image(prompt: str, out_path: Path, model: str) -> Path:
    k = settings.key("gemini")
    if not k:
        raise LLMError("Gemini API 키가 없습니다. [설정] 탭에서 입력해 주세요.")
    if model.startswith("imagen"):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict?key={k}"
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": 1, "aspectRatio": "9:16", "personGeneration": "allow_adult"},
        }
        data = _post(url, headers={"Content-Type": "application/json"}, payload=payload)
        preds = data.get("predictions") or []
        if not preds:
            raise LLMError(f"이미지 응답이 비어 있습니다: {json.dumps(data)[:300]}")
        p0 = preds[0]
        b64 = (p0.get("bytesBase64Encoded") or p0.get("image", {}).get("bytesBase64Encoded")
               or (p0.get("predictions") or [{}])[0].get("bytesBase64Encoded"))
        if not b64:
            raise LLMError("이미지 데이터가 없습니다.")
        return _save_b64(b64, out_path)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={k}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }
    data = _post(url, headers={"Content-Type": "application/json"}, payload=payload, timeout=300)
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except Exception:
        raise LLMError(f"이미지 응답 형식 오류: {json.dumps(data)[:300]}")
    for p in parts:
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and inline.get("data"):
            return _save_b64(inline["data"], out_path)
    raise LLMError("이미지 파트를 찾지 못했습니다(모델이 텍스트만 반환).")


def generate_image(prompt: str, out_path: Path) -> tuple[Path, str]:
    """설정된 이미지 모델들을 순서대로 시도. 반환 (경로, 사용모델)."""
    s = settings.load_settings()
    errors = []
    for model in s["models"].get("image_models", []):
        try:
            p = gemini_image(prompt, out_path, model)
            return p, model
        except Exception as e:
            errors.append(f"{model}: {e}")
            continue
    raise LLMError("이미지 생성 실패\n" + "\n".join(errors[-3:]))


def provider_status() -> list[dict]:
    s = settings.load_settings()
    return [
        {"name": "Gemini", "key": "gemini", "ok": bool(settings.key("gemini")),
         "use": "대본·이미지", "model": s["models"]["script_model_gemini"]},
        {"name": "OpenAI", "key": "openai", "ok": bool(settings.key("openai")),
         "use": "대본", "model": s["models"]["script_model_openai"]},
        {"name": "Claude", "key": "anthropic", "ok": bool(settings.key("anthropic")),
         "use": "대본", "model": s["models"]["script_model_anthropic"]},
        {"name": "Kling", "key": "kling", "ok": bool(settings.key("kling_api_key") or (settings.key("kling_access_key") and settings.key("kling_secret_key"))),
         "use": "영상화", "model": s["models"]["kling_model"]},
    ]

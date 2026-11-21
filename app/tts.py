"""음성: edge-tts 다중 성우(피치·속도) + 룸 리버브, faster-whisper 발음 검수.

- 한국어 대사만 합성하고 자막은 영어만 띄운다(채널 표준).
- 성우별 pitch/rate 로 4인방 캐릭터를 구분한다.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from . import ffmpeg_tools, settings
from .planner import SQUAD

try:
    import edge_tts  # type: ignore
except Exception:  # pragma: no cover
    edge_tts = None


class TTSError(RuntimeError):
    pass


def _room_filter(preset: str = "kitchen") -> str:
    if preset == "none":
        return "anull"
    if preset == "big":
        return "aecho=0.85:0.9:40|75:0.28|0.18"
    # 작은 주방 룸
    return "aecho=0.8:0.85:18|36:0.22|0.14"


def synth(text: str, out_mp3: Path, voice: str, pitch: str = "+0Hz", rate: str = "+0%") -> Path:
    if edge_tts is None:
        raise TTSError("edge-tts 가 설치되어 있지 않습니다. start.command 로 다시 설치해 주세요.")
    out_mp3.parent.mkdir(parents=True, exist_ok=True)

    async def _go():
        c = edge_tts.Communicate(text, voice, pitch=pitch, rate=rate)
        with open(out_mp3, "wb") as f:
            async for chunk in c.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])

    try:
        asyncio.run(_go())
    except Exception as e:
        raise TTSError(f"음성 합성 실패: {e}") from e
    if not out_mp3.exists() or out_mp3.stat().st_size < 1000:
        raise TTSError("음성 파일이 비어 있습니다(네트워크 확인).")
    return out_mp3


def to_wav(src: Path, out_wav: Path, room: str = "kitchen", gain_db: float = 0.0) -> Path:
    """mp3 → 48kHz wav + 룸 리버브 + 앞뒤 무음 제거(무음 0 원칙)."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    trim = ("silenceremove=start_periods=1:start_silence=0.02:start_threshold=-45dB,"
            "areverse,silenceremove=start_periods=1:start_silence=0.05:start_threshold=-45dB,areverse")
    af = f"{trim},{_room_filter(room)}"
    if gain_db:
        af += f",volume={gain_db}dB"
    cmd = [
        ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-af", af, "-ar", "48000", "-ac", "2",
        str(out_wav),
    ]
    rc, out = ffmpeg_tools.run(cmd, timeout=180)
    if rc != 0:
        raise TTSError(f"음성 변환 실패: {out[-400:]}")
    return out_wav


def synthesize_dialogue(cuts: list[dict], work_dir: Path, voices: dict | None = None) -> list[dict]:
    """드라마 컷의 대사를 성우별로 합성. 반환 [{index, wav, dur, ko, en, speaker}]"""
    voices = voices or SQUAD
    work_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, c in enumerate(cuts):
        line = c.get("line") or {}
        ko = (line.get("ko") or "").strip()
        if not ko:
            out.append({"index": i, "wav": None, "dur": 0.0, "ko": "", "en": line.get("en", ""),
                        "speaker": line.get("speaker", "")})
            continue
        sp = voices.get(line.get("speaker", ""), voices.get("pizza", {}))
        voice = sp.get("voice", "ko-KR-InJoonNeural")
        mp3 = work_dir / f"c{i+1:02d}.mp3"
        wav = work_dir / f"c{i+1:02d}.wav"
        synth(ko, mp3, voice, pitch=sp.get("pitch", "+0Hz"), rate=sp.get("rate", "+0%"))
        to_wav(mp3, wav, room="kitchen", gain_db=1.5)
        out.append({
            "index": i,
            "wav": wav,
            "dur": ffmpeg_tools.duration(wav),
            "ko": ko,
            "en": line.get("en", ""),
            "speaker": line.get("speaker", ""),
        })
    return out


def subtitle_timings(dialogue: list[dict], cuts: list[dict]) -> list[dict]:
    """컷 시작 시각 + 음성 길이 기준으로 영어 자막 타이밍 계산."""
    t = 0.0
    out = []
    for d, c in zip(dialogue, cuts):
        start = t
        dur = c["dur"]
        if d.get("en"):
            speak = min(d["dur"] or dur * 0.9, dur * 0.95)
            out.append({"start": round(start + 0.05, 3), "end": round(start + max(0.5, speak), 3),
                        "text": d["en"], "speaker": d.get("speaker", "")})
        t += dur
    return out


# ------------------------------------------------------------- 발음 검수
_WHISPER = None


def whisper_model(size: str = "small"):
    global _WHISPER
    if _WHISPER is None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as e:  # pragma: no cover
            raise TTSError(f"faster-whisper 미설치: {e}") from e
        _WHISPER = WhisperModel(size, device="cpu", compute_type="int8")
    return _WHISPER


def transcribe(path: Path, language: str = "ko") -> list[dict]:
    m = whisper_model()
    segments, _info = m.transcribe(str(path), language=language, vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": (s.text or "").strip()} for s in segments]


def normalize_korean(text: str) -> str:
    text = re.sub(r"[^가-힣0-9A-Za-z ]", "", text)
    return text.replace(" ", "")


def compare_pronunciation(expected_lines: list[str], heard: str) -> list[dict]:
    """기대 대사와 인식 결과를 비교해 누락·오인식 후보를 표시(간이 검수)."""
    heard_n = normalize_korean(heard)
    rows = []
    for line in expected_lines:
        n = normalize_korean(line)
        if not n:
            continue
        if n in heard_n:
            rows.append({"line": line, "status": "ok", "score": 1.0})
            continue
        hit = sum(1 for ch in n if ch in heard_n)
        score = hit / max(len(n), 1)
        rows.append({"line": line, "status": "ok" if score >= 0.85 else "확인", "score": round(score, 2)})
    return rows

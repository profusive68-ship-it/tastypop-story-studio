"""ffmpeg / ffprobe 실행 도구와 맥북 하드웨어 가속 감지."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from . import settings

_encoder_lock = threading.Lock()
_encoder_cache: dict | None = None


class FFmpegMissing(RuntimeError):
    pass


def _candidates(name: str) -> list[str]:
    cands: list[str] = []
    env = os.environ.get("FFMPEG_BINARY" if name == "ffmpeg" else "FFPROBE_BINARY")
    if env:
        cands.append(env)
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        cands.append(str(Path(d) / name))
    which = shutil.which(name)
    if which:
        cands.append(which)
    return cands


def ffmpeg() -> str:
    for c in _candidates("ffmpeg"):
        if c and Path(c).exists() and os.access(c, os.X_OK):
            return c
    # imageio-ffmpeg 폴백 (brew 없이도 동작)
    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    raise FFmpegMissing(
        "ffmpeg 를 찾을 수 없습니다. 터미널에서 `brew install ffmpeg` 를 실행한 뒤 다시 시작해 주세요."
    )


def ffprobe() -> str | None:
    """ffprobe 경로(없으면 None — ffmpeg 출력 파싱으로 대체)."""
    for c in _candidates("ffprobe"):
        if c and Path(c).exists() and os.access(c, os.X_OK):
            return c
    name = Path(ffmpeg()).name.replace("ffmpeg", "ffprobe")
    sib = Path(ffmpeg()).with_name(name)
    if sib.exists():
        return str(sib)
    return None


def _parse_ffmpeg_info(path: Path | str) -> dict:
    """ffprobe 가 없을 때 `ffmpeg -i` 출력으로 길이·해상도·코덱을 읽는다."""
    cmd = [ffmpeg(), "-hide_banner", "-i", str(path)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, errors="replace")
        out = (p.stderr or "") + (p.stdout or "")
    except Exception:
        return {}
    info: dict = {"format": {}, "streams": []}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info["format"]["duration"] = f"{h*3600 + mi*60 + s:.3f}"
    for line in out.splitlines():
        if "Stream #" not in line:
            continue
        stream: dict = {}
        mm = re.search(r"Video:\s*([a-zA-Z0-9_]+)", line)
        if mm:
            stream["codec_type"] = "video"
            stream["codec_name"] = mm.group(1)
            dim = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if dim:
                stream["width"], stream["height"] = int(dim.group(1)), int(dim.group(2))
            fr = re.search(r"([\d.]+)\s*fps", line)
            if fr:
                stream["r_frame_rate"] = f"{fr.group(1)}/1"
        ma = re.search(r"Audio:\s*([a-zA-Z0-9_]+)", line)
        if ma:
            stream["codec_type"] = "audio"
            stream["codec_name"] = ma.group(1)
        if stream:
            info["streams"].append(stream)
    return info


def run(cmd: list[str], timeout: int = 900, log_path: Path | None = None) -> tuple[int, str]:
    """ffmpeg 실행. 반환 (returncode, stderr 꼬리)."""
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
            errors="replace",
        )
        out = p.stdout or ""
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        out += "\n[timeout]"
        rc = 124
    if log_path:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("$ " + " ".join(cmd) + "\n")
                f.write(out[-20000:] + "\n")
        except Exception:
            pass
    return rc, out


def probe(path: Path | str) -> dict:
    fp = ffprobe()
    if not fp:
        return _parse_ffmpeg_info(path)
    cmd = [
        fp, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return json.loads(p.stdout or "{}")
    except Exception:
        return _parse_ffmpeg_info(path)


def duration(path: Path | str) -> float:
    info = probe(path)
    try:
        return float(info["format"]["duration"])
    except Exception:
        pass
    for s in info.get("streams", []):
        try:
            return float(s.get("duration", 0))
        except Exception:
            continue
    return 0.0


def has_audio(path: Path | str) -> bool:
    info = probe(path)
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def video_size(path: Path | str) -> tuple[int, int]:
    info = probe(path)
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            try:
                return int(s["width"]), int(s["height"])
            except Exception:
                pass
    return 0, 0


def _test_videotoolbox() -> bool:
    """짧은 더미 인코딩으로 h264_videotoolbox 실제 동작 여부 확인(결과 캐시)."""
    cache = settings.CACHE_DIR / "encoder.json"
    try:
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("ffmpeg") == ffmpeg():
                return bool(data.get("videotoolbox"))
    except Exception:
        pass
    ok = False
    try:
        cmd = [
            ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:r=30:d=0.2",
            "-c:v", "h264_videotoolbox", "-f", "null", "-",
        ]
        p = subprocess.run(cmd, capture_output=True, timeout=60)
        ok = p.returncode == 0
    except Exception:
        ok = False
    try:
        settings.ensure_dirs()
        cache.write_text(json.dumps({"ffmpeg": ffmpeg(), "videotoolbox": ok}), encoding="utf-8")
    except Exception:
        pass
    return ok


def encoder_args(crf: int | None = None, bitrate: str | None = None) -> list[str]:
    """하드웨어 가속 인코더 선택: h264_videotoolbox → libx264 폴백."""
    global _encoder_cache
    s = settings.load_settings()["render"]
    want = (s.get("encoder") or "auto").lower()
    crf = s.get("crf", 18) if crf is None else crf
    bitrate = s.get("bitrate", "8M") if bitrate is None else bitrate

    with _encoder_lock:
        if _encoder_cache is None:
            _encoder_cache = {"videotoolbox": _test_videotoolbox()}

    use_vt = want == "h264_videotoolbox" or (want == "auto" and _encoder_cache["videotoolbox"])
    if use_vt:
        return [
            "-c:v", "h264_videotoolbox",
            "-b:v", bitrate,
            "-maxrate", "12M",
            "-bufsize", "24M",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-allow_sw", "1",
        ]
    # libx264 폴백
    preset = "medium"
    return [
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]


def encoder_name() -> str:
    args = encoder_args()
    return args[args.index("-c:v") + 1]


def check_environment() -> dict:
    out = {"ok": True, "items": []}
    try:
        p = subprocess.run([ffmpeg(), "-version"], capture_output=True, text=True, timeout=30)
        out["items"].append({"name": "ffmpeg", "ok": p.returncode == 0,
                             "detail": (p.stdout or "").splitlines()[0] if p.stdout else ""})
    except Exception as e:
        out["ok"] = False
        out["items"].append({"name": "ffmpeg", "ok": False, "detail": str(e)})
    try:
        info = probe(Path(__file__))
        out["items"].append({"name": "ffprobe", "ok": bool(info) or ffprobe() is not None,
                             "detail": ffprobe() or "ffmpeg 출력 파싱으로 대체"})
    except Exception as e:
        out["ok"] = False
        out["items"].append({"name": "ffprobe", "ok": False, "detail": str(e)})
    out["items"].append({"name": "하드웨어 가속(h264_videotoolbox)",
                         "ok": _test_videotoolbox(),
                         "detail": "하드웨어 인코더 사용" if _test_videotoolbox() else "libx264 폴백"})
    return out

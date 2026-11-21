"""오디오 설계: ASMR 사운드 자동 믹싱 · 3단 감정 음악 · -12.5 LUFS 라우드니스 · 무음 0 · 루프 크로스페이드."""
from __future__ import annotations

import json
import re
import wave
from pathlib import Path

import numpy as np

from . import ffmpeg_tools, settings, sfx

SR = 48000


# ------------------------------------------------------------------ WAV 입출력
def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    if ch > 1:
        data = data.reshape(-1, ch)
    else:
        data = data.reshape(-1, 1)
    if sr != SR:
        # 선형 보간 리샘플
        idx = np.linspace(0, len(data) - 1, int(len(data) * SR / sr))
        data = np.stack([np.interp(idx, np.arange(len(data)), data[:, c]) for c in range(data.shape[1])], axis=1)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    if data.shape[1] > 2:
        data = data[:, :2]
    return data.astype(np.float32)


def write_wav(path: Path, data: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(data, -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def to_wav_from_any(src: Path, dst: Path, filters: str | None = None) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    if filters:
        cmd += ["-af", filters]
    cmd += ["-ar", str(SR), "-ac", "2", str(dst)]
    rc, out = ffmpeg_tools.run(cmd, timeout=300)
    if rc != 0:
        raise RuntimeError(f"오디오 변환 실패: {out[-400:]}")
    return dst


# ------------------------------------------------------------------ 믹싱 유틸
def _db(x: float) -> float:
    return 10 ** (x / 20.0)


def _place(track: np.ndarray, clip: np.ndarray, at: float, gain_db: float = 0.0, fade_in: float = 0.0,
           fade_out: float = 0.0) -> None:
    start = int(at * SR)
    if start >= len(track):
        return
    clip = clip.copy()
    if fade_in > 0:
        n = min(int(fade_in * SR), len(clip))
        clip[:n] *= np.linspace(0, 1, n)[:, None]
    if fade_out > 0:
        n = min(int(fade_out * SR), len(clip))
        clip[-n:] *= np.linspace(1, 0, n)[:, None]
    end = start + len(clip)
    if end > len(track):
        clip = clip[: len(track) - start]
        end = len(track)
    track[start:end] += clip * _db(gain_db)


def _loop_fill(track: np.ndarray, sample: np.ndarray, gain_db: float) -> None:
    """배경 앰비언스를 트랙 전체에 끊김 없이 깐다(무음 0 보장)."""
    n = len(sample)
    if n == 0:
        return
    pos = 0
    idx = 0
    while pos < len(track):
        seg = sample if idx == 0 else np.roll(sample, idx % n, axis=0)
        end = min(pos + n, len(track))
        track[pos:end] += seg[: end - pos] * _db(gain_db)
        pos = end
        idx += 1


def _limit(x: np.ndarray, ceiling_db: float = -1.0) -> np.ndarray:
    """부드러운 소프트 리미터(피크만 눌러 왜곡을 줄인다)."""
    c = _db(ceiling_db)
    a = np.abs(x)
    over = a > c * 0.7
    if np.any(over):
        x = x.copy()
        head = c * 0.7
        x[over] = np.sign(x[over]) * (head + (c - head) * np.tanh((a[over] - head) / (c - head)))
    return x


def cut_times(cuts: list[dict]) -> list[float]:
    t = 0.0
    out = []
    for c in cuts:
        if not c.get("enabled", True):
            continue
        out.append(round(t, 3))
        t += c["dur"]
    return out


def total_duration(cuts: list[dict]) -> float:
    return round(sum(c["dur"] for c in cuts if c.get("enabled", True)), 3)


# ------------------------------------------------------------------ ASMR 모드
ASMR_BEDS = ["sizzle_grill", "sizzle_fry", "pot_boil"]


def build_asmr(project: dict, out_wav: Path, log=None) -> Path:
    cuts = [c for c in project["cuts"] if c.get("enabled", True)]
    dur = total_duration(cuts)
    track = np.zeros((int(dur * SR) + SR // 2, 2), dtype=np.float32)
    cfg = settings.load_settings()
    r = cfg["render"]
    opts = project.get("options") or {}
    flash_on = opts.get("flash", cfg["content"].get("hook_flash", True))
    whoosh_on = opts.get("whoosh", cfg["content"].get("cut_whoosh", True))

    bed_name = (project.get("audio", {}) or {}).get("bed") or "sizzle_grill"
    if bed_name not in sfx.LIBRARY:
        bed_name = "sizzle_grill"
    _loop_fill(track, read_wav(sfx.path_for(bed_name)), -20.0)
    _loop_fill(track, read_wav(sfx.path_for("pot_boil")), -27.0)

    if flash_on:
        _place(track, read_wav(sfx.path_for("flash_impact")), 0.0, -6.0, fade_out=0.2)

    t = 0.0
    for i, c in enumerate(cuts):
        if i > 0 and whoosh_on:
            _place(track, read_wav(sfx.path_for("whoosh_cut")), max(0.0, t - 0.10), -15.0)
        names = c.get("sfx") or []
        cut_gain = float(c.get("sfx_gain", 0.0) or 0.0)
        for j, name in enumerate(names):
            try:
                p = sfx.path_for(name)
            except Exception:
                continue
            off = 0.03 + j * 0.18
            _place(track, read_wav(p), t + off, -3.0 + cut_gain, fade_out=0.05)
        t += c["dur"]

    # 마지막 컷: 루프 리셋 삐빅 + 하트
    _place(track, read_wav(sfx.path_for("heart_pop")), max(0.0, dur - 1.35), -9.0)
    _place(track, read_wav(sfx.path_for("beep_scale")), max(0.0, dur - 0.75), -12.0)

    track = track[: int(dur * SR)]
    # 아주 미세한 공기감(완전 무음 방지)
    rng = np.random.default_rng(7)
    track += (rng.standard_normal(track.shape).astype(np.float32) * 0.0006)
    track = _limit(track, -1.0)
    if log:
        log(f"ASMR 오디오 구성: {dur:.2f}초 / 배경 {bed_name} / 컷 사운드 {sum(len(c.get('sfx') or []) for c in cuts)}개")
    return write_wav(out_wav, track)


# ------------------------------------------------------------------ 드라마 모드
def build_drama(project: dict, dialogue: list[dict], out_wav: Path, log=None) -> Path:
    cuts = [c for c in project["cuts"] if c.get("enabled", True)]
    dur = total_duration(cuts)
    voice = np.zeros((int(dur * SR) + SR, 2), dtype=np.float32)
    music = np.zeros_like(voice)
    fx = np.zeros_like(voice)
    cfg = settings.load_settings()

    bed = (project.get("audio", {}) or {}).get("music") or "music_bed_warm"
    if bed not in sfx.LIBRARY:
        bed = "music_bed_warm"
    _loop_fill(music, read_wav(sfx.path_for(bed)), -21.0)
    # 무음 0 보장: 아주 옅은 룸톤을 깔아 둔다.
    _loop_fill(music, read_wav(sfx.path_for("sizzle_grill")), -38.0)

    t = 0.0
    for c, d in zip(project["cuts"], dialogue):
        if not c.get("enabled", True):
            continue
        if d.get("wav") and Path(d["wav"]).exists():
            v = read_wav(Path(d["wav"]))
            # 대사가 컷보다 길면 살짝 당겨 넣는다
            max_len = int((c["dur"] - 0.05) * SR)
            if len(v) > max_len > 0:
                v = v[:max_len]
            _place(voice, v, t + 0.04, 0.0, fade_in=0.01, fade_out=0.03)
        for name in (c.get("sfx") or [])[:2]:
            try:
                _place(fx, read_wav(sfx.path_for(name)), t + 0.02, -14.0 + float(c.get("sfx_gain", 0.0) or 0.0))
            except Exception:
                pass
        t += c["dur"]

    # 감정 스웰: 후반으로 갈수록 음악을 조금씩 올린다
    n = len(music)
    ramp = np.linspace(_db(-21), _db(-18), n)[:, None]
    music = music / max(_db(-21), 1e-6) * ramp

    # 보이스 덕킹(말할 때 음악 -3.5dB)
    env = np.abs(voice).mean(axis=1)
    win = int(0.12 * SR)
    if win > 1:
        kernel = np.ones(win) / win
        env = np.convolve(env, kernel, mode="same")
    gate = (env > 0.004).astype(np.float32)
    duck = 1.0 - 0.35 * np.convolve(gate, np.ones(win) / win, mode="same")[: len(music)]
    music *= duck[:, None]

    _place(fx, read_wav(sfx.path_for("heart_pop")), max(0.0, dur - 1.2), -11.0)
    mix = voice * 1.0 + music + fx
    mix = mix[: int(dur * SR)]
    rng = np.random.default_rng(11)
    mix += (rng.standard_normal(mix.shape).astype(np.float32) * 0.0012)
    mix = _limit(mix, -1.0)
    if log:
        log(f"드라마 오디오: {dur:.2f}초 / 대사 {sum(1 for d in dialogue if d.get('wav'))}개 / BGM {bed}")
    return write_wav(out_wav, mix)


# ------------------------------------------------------------------ 루프·라우드니스
def loop_crossfade(src: Path, dst: Path, fade: float = 0.25, log=None) -> Path:
    """끝 → 처음 0.25초 크로스디졸브(무한루프). 영상 루프와 동일한 방식."""
    x = read_wav(src)
    f = int(fade * SR)
    if f > 8 and len(x) > f * 2:
        ramp = np.linspace(0, 1, f)[:, None]
        head = x[:f].copy()
        x[-f:] = x[-f:] * (1 - ramp) + head * ramp
    if log:
        log(f"오디오 루프 크로스페이드 {fade}초 적용")
    return write_wav(dst, x)


def measure_lufs(path: Path) -> dict:
    cmd = [
        ffmpeg_tools.ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
        "-af", "loudnorm=I=-12.5:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-",
    ]
    rc, out = ffmpeg_tools.run(cmd, timeout=600)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", out, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


def loudnorm(src: Path, dst: Path, lufs: float = -12.5, tp: float = -1.5, log=None) -> dict:
    """목표 LUFS 에 정확히 맞추는 마스터링(2-pass loudnorm + 게인·리미터 반복 수렴).

    피크가 큰 ASMR 임팩트 때문에 loudnorm 한 번으로는 목표에 못 미치는 경우가 있어,
    실제 측정값을 보며 게인과 리미터를 최대 6회 반복해 오차 0.3 LU 이내로 수렴시킨다.
    """
    import shutil
    import tempfile

    tmpdir = Path(tempfile.mkdtemp(prefix="tp_master_"))
    work = tmpdir / "pass0.wav"

    m0 = measure_lufs(src)
    af0 = f"loudnorm=I={lufs}:TP={tp}:LRA=11"
    if m0.get("input_i") not in (None, "-inf", "-70.00"):
        af0 = (f"loudnorm=I={lufs}:TP={tp}:LRA=11:"
               f"measured_I={m0['input_i']}:measured_TP={m0['input_tp']}:"
               f"measured_LRA={m0['input_lra']}:measured_thresh={m0['input_thresh']}:"
               f"offset={m0.get('target_offset', '0.0')}")
    rc, out = ffmpeg_tools.run(
        [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(src), "-af", af0, "-ar", str(SR), "-ac", "2", str(work)], timeout=600)
    if rc != 0:
        raise RuntimeError(f"라우드니스 정규화 실패: {out[-400:]}")

    limit = 10 ** ((tp - 1.6) / 20.0)   # true peak -1.5 dB 를 지키기 위한 여유
    history = []
    for i in range(6):
        m = measure_lufs(work)
        try:
            cur_i = float(m.get("input_i", -70))
        except Exception:
            cur_i = -70.0
        try:
            cur_tp = float(m.get("input_tp", -99))
        except Exception:
            cur_tp = -99.0
        diff = lufs - cur_i
        history.append({"pass": i, "I": cur_i, "TP": cur_tp, "diff": round(diff, 2)})
        if abs(diff) <= 0.3 and cur_tp <= tp + 0.5:
            break
        if diff > 0:
            af = (f"volume={min(diff, 12):.2f}dB,"
                  f"alimiter=limit={limit:.4f}:level=0:attack=2:release=70")
        else:
            af = f"volume={max(diff, -12):.2f}dB"
        nxt = tmpdir / f"pass{i+1}.wav"
        rc, out = ffmpeg_tools.run(
            [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(work), "-af", af, "-ar", str(SR), "-ac", "2", str(nxt)], timeout=600)
        if rc != 0:
            break
        work = nxt

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(work, dst)
    after = measure_lufs(dst)
    if log:
        log(f"라우드니스 {m0.get('input_i', '?')} → {after.get('input_i', '?')} LUFS "
            f"(목표 {lufs}, true peak {after.get('input_tp', '?')} dB)")
    shutil.rmtree(tmpdir, ignore_errors=True)
    return {"before": m0, "after": after, "passes": history}


def adjust_gain(src: Path, dst: Path, diff_db: float, tp: float = -1.5) -> Path:
    """AAC 변환 후 생기는 미세한 라우드니스 차이를 마지막에 보정한다."""
    limit = 10 ** ((tp - 1.6) / 20.0)
    if diff_db >= 0:
        af = f"volume={min(diff_db, 6):.2f}dB,alimiter=limit={limit:.4f}:level=0:attack=2:release=70"
    else:
        af = f"volume={max(diff_db, -6):.2f}dB"
    rc, out = ffmpeg_tools.run(
        [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(src), "-af", af, "-ar", str(SR), "-ac", "2", str(dst)], timeout=600)
    if rc != 0:
        raise RuntimeError(f"라우드니스 보정 실패: {out[-300:]}")
    return dst


def silence_report(path: Path, min_len: float = 0.15) -> dict:
    cmd = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
           "-af", f"silencedetect=noise=-50dB:d={min_len}", "-f", "null", "-"]
    _rc, out = ffmpeg_tools.run(cmd, timeout=600)
    gaps = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", out)]
    return {
        "silences": gaps,
        "count": len(gaps),
        "longest": max(gaps) if gaps else 0.0,
        "total": round(sum(gaps), 3),
        "ok": (not gaps) or max(gaps) <= min_len,
    }

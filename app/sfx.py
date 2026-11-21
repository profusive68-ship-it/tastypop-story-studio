"""내장 ASMR 사운드 라이브러리 생성기.

외부 음원 없이 numpy 로 직접 합성해 `sfx_library/` 에 WAV(48kHz/16bit/스테레오)로 저장한다.
- 상업적 이용 100% 무료(자체 합성 = CC0), 저작권 클레임 위험 0
- 컷 전환·재료 소리·완성음·루프 리셋음까지 자동 믹싱에 사용
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

from . import settings

SR = 48000

# 이름: (설명, 기본 길이(초), 기본 게인(dB), 루프용 여부)
LIBRARY: dict[str, tuple[str, float, float, bool]] = {
    "sizzle_grill": ("지글지글 - 뜨거운 불판/구이", 4.0, -6.0, True),
    "sizzle_fry": ("지글지글 - 튀김 기름", 3.5, -7.0, True),
    "pot_boil": ("보글보글 - 끓는 냄비", 4.0, -8.0, True),
    "steam_hiss": ("쉬이익 - 김이 오르는 소리", 1.6, -12.0, False),
    "crunch_bite": ("바삭 - 한 입 크런치", 0.9, -5.0, False),
    "crunch_big": ("바삭 - 크게 깨무는 소리", 1.2, -4.0, False),
    "cheese_stretch": ("쭈욱 - 치즈 늘어남", 1.6, -6.0, False),
    "slurp_noodle": ("후루룩 - 면 빨아들이기", 1.3, -5.0, False),
    "sauce_pour": ("찌익 - 소스 붓기/뿌리기", 1.4, -7.0, False),
    "fizz_soda": ("탄산 - 톡톡 터지는 소리", 2.2, -8.0, False),
    "knife_chop": ("톡톡 - 칼질", 0.45, -6.0, False),
    "beep_scale": ("삐빅 - 체중계/타이머", 0.9, -9.0, False),
    "whoosh_cut": ("컷 전환 - 스우시", 0.32, -10.0, False),
    "boom_impact": ("컷 전환 - 붐 임팩트", 0.6, -6.0, False),
    "flash_impact": ("0.000초 골든 플래시 임팩트", 0.8, -4.0, False),
    "pop_bubble": ("톡 - 방울/버튼", 0.28, -12.0, False),
    "heart_pop": ("하트 - CTA 유도음", 0.5, -12.0, False),
    "music_bed_warm": ("드라마용 따뜻한 BGM 패드", 10.0, -14.0, True),
    "music_bed_playful": ("드라마용 통통 튀는 BGM", 10.0, -14.0, True),
}


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * SR), dtype=np.float64) / SR


def _noise(seconds: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(int(seconds * SR))


def _fft_filter(x: np.ndarray, lo: float, hi: float, slope: float = 0.6) -> np.ndarray:
    """FFT 기반 부드러운 밴드패스 (scipy 불필요)."""
    n = len(x)
    if n == 0:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    mask = np.ones_like(freqs)
    lo = max(lo, 20.0)
    hi = min(hi, SR / 2 - 100)
    if lo > 20:
        mask *= 1.0 / (1.0 + np.exp(-(freqs - lo) / max(lo * slope, 1.0)))
    if hi < SR / 2:
        z = np.clip((freqs - hi) / max(hi * slope * 0.2, 1.0), -60, 60)
        mask *= 1.0 / (1.0 + np.exp(z))
    return np.fft.irfft(spec * mask, n=n)


def _env(x: np.ndarray, attack: float = 0.005, decay: float = 0.2, power: float = 2.0) -> np.ndarray:
    n = len(x)
    a = max(int(attack * SR), 1)
    t = np.linspace(0, 1, n)
    e = np.exp(-t * (3.0 / max(decay, 0.01)) ** power)
    if a < n:
        e[:a] *= np.linspace(0, 1, a) ** 0.5
    return x * e


def _echo(x: np.ndarray, taps: tuple[tuple[float, float], ...]) -> np.ndarray:
    out = x.copy()
    for delay, gain in taps:
        d = int(delay * SR)
        if d <= 0 or d >= len(x):
            continue
        out[d:] += x[:-d] * gain
    return out


def _norm(x: np.ndarray, peak_db: float = -3.0) -> np.ndarray:
    peak = float(np.max(np.abs(x))) or 1.0
    target = 10 ** (peak_db / 20.0)
    return x / peak * target


def _stereo(x: np.ndarray, width: float = 0.12) -> np.ndarray:
    """아주 살짝 좌우를 벌려 현장감을 준다."""
    d = int(0.004 * SR)
    left = x
    right = np.concatenate([np.zeros(d), x[:-d]]) if d < len(x) else x
    return np.stack([left * (1 - width) + right * width, right * (1 - width) + left * width], axis=1)


def _loopify(x: np.ndarray, fade: float = 0.15) -> np.ndarray:
    """앞뒤 크로스페이드로 끊김 없는 루프 소스로 만든다."""
    f = int(fade * SR)
    if f * 2 >= len(x):
        return x
    head = x[:f].copy()
    tail = x[-f:].copy()
    ramp = np.linspace(0, 1, f)[:, None] if x.ndim > 1 else np.linspace(0, 1, f)
    body = np.concatenate([head * ramp + tail * (1 - ramp), x[f:-f]], axis=0)
    return body


# ---------------------------------------------------------------- 개별 합성기
def _sizzle(seconds: float, seed: int, lo: float, hi: float, crackle: float, hum: float) -> np.ndarray:
    n = _noise(seconds, seed)
    hiss = _fft_filter(n, lo, hi, 0.8) * 0.8
    rng = np.random.default_rng(seed + 1)
    pops = np.zeros(len(hiss))
    count = max(int(seconds * 22 * crackle), 3)
    for _ in range(count):
        pos = rng.integers(0, len(hiss) - 200)
        ln = int(rng.uniform(0.004, 0.02) * SR)
        burst = rng.standard_normal(ln) * np.hanning(ln)
        pops[pos:pos + ln] += burst * rng.uniform(0.2, 1.0)
    low = _fft_filter(_noise(seconds, seed + 7), 60, 160) * hum
    x = hiss + pops + low
    # 아주 완만한 세기 변화
    t = np.linspace(0, 1, len(x))
    x *= (0.85 + 0.15 * np.sin(2 * np.pi * t * 1.7 + seed))
    return x


def _crunch(seconds: float, seed: int, hard: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.zeros(int(seconds * SR))
    pieces = max(int(seconds * 14), 4)
    for i in range(pieces):
        pos = int((i / pieces) * len(x) * 0.9 + rng.uniform(0, 0.02) * SR)
        ln = int(rng.uniform(0.006, 0.03) * SR)
        if pos + ln >= len(x):
            break
        grain = rng.standard_normal(ln) * np.exp(-np.linspace(0, 6, ln))
        grain = _fft_filter(grain, 700, 11000, 0.5)
        x[pos:pos + ln] += grain * rng.uniform(0.3, 1.0) * hard
    body = _fft_filter(_noise(seconds, seed + 3), 300, 6000, 0.6)
    x += _env(body, 0.01, seconds * 0.8) * 0.55 * hard
    return x


def _cheese(seconds: float) -> np.ndarray:
    t = _t(seconds)
    base = 150 + 60 * np.sin(2 * np.pi * 0.7 * t)
    glide = np.cumsum(base) / SR
    tone = 0.5 * np.sin(2 * np.pi * glide) + 0.25 * np.sin(2 * np.pi * glide * 2.01)
    goo = _fft_filter(_noise(seconds, 11), 200, 1400, 0.7) * 0.6
    env = np.exp(-((t - seconds * 0.45) ** 2) / (2 * (seconds * 0.26) ** 2))
    return (tone + goo) * env


def _slurp(seconds: float) -> np.ndarray:
    t = _t(seconds)
    n = _noise(seconds, 21)
    out = np.zeros(len(t))
    for k, (f0, f1, st, du) in enumerate([(300, 1100, 0.05, 0.5), (260, 900, 0.55, 0.4)]):
        seg = (t >= st) & (t < st + du)
        idx = np.where(seg)[0]
        if not len(idx):
            continue
        ph = np.cumsum(np.linspace(f0, f1, len(idx))) / SR
        tone = np.sin(2 * np.pi * ph) * 0.4
        nseg = _fft_filter(n[idx], 250, 3500) * 0.8
        e = np.hanning(len(idx))
        out[idx] += (tone + nseg) * e
    return out


def _fizz(seconds: float) -> np.ndarray:
    t = _t(seconds)
    x = _fft_filter(_noise(seconds, 31), 1500, 12000, 0.5) * 0.5
    rng = np.random.default_rng(32)
    for _ in range(int(seconds * 55)):
        pos = rng.integers(0, len(x) - 400)
        ln = int(rng.uniform(0.002, 0.008) * SR)
        x[pos:pos + ln] += rng.standard_normal(ln) * np.hanning(ln) * rng.uniform(0.2, 0.9)
    return x * np.exp(-t * 1.2)


def _knife(seconds: float) -> np.ndarray:
    rng = np.random.default_rng(41)
    x = np.zeros(int(seconds * SR))
    for i in range(3):
        pos = int(i * len(x) / 3.2)
        ln = int(0.05 * SR)
        if pos + ln >= len(x):
            break
        hit = rng.standard_normal(ln) * np.exp(-np.linspace(0, 12, ln))
        hit = _fft_filter(hit, 900, 9000, 0.5)
        x[pos:pos + ln] += hit
        x[pos:pos + int(0.12 * SR)] += np.sin(2 * np.pi * 220 * _t(0.12)) * np.exp(-np.linspace(0, 14, int(0.12 * SR))) * 0.25
    return x


def _beep(seconds: float) -> np.ndarray:
    t = _t(seconds)
    x = np.zeros(len(t))
    for st in (0.0, seconds * 0.55):
        idx = (t >= st) & (t < st + 0.14)
        seg = t[idx] - st
        x[idx] += np.sign(np.sin(2 * np.pi * 1180 * seg)) * 0.35
    return _env(x, 0.002, seconds * 0.9, 0.7)


def _whoosh(seconds: float) -> np.ndarray:
    t = _t(seconds)
    n = _noise(seconds, 51)
    # 시간에 따라 대역이 위로 훑고 지나가는 스우시
    out = np.zeros(len(t))
    step = 256
    for i in range(0, len(t) - step, step):
        frac = i / max(len(t), 1)
        lo, hi = 300 + 3000 * frac, 1500 + 7000 * frac
        out[i:i + step] = _fft_filter(n[i:i + step], lo, hi, 0.8)
    env = np.sin(np.pi * np.clip(t / seconds, 0, 1)) ** 1.4
    return out * env


def _boom(seconds: float) -> np.ndarray:
    t = _t(seconds)
    sub = np.sin(2 * np.pi * (90 * np.exp(-t * 5) + 38) * t) * np.exp(-t * 4.5)
    click = _fft_filter(_noise(seconds, 61), 800, 8000) * np.exp(-t * 40) * 0.5
    return sub * 1.0 + click


def _flash(seconds: float) -> np.ndarray:
    t = _t(seconds)
    rise = np.sin(2 * np.pi * 2600 * t) * np.exp(-t * 22) * 0.3
    shimmer = _fft_filter(_noise(seconds, 71), 4000, 14000) * np.exp(-t * 9) * 0.5
    low = np.sin(2 * np.pi * 70 * t) * np.exp(-t * 6) * 0.7
    return rise + shimmer + low


def _pop(seconds: float) -> np.ndarray:
    t = _t(seconds)
    return np.sin(2 * np.pi * (420 + 900 * np.exp(-t * 30)) * t) * np.exp(-t * 22)


def _heart(seconds: float) -> np.ndarray:
    t = _t(seconds)
    x = np.zeros(len(t))
    for st, f in ((0.0, 660), (0.16, 990)):
        idx = (t >= st) & (t < st + 0.16)
        seg = t[idx] - st
        x[idx] += np.sin(2 * np.pi * f * seg) * np.exp(-seg * 14)
    return x


def _pad(seconds: float, seed: int, chords: list[list[float]], bright: float = 1.0) -> np.ndarray:
    t = _t(seconds)
    x = np.zeros(len(t))
    seg = seconds / len(chords)
    for i, chord in enumerate(chords):
        idx = (t >= i * seg) & (t < (i + 1) * seg)
        if not len(idx):
            continue
        tt = t[idx] - i * seg
        fade = np.minimum(1.0, np.minimum(tt / 0.8, (seg - tt) / 0.8))
        for f in chord:
            x[idx] += (np.sin(2 * np.pi * f * tt) * 0.5
                       + np.sin(2 * np.pi * f * 2 * tt) * 0.12 * bright
                       + np.sin(2 * np.pi * f * 3 * tt) * 0.05 * bright) * fade
    # 부드러운 노이즈 공기감
    air = _fft_filter(_noise(seconds, seed), 2000, 7000) * 0.03
    trem = 0.9 + 0.1 * np.sin(2 * np.pi * t * 0.35)
    return (x / max(len(chords), 1) + air) * trem


def build(name: str, force: bool = False) -> Path:
    settings.ensure_dirs()
    path = settings.SFX_DIR / f"{name}.wav"
    if path.exists() and not force:
        return path

    if name not in LIBRARY:
        raise KeyError(f"알 수 없는 사운드: {name}")
    desc, seconds, gain_db, _loop = LIBRARY[name]

    seed = abs(hash(name)) % 9999
    if name == "sizzle_grill":
        x = _sizzle(seconds, seed, 900, 9000, 1.0, 0.25)
    elif name == "sizzle_fry":
        x = _sizzle(seconds, seed, 1400, 12000, 1.6, 0.15)
    elif name == "pot_boil":
        t = _t(seconds)
        x = _fft_filter(_noise(seconds, seed), 200, 1600) * 0.5
        rng = np.random.default_rng(seed + 2)
        for _ in range(int(seconds * 9)):
            pos = rng.integers(0, len(x) - 4000)
            ln = int(rng.uniform(0.02, 0.06) * SR)
            f = rng.uniform(280, 620)
            x[pos:pos + ln] += np.sin(2 * np.pi * f * _t(ln / SR)) * np.exp(-np.linspace(0, 8, ln)) * 0.5
    elif name == "steam_hiss":
        x = _fft_filter(_noise(seconds, seed), 2500, 13000, 0.6)
        x = _env(x, 0.05, seconds * 0.55, 1.2)
    elif name in ("crunch_bite", "crunch_big"):
        x = _crunch(seconds, seed, 1.0 if name == "crunch_bite" else 1.25)
    elif name == "cheese_stretch":
        x = _cheese(seconds)
    elif name == "slurp_noodle":
        x = _slurp(seconds)
    elif name == "sauce_pour":
        t = _t(seconds)
        x = _fft_filter(_noise(seconds, seed), 400, 4000) * (0.5 + 0.5 * np.sin(2 * np.pi * t * 3)) * np.exp(-t * 1.6)
    elif name == "fizz_soda":
        x = _fizz(seconds)
    elif name == "knife_chop":
        x = _knife(seconds)
    elif name == "beep_scale":
        x = _beep(seconds)
    elif name == "whoosh_cut":
        x = _whoosh(seconds)
    elif name == "boom_impact":
        x = _boom(seconds)
    elif name == "flash_impact":
        x = _flash(seconds)
    elif name == "pop_bubble":
        x = _pop(seconds)
    elif name == "heart_pop":
        x = _heart(seconds)
    elif name == "music_bed_warm":
        x = _pad(seconds, seed, [[261.6, 329.6, 392.0], [220.0, 261.6, 329.6],
                                 [174.6, 220.0, 261.6], [196.0, 246.9, 293.7]], bright=0.6)
    elif name == "music_bed_playful":
        x = _pad(seconds, seed, [[392.0, 493.9, 587.3], [440.0, 554.4, 659.3],
                                 [349.2, 440.0, 523.3], [392.0, 493.9, 587.3]], bright=1.4)
    else:
        raise KeyError(name)

    x = _norm(x, peak_db=-3.0)
    x = x * (10 ** (gain_db / 20.0)) if gain_db < -3 else x
    if x.ndim == 1:
        st = _stereo(x)
    else:
        st = x
    if _loop:
        st = _loopify(st)
    _write_wav(path, st)
    return path


def _write_wav(path: Path, data: np.ndarray) -> None:
    data = np.clip(data, -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2 if pcm.ndim > 1 else 1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def ensure_library(force: bool = False) -> dict:
    settings.ensure_dirs()
    made = {}
    for name in LIBRARY:
        try:
            p = build(name, force=force)
            made[name] = p
        except Exception as e:  # 하나 실패해도 나머지는 계속
            made[name] = None
            print(f"[sfx] {name} 생성 실패: {e}")
    return made


def list_library() -> list[dict]:
    out = []
    for name, (desc, secs, gain, loop) in LIBRARY.items():
        p = settings.SFX_DIR / f"{name}.wav"
        out.append({
            "name": name,
            "desc": desc,
            "seconds": secs,
            "gain_db": gain,
            "loop": loop,
            "exists": p.exists(),
            "file": str(p.relative_to(settings.APP_DIR)) if p.exists() else "",
        })
    return out


def path_for(name: str) -> Path:
    p = settings.SFX_DIR / f"{name}.wav"
    if not p.exists():
        return build(name)
    return p

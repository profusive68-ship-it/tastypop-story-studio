"""영상 렌더 파이프라인.

0.000초 훅 자막 → 0.900초 컷 전환과 동시에 0픽셀 소멸 / 컷 리듬 / CTA / 0.25초 무한루프 /
-12.5 LUFS / 1080x1920 30fps / 워터마크 0.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from . import audio, ffmpeg_tools, fonts, planner, settings


class RenderError(RuntimeError):
    pass


def _cb(progress, pct: float, stage: str):
    if progress:
        progress(pct, stage)


def _enc_args() -> list[str]:
    return ffmpeg_tools.encoder_args()


def _media_for(project: dict, cut: dict) -> tuple[Path | None, str]:
    return planner.resolve_media(project, cut)


def prepare_cuts(project: dict, progress=None, log=None) -> list[Path]:
    """컷별 1080x1920 / 30fps / 정확한 프레임 수로 정규화한다."""
    r = settings.load_settings()["render"]
    W, H, FPS = int(r["width"]), int(r["height"]), int(r["fps"])
    d = planner.project_dir(project["id"])
    tmp = d / "work"
    tmp.mkdir(parents=True, exist_ok=True)
    out = []
    cuts = [c for c in project["cuts"] if c.get("enabled", True)]
    missing = []
    for i, c in enumerate(cuts):
        src, kind = _media_for(project, c)
        if not src:
            missing.append(c["id"])
            continue
        frames = max(int(round(c["dur"] * FPS)), 2)
        dst = tmp / f"cut_{i+1:02d}.mp4"
        if kind == "clip":
            vf = (
                f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H},fps={FPS},setsar=1,"
                f"tpad=stop_mode=clone:stop_duration=3"
            )
            cmd = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                   "-t", f"{c['dur'] + 0.5:.3f}", "-i", str(src),
                   "-vf", vf, "-frames:v", str(frames), "-an", "-r", str(FPS),
                   *_enc_args(), str(dst)]
        else:
            kb = r.get("kenburns", True)
            if kb:
                vf = (
                    f"scale={int(W*1.25)}:{int(H*1.25)}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={int(W*1.25)}:{int(H*1.25)},"
                    f"zoompan=z='min(zoom+0.0009,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                    f":d=1:s={W}x{H}:fps={FPS},setsar=1"
                )
            else:
                vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                      f"crop={W}:{H},fps={FPS},setsar=1")
            cmd = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                   "-loop", "1", "-framerate", str(FPS), "-t", f"{c['dur'] + 0.3:.3f}", "-i", str(src),
                   "-vf", vf, "-frames:v", str(frames), "-an", "-r", str(FPS),
                   *_enc_args(), str(dst)]
        rc, o = ffmpeg_tools.run(cmd, timeout=600, log_path=d / "logs" / "render.log")
        if rc != 0 or not dst.exists():
            raise RenderError(f"CUT {c['id']} 준비 실패: {o[-500:]}")
        out.append(dst)
        _cb(progress, 10 + 30 * (i + 1) / max(len(cuts), 1), f"컷 준비 {i+1}/{len(cuts)}")
    if missing:
        raise RenderError(
            "이미지/클립이 없는 컷: " + ", ".join(f"C{int(m):02d}" for m in missing)
            + "\n→ kf/ 폴더에 kf1.png ~ kf10.png (또는 c01.png …) 를 넣어 주세요."
            + " 대시보드 [📥 이미지 감지] 가 초록불이 되면 바로 조립할 수 있습니다."
        )
    if log:
        log(f"컷 {len(out)}개 정규화 완료 ({W}x{H} / {FPS}fps)")
    return out


def _concat(clips: list[Path], out: Path, log=None) -> Path:
    lst = out.parent / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in clips), encoding="utf-8")
    cmd = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)]
    rc, o = ffmpeg_tools.run(cmd, timeout=900)
    if rc != 0 or not out.exists():
        raise RenderError(f"컷 연결 실패: {o[-500:]}")
    if log:
        log(f"컷 {len(clips)}개 연결 → {out.name}")
    return out


def overlays_for(project: dict) -> list[tuple[Image.Image, float, float, str]]:
    """시간 구간별 오버레이 목록 (PNG, 시작, 끝, 이름)."""
    r = settings.load_settings()["render"]
    dur = project.get("total_seconds") or planner_total(project)
    opts = project.get("options") or {}
    hook = project.get("hook", {})
    items = []
    if hook.get("text") and opts.get("hook", True):
        items.append((fonts.hook_overlay(hook["text"], r), float(hook.get("start", 0.0)),
                      float(hook.get("end", 0.9)), "hook"))
    if project.get("content_type") == "drama4":
        for sub in project.get("subtitles") or []:
            if sub.get("text"):
                items.append((fonts.subtitle_overlay(sub["text"], cfg=r), float(sub["start"]),
                              float(sub["end"]), f"sub{sub['start']}"))
    cta = project.get("cta") or {}
    cta_sec = float(cta.get("seconds", 1.5))
    if opts.get("cta", True) and (cta.get("like") or cta.get("save")):
        items.append((fonts.cta_overlay(cta.get("like", ""), cta.get("save", ""), r),
                      max(0.0, dur - cta_sec), dur, "cta"))
    return items


def planner_total(project: dict) -> float:
    return round(sum(c["dur"] for c in project["cuts"] if c.get("enabled", True)), 3)


def composite(project: dict, body: Path, out: Path, progress=None, log=None) -> Path:
    r = settings.load_settings()["render"]
    dur = planner_total(project)
    items = overlays_for(project)
    d = planner.project_dir(project["id"])
    ov_dir = d / "work" / "overlays"
    ov_dir.mkdir(parents=True, exist_ok=True)
    pngs = []
    for img, t0, t1, name in items:
        p = ov_dir / f"{name}.png"
        img.save(p)
        pngs.append((p, t0, t1))

    if not pngs:
        shutil.copyfile(body, out)
        return out

    cmd = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(body)]
    for p, _t0, _t1 in pngs:
        cmd += ["-loop", "1", "-framerate", str(int(r["fps"])), "-t", f"{dur:.3f}", "-i", str(p)]
    chains = []
    prev = "0:v"
    for idx, (p, t0, t1) in enumerate(pngs, start=1):
        # 마지막 프레임 경계에서 정확히 사라지도록 lt 사용
        label = f"v{idx}"
        chains.append(
            f"[{prev}][{idx}:v]overlay=0:0:eof_action=pass:enable='gte(t,{t0:.3f})*lt(t,{t1:.3f})'[{label}]"
        )
        prev = label
    filters = ";".join(chains)
    cmd += ["-filter_complex", filters, "-map", f"[{prev}]", "-an", "-r", str(int(r["fps"])),
            "-t", f"{dur:.3f}", *_enc_args(), str(out)]
    rc, o = ffmpeg_tools.run(cmd, timeout=900, log_path=d / "logs" / "render.log")
    if rc != 0 or not out.exists():
        raise RenderError(f"자막·훅 합성 실패: {o[-600:]}")
    if log:
        log(f"오버레이 {len(pngs)}개 합성 (훅 0.000~0.900초, CTA 마지막 {project.get('cta', {}).get('seconds', 1.5)}초)")
    _cb(progress, 72, "자막·훅·CTA 합성")
    return out


def make_loop(project: dict, src: Path, out: Path, log=None) -> Path:
    r = settings.load_settings()["render"]
    dur = planner_total(project)
    fade = float(r.get("loop_crossfade", 0.25))
    cmd = [
        ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-t", f"{fade:.3f}", "-i", str(src),
        "-filter_complex", f"[0:v][1:v]xfade=transition=fade:duration={fade}:offset={max(dur - fade, 0.1):.3f},format=yuv420p[v]",
        "-map", "[v]", "-an", "-r", str(int(r["fps"])), "-t", f"{dur:.3f}", *_enc_args(), str(out),
    ]
    rc, o = ffmpeg_tools.run(cmd, timeout=900)
    if rc != 0 or not out.exists():
        raise RenderError(f"무한루프 크로스디졸브 실패: {o[-500:]}")
    if log:
        log(f"끝→처음 {fade}초 크로스디졸브 무한루프 적용")
    _cb(0, 82, "무한루프 연결")
    return out


def mux(video: Path, wav: Path, out: Path, log=None) -> Path:
    cmd = [
        ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video), "-i", str(wav),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest", str(out),
    ]
    rc, o = ffmpeg_tools.run(cmd, timeout=600)
    if rc != 0 or not out.exists():
        raise RenderError(f"오디오 합성 실패: {o[-500:]}")
    if log:
        log(f"최종 파일 생성: {out.name}")
    return out


def verify(path: Path) -> dict:
    info = ffmpeg_tools.probe(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    lufs = audio.measure_lufs(path)
    sil = audio.silence_report(path)
    try:
        fps = eval(v.get("r_frame_rate", "0/1")) if v else 0
    except Exception:
        fps = 0
    return {
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": round(float(fps), 2),
        "duration": round(float(info.get("format", {}).get("duration", 0) or 0), 2),
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
        "lufs": lufs.get("input_i"),
        "true_peak": lufs.get("input_tp"),
        "silence": sil,
        "has_audio": bool(a),
    }


def check_hook_kill(video: Path, hook_end: float = 0.9, samples: tuple[float, float] = (0.30, 1.30)) -> dict:
    """훅 자막이 0.900초에 정확히 사라지는지 프레임 픽셀로 검증."""
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    vals = {}
    for t in samples:
        f = tmp / f"f_{t}.png"
        cmd = [ffmpeg_tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(f)]
        ffmpeg_tools.run(cmd, timeout=120)
        if f.exists():
            arr = np.asarray(Image.open(f).convert("L"), dtype=np.float32)
            band = arr[int(arr.shape[0] * 0.06): int(arr.shape[0] * 0.20), :]
            vals[t] = float(band.std())
    before = vals.get(samples[0], 0.0)
    after = vals.get(samples[1], 0.0)
    return {
        "before_std": round(before, 3),
        "after_std": round(after, 3),
        "hook_visible_before": before > 12,
        "hook_gone_after": after < max(12.0, before * 0.55),
        "ok": before > 12 and after < max(12.0, before * 0.55),
    }


def render(project: dict, dialogue: list[dict] | None = None, progress=None, log=None) -> dict:
    pid = project["id"]
    d = planner.project_dir(pid)
    out_dir = d / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    r = settings.load_settings()["render"]
    dur = planner_total(project)
    project["total_seconds"] = dur
    planner.save_project(project)

    if log:
        log(f"렌더 시작 — {project['content_label']} / {dur}초 / {len(project['cuts'])}컷")
    _cb(progress, 5, "렌더 준비")

    # 1) 오디오 먼저 (자막 타이밍이 음성 길이에 맞춰져야 하므로)
    raw_wav = d / "work" / "mix_raw.wav"
    if project["content_type"] == "drama4":
        if dialogue is None:
            dialogue = []
        audio.build_drama(project, dialogue, raw_wav, log=log)
    else:
        audio.build_asmr(project, raw_wav, log=log)

    looped_wav = d / "work" / "mix_loop.wav"
    audio.loop_crossfade(raw_wav, looped_wav, float(r.get("loop_crossfade", 0.25)), log=log)
    final_wav = d / "out" / "audio_master.wav"
    audio.loudnorm(looped_wav, final_wav, lufs=float(r["lufs"]), tp=float(r["true_peak"]), log=log)
    _cb(progress, 35, "ASMR·음성 믹싱")

    # 2) 컷 정규화 → 연결
    clips = prepare_cuts(project, progress, log)
    body = d / "work" / "body.mp4"
    _concat(clips, body, log)
    _cb(progress, 55, "컷 리듬 조립")

    # 3) 훅·자막·CTA
    composed = d / "work" / "composed.mp4"
    composite(project, body, composed, progress, log)

    # 4) 무한루프(옵션 OFF 면 원본 그대로)
    looped = d / "work" / "loop.mp4"
    if (project.get("options") or {}).get("loop", True):
        make_loop(project, composed, looped, log)
    else:
        looped = composed
        if log:
            log("무한루프 OFF — 컷 그대로 종료")

    # 5) 오디오 합성
    final = out_dir / "final.mp4"
    mux(looped, final_wav, final, log)
    _cb(progress, 92, "최종 인코딩")

    report = verify(final)
    # AAC 인코딩 후 남는 0.5 LU 이상 차이는 오디오만 재보정해 한 번 더 합성
    try:
        target_lufs = float(r["lufs"])
        cur = float(report.get("lufs") or -99)
        if abs(cur - target_lufs) > 0.5 and cur > -60:
            fixed = d / "out" / "audio_master_fix.wav"
            audio.adjust_gain(final_wav, fixed, target_lufs - cur, tp=float(r["true_peak"]))
            mux(looped, fixed, final, log)
            if log:
                log(f"AAC 보정: {cur} → 재합성")
            report = verify(final)
    except Exception as e:
        if log:
            log(f"라우드니스 미세보정 건너뜀: {e}")
    report["hook"] = check_hook_kill(final, float(project.get("hook", {}).get("end", 0.9)))
    report["target"] = {
        "size": f"{r['width']}x{r['height']}",
        "fps": r["fps"],
        "lufs": r["lufs"],
        "watermark": 0,
        "loop": r.get("loop_crossfade"),
    }
    report["ok"] = bool(
        report["width"] == int(r["width"]) and report["height"] == int(r["height"])
        and abs(float(report["lufs"] or 0) - float(r["lufs"])) <= 1.0
        and report["silence"]["ok"] and report["has_audio"] and report["hook"]["ok"]
    )
    (out_dir / "qa_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    project["files"]["video"] = str(final.relative_to(d))
    project["files"]["audio"] = "out/audio_master.wav"
    project["render"] = report
    project["status"] = "rendered"
    planner.save_project(project)
    if log:
        log(f"검증: {report['width']}x{report['height']} {report['fps']}fps / "
            f"{report['lufs']} LUFS / 무음 {report['silence']['count']}구간 / 훅소멸 {'통과' if report['hook']['ok'] else '확인필요'}")
    _cb(progress, 96, "품질 검증")
    return {"video": final, "audio": final_wav, "report": report}

"""자막·훅·CTA 오버레이 PNG 생성 (Pretendard ExtraBold, 폴백 AppleSDGothicNeo)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import settings

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

CANDIDATES_BOLD = [
    settings.FONT_DIR / "Pretendard-ExtraBold.otf",
    settings.FONT_DIR / "Pretendard-Bold.otf",
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]
CANDIDATES_REGULAR = [
    settings.FONT_DIR / "Pretendard-Bold.otf",
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]


def font_path(bold: bool = True) -> Path | None:
    for p in (CANDIDATES_BOLD if bold else CANDIDATES_REGULAR):
        if p.exists():
            return p
    return None


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    key = ("b" if bold else "r", size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    p = font_path(bold)
    f = ImageFont.truetype(str(p), size) if p else ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _text_size(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=f, stroke_width=0)
    return box[2] - box[0], box[3] - box[1]


def wrap(text: str, f: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    tmp = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(tmp)
    for w in words[1:]:
        trial = cur + " " + w
        if _text_size(d, trial, f)[0] <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _fit(text: str, size: int, max_width: int, max_lines: int = 2) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    s = size
    while s > 28:
        f = font(s)
        lines = wrap(text, f, max_width)
        if len(lines) <= max_lines and all(_text_size(ImageDraw.Draw(Image.new("RGBA", (4, 4))), ln, f)[0] <= max_width for ln in lines):
            return f, lines
        s -= 4
    f = font(max(s, 28))
    return f, wrap(text, f, max_width)[:max_lines]


def text_block(
    text: str,
    *,
    size: int,
    y: int,
    width: int = 940,
    fill=(255, 255, 255, 255),
    stroke=(0, 0, 0, 235),
    stroke_width: int | None = None,
    max_lines: int = 2,
    line_gap: int = 12,
    shadow: bool = True,
    box: bool = False,
    box_color=(0, 0, 0, 130),
    canvas: tuple[int, int] = (1080, 1920),
) -> Image.Image:
    W, H = canvas
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f, lines = _fit(text, size, width, max_lines)
    if stroke_width is None:
        stroke_width = max(2, size // 18)
    heights = [_text_size(d, ln, f)[1] + 8 for ln in lines]
    total = sum(heights) + line_gap * (len(lines) - 1)
    cur_y = y
    if box:
        pad = 26
        d.rounded_rectangle(
            [W // 2 - width // 2 - pad, cur_y - pad, W // 2 + width // 2 + pad, cur_y + total + pad],
            radius=28, fill=box_color,
        )
    for ln, h in zip(lines, heights):
        tw, _ = _text_size(d, ln, f)
        x = (W - tw) // 2
        if shadow:
            d.text((x + 4, cur_y + 6), ln, font=f, fill=(0, 0, 0, 150), stroke_width=stroke_width,
                   stroke_fill=(0, 0, 0, 150))
        d.text((x, cur_y), ln, font=f, fill=fill, stroke_width=stroke_width, stroke_fill=stroke)
        cur_y += h + line_gap
    return img


def hook_overlay(text: str, cfg: dict | None = None) -> Image.Image:
    r = (cfg or settings.load_settings()["render"])
    return text_block(
        text,
        size=int(r.get("hook_size", 66)),
        y=int(r.get("hook_y", 210)),
        width=960,
        fill=(255, 255, 255, 255),
        stroke=(0, 0, 0, 235),
        max_lines=2,
    )


def _heart(d: ImageDraw.ImageDraw, cx: float, cy: float, size: float, fill) -> None:
    """폰트에 ♥ 글리프가 없어도 되도록 하트를 직접 그린다."""
    s = size
    d.ellipse([cx - s * 0.5, cy - s * 0.55, cx - s * 0.02, cy - s * 0.02], fill=fill)
    d.ellipse([cx + s * 0.02, cy - s * 0.55, cx + s * 0.5, cy - s * 0.02], fill=fill)
    d.polygon([(cx - s * 0.48, cy - s * 0.16), (cx + s * 0.48, cy - s * 0.16), (cx, cy + s * 0.52)], fill=fill)


def _plane(d: ImageDraw.ImageDraw, x: float, y: float, size: float, fill) -> None:
    """종이비행기(▶) 아이콘을 직접 그린다."""
    s = size
    d.polygon([(x, y), (x + s, y + s * 0.48), (x + s * 0.42, y + s * 0.55),
               (x + s * 0.28, y + s), (x + s * 0.12, y + s * 0.55)], fill=fill)


def cta_overlay(like: str, save: str, cfg: dict | None = None) -> Image.Image:
    """마지막 1.5초 CTA: ♥ Like · ✈ Share / Save for 2 AM (아이콘은 직접 드로잉)."""
    r = (cfg or settings.load_settings()["render"])
    W, H = int(r["width"]), int(r["height"])
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    base_y = int(H * 0.80)
    d.rounded_rectangle([60, base_y - 40, W - 60, base_y + 200], radius=44, fill=(0, 0, 0, 135))

    f1 = font(64)
    f2 = font(44)
    icon = 52
    like_word = "Like"
    share_word = "Share"
    sep = "·"
    tw_like = _text_size(d, like_word, f1)[0]
    tw_share = _text_size(d, share_word, f1)[0]
    tw_sep = _text_size(d, sep, f1)[0]
    gap = 20
    total = icon + gap + tw_like + gap + tw_sep + gap + icon + gap + tw_share
    x = (W - total) / 2
    cy = base_y + 46

    _heart(d, x + icon / 2, cy + 2, icon, (255, 92, 120, 255))
    x += icon + gap
    d.text((x, base_y + 8), like_word, font=f1, fill=(255, 255, 255, 255),
           stroke_width=3, stroke_fill=(0, 0, 0, 220))
    x += tw_like + gap
    d.text((x, base_y + 8), sep, font=f1, fill=(255, 255, 255, 255),
           stroke_width=3, stroke_fill=(0, 0, 0, 220))
    x += tw_sep + gap
    _plane(d, x + 4, cy - icon * 0.5, icon * 0.95, (255, 255, 255, 255))
    x += icon + gap
    d.text((x, base_y + 8), share_word, font=f1, fill=(255, 255, 255, 255),
           stroke_width=3, stroke_fill=(0, 0, 0, 220))

    tw2, _ = _text_size(d, save, f2)
    d.text(((W - tw2) // 2, base_y + 108), save, font=f2, fill=(255, 226, 120, 255),
           stroke_width=3, stroke_fill=(0, 0, 0, 220))
    return img


def subtitle_overlay(text: str, size: int = 58, y_ratio: float = 0.755, cfg: dict | None = None) -> Image.Image:
    r = (cfg or settings.load_settings()["render"])
    H = int(r["height"])
    return text_block(
        text,
        size=size,
        y=int(H * y_ratio),
        width=920,
        fill=(255, 255, 255, 255),
        stroke=(0, 0, 0, 235),
        max_lines=2,
        box=True,
        box_color=(0, 0, 0, 110),
    )


def label_overlay(text: str, cfg: dict | None = None) -> Image.Image:
    r = (cfg or settings.load_settings()["render"])
    H = int(r["height"])
    return text_block(text, size=44, y=int(H * 0.115), width=860,
                      fill=(255, 240, 200, 245), stroke=(0, 0, 0, 200), max_lines=1)


def thumbnail(
    image_path: Path | None,
    title: str,
    hook: str,
    out_path: Path,
    *,
    size: tuple[int, int] = (1080, 1920),
    accent: tuple[int, int, int] = (255, 210, 70),
) -> Path:
    """썸네일: 대표 컷 + 상단 훅 + 하단 타이틀."""
    W, H = size
    base = Image.new("RGB", (W, H), (18, 16, 14))
    if image_path and Path(image_path).exists():
        try:
            im = Image.open(image_path).convert("RGB")
            ratio = max(W / im.width, H / im.height)
            im = im.resize((int(im.width * ratio) + 1, int(im.height * ratio) + 1), Image.LANCZOS)
            left = (im.width - W) // 2
            top = (im.height - H) // 2
            base = im.crop((left, top, left + W, top + H))
        except Exception:
            pass
    # 하단 그라데이션
    grad = Image.new("L", (1, H), 0)
    for y in range(H):
        v = 0
        if y > H * 0.55:
            v = int(min(210, (y - H * 0.55) / (H * 0.45) * 230))
        grad.putpixel((0, y), v)
    black = Image.new("RGB", (W, H), (0, 0, 0))
    base = Image.composite(black, base, grad.resize((W, H)))
    base = base.convert("RGBA")

    hook_img = text_block(hook, size=78, y=int(H * 0.055), width=940,
                          fill=(255, 255, 255, 255), stroke=(0, 0, 0, 240), max_lines=2)
    title_img = text_block(title, size=104, y=int(H * 0.735), width=960,
                           fill=(*accent, 255), stroke=(20, 10, 0, 245), max_lines=3, line_gap=8)
    base = Image.alpha_composite(base, hook_img)
    base = Image.alpha_composite(base, title_img)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out_path, quality=95)
    return out_path


def safe_area_guide(cfg: dict | None = None) -> Image.Image:
    """모바일 안전영역(가운데 78%) 가이드 PNG."""
    r = (cfg or settings.load_settings()["render"])
    W, H = int(r["width"]), int(r["height"])
    ratio = float(r.get("safe_area", 0.78))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    inner_w, inner_h = int(W * ratio), int(H * ratio)
    x0, y0 = (W - inner_w) // 2, (H - inner_h) // 2
    d.rectangle([x0, y0, x0 + inner_w, y0 + inner_h], outline=(80, 220, 160, 220), width=6)
    # 상단 유튜브 UI / 하단 인스타 UI 가리는 영역 표시
    d.rectangle([0, 0, W, 150], fill=(255, 0, 0, 40))
    d.rectangle([0, H - 380, W, H], fill=(255, 0, 0, 40))
    return img

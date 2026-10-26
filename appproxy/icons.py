# -*- coding: utf-8 -*-
"""为每个应用档案生成图标 —— 「同一个软件、多个代理、多个图标」靠它区分。

设计
    底色 + 大字 = 这是哪个软件（同一个 exe 的所有档案底色一致、字母一致）
    右下角圆形角标 = 走哪条线路（不同线路不同颜色 + 短码）
    这样桌面上并排放 3 个 Chrome 图标，一眼能看出哪个走哪条线。

只依赖 Pillow；没有 Pillow 时上层会优雅降级为「用 exe 原图标」。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

# 调色板：饱和度适中，深浅背景下都能看清
PALETTE = ("#2F6BFF", "#12A150", "#E5484D", "#F5A524", "#8B5CF6",
           "#0EA5E9", "#EC4899", "#F97316", "#14B8A6", "#6366F1")

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyhbd.ttc",      # 微软雅黑 Bold，中文也能显示
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
)


def _pick(seed: str, *, skip: str | None = None) -> str:
    n = int(hashlib.md5((seed or "x").encode("utf-8")).hexdigest()[:8], 16)
    color = PALETTE[n % len(PALETTE)]
    if skip and color == skip:               # 底色和角标撞色时顺延一个
        color = PALETTE[(n + 1) % len(PALETTE)]
    return color


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _font(size: int):
    from PIL import ImageFont
    for p in FONT_CANDIDATES:
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _glyph(profile) -> str:
    """取 1~2 个字符做标识：中文名取首字，英文名取 exe 首字母。"""
    name = (profile.name or "").strip()
    if name and not name.isascii():
        return name[0]
    stem = Path(profile.exe).stem or name or "?"
    letters = [c for c in stem if c.isalnum()]
    return (letters[0] if letters else "?").upper()


def _center_text(draw, box, text: str, size: int, fill) -> None:
    from PIL import ImageDraw  # noqa: F401  (仅类型提示用)
    font = _font(size)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    w, h = right - left, bottom - top
    cx = box[0] + (box[2] - box[0] - w) / 2 - left
    cy = box[1] + (box[3] - box[1] - h) / 2 - top
    draw.text((cx, cy), text, font=font, fill=fill)


def render(profile, out: Path | None = None) -> Path:
    """画一个 256 基准的多尺寸 .ico，返回路径。"""
    from PIL import Image, ImageDraw

    out = Path(out) if out else profile.icon_path()
    out.parent.mkdir(parents=True, exist_ok=True)

    size = 256
    bg = _pick(profile.exe.lower())
    accent = _pick(profile.line_id or "direct", skip=bg)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 底板：纯色圆角方块（不做高光渐变 —— 在小尺寸下会露出难看的白块）
    d.rounded_rectangle((4, 4, size - 4, size - 4), radius=54, fill=_hex(bg) + (255,))

    # 主体字母：整体偏左上，给右下角标让位，避免笔画被压住
    _center_text(d, (-10, -18, size - 46, size - 76), _glyph(profile), 116,
                 (255, 255, 255, 245))

    # 角标：白环 + 线路色 + 短码
    cx, cy, r = size - 60, size - 60, 52
    d.ellipse((cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6), fill=(255, 255, 255, 255))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_hex(accent) + (255,))
    code = profile.short_code()
    _center_text(d, (cx - r, cy - r + 2, cx + r, cy + r), code, 50 if len(code) < 3 else 36,
                 (255, 255, 255, 250))

    img.save(out, format="ICO",
             sizes=[(s, s) for s in ICO_SIZES])
    return out


def available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False

# -*- coding: utf-8 -*-
"""生成「图标预览图」：把三个档案的图标并排画出来，方便直观比对。

输出：docs\\图标预览.png
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from appproxy import icons, profiles, settings
from appproxy.registry import Registry

OUT = Path(os.environ["APPPROXY_SHOT_DIR"]) / "图标预览.png" \
    if os.environ.get("APPPROXY_SHOT_DIR") else ROOT / "docs" / "图标预览.png"
BG = (247, 248, 250)
CARD = (255, 255, 255)
TEXT = (32, 38, 48)
MUTED = (110, 120, 135)


def main() -> int:
    settings.ensure_dirs()
    store = profiles.ProfileStore().load()
    reg = Registry().load()
    items = []
    for p in store.profiles:
        ico = p.icon_path()
        if not ico.is_file():
            icons.render(p)
        line = reg.get(p.line_id) if p.line_id else None
        items.append((p, line))
    if not items:
        print("没有档案")
        return 1

    cell_w, cell_h, pad = 240, 300, 28
    W = pad * 2 + cell_w * len(items)
    H = pad + 150 + 130
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    title_font = icons._font(30)
    name_font = icons._font(26)
    small_font = icons._font(22)

    d.text((pad, 18), "分应用代理管家 · 同软件多代理多图标", font=title_font, fill=TEXT)

    for i, (p, line) in enumerate(items):
        x = pad + i * cell_w
        y = 84
        d.rounded_rectangle((x + 8, y - 10, x + cell_w - 8, y + 200), radius=18, fill=CARD)
        with Image.open(p.icon_path()) as im:
            big = im.convert("RGBA")
            try:
                big.size = (128, 128)
            except Exception:
                big = big.resize((128, 128), Image.LANCZOS)
        img.paste(big, (x + (cell_w - 128) // 2, y + 12), big)
        d.text((x + 26, y + 156), p.name, font=name_font, fill=TEXT)
        proxy = f"127.0.0.1:{line.http_port}" if line else "直连（不走代理）"
        d.text((x + 26, y + 186), proxy, font=small_font, fill=MUTED)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print(f"[OK] {OUT} ({W}x{H})")
    for p, line in items:
        print(f"  {p.name:<14} {(line.http_port if line else '直连'):>6}  {p.icon_path().name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

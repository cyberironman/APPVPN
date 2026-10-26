# -*- coding: utf-8 -*-
"""生成「TUN 验证在哪里点」的说明图。

输出：docs\\TUN在哪里点.png
左边是**真实界面截图**（设置页 TUN 卡片），右边是手绘的 UAC 示意图 ——
因为 UAC 窗口是系统弹的，截不到，只能画个样子告诉用户点哪个按钮。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from appproxy import icons  # noqa: E402

OUT = Path(os.environ["APPPROXY_SHOT_DIR"]) / "TUN在哪里点.png" \
    if os.environ.get("APPPROXY_SHOT_DIR") else ROOT / "docs" / "TUN在哪里点.png"
SHOT = Path(os.environ.get("APPPROXY_SHOT_SRC") or (ROOT / "docs" / "_settings-full.png"))

BG = (244, 246, 249)
CARD = (255, 255, 255)
BORDER = (222, 227, 234)
TEXT = (26, 32, 42)
MUTED = (126, 136, 150)
RED = (222, 56, 62)
BLUE = (35, 100, 220)

F_T = icons._font(30)
F_H = icons._font(22)
F_B = icons._font(19)
F_S = icons._font(17)

# 截图里「开启 TUN」按钮的相对位置（相对 crop 左上角），实测出来的
BTN_BOX = (88, 86, 520, 148)


def rounded(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def badge(d, xy, text, r=15, fill=RED):
    x, y = xy
    d.ellipse((x - r, y - r, x + r, y + r), fill=fill)
    w = d.textlength(text, font=F_H)
    d.text((x - w / 2, y - r + 4), text, font=F_H, fill=(255, 255, 255))


def draw_uac(d, x, y, w=520, h=210):
    """画一个「用户账户控制」窗口的示意（真实窗口截不到）。"""
    d.rectangle((x + 4, y + 6, x + w + 4, y + h + 6), fill=(232, 235, 240))
    rounded(d, (x, y, x + w, y + h), 8, fill=CARD, outline=BORDER)
    d.rectangle((x, y, x + w, y + 42), fill=(243, 246, 252))
    d.text((x + 18, y + 10), "用户账户控制", font=F_H, fill=TEXT)
    d.text((x + 18, y + 58), "你要允许此应用对你的设备进行更改吗？", font=F_B, fill=TEXT)
    d.text((x + 18, y + 88), "程序名称：Python", font=F_S, fill=MUTED)
    d.text((x + 18, y + 112), "已验证的发布者：python.org", font=F_S, fill=MUTED)

    # 「是」按钮 —— 要圈出来的那个
    by = y + h - 58
    rounded(d, (x + w - 190, by, x + w - 100, by + 40), 5,
            fill=(0, 103, 192), outline=(0, 90, 170))
    d.text((x + w - 168, by + 9), "是(Y)", font=F_H, fill=(255, 255, 255))
    rounded(d, (x + w - 88, by, x + w - 18, by + 40), 5,
            fill=(240, 242, 246), outline=BORDER)
    d.text((x + w - 70, by + 9), "否(N)", font=F_H, fill=TEXT)

    box = (x + w - 198, by - 10, x + w - 92, by + 50)
    d.rounded_rectangle(box, radius=9, outline=RED, width=4)
    badge(d, (box[0] - 6, box[1] - 6), "2")
    return box


def main() -> int:
    if not SHOT.is_file():
        print(f"[!!] 缺截图：{SHOT}（先用 headless 截一张设置页全图）")
        return 1

    shot = Image.open(SHOT).convert("RGB")
    crop = shot.crop((256, 536, 1060, 792))         # TUN 卡片所在的一条（130 起，避开左侧栏）
    scale = 0.92
    crop = crop.resize((int(crop.width * scale), int(crop.height * scale)),
                       Image.LANCZOS)

    W = 1060
    img = Image.new("RGB", (W, 900), BG)
    d = ImageDraw.Draw(img)

    d.text((34, 22), "TUN 验证：你只需要点两下", font=F_T, fill=TEXT)
    d.text((36, 66), "配置已通过 sing-box 真机校验，剩下的只是权限。全程约 20 秒。",
           font=F_B, fill=MUTED)

    # ---- 第 1 步：真实截图 + 红框 ----
    px, py = 34, 108
    rounded(d, (px - 6, py - 6, px + crop.width + 6, py + crop.height + 6), 10,
            fill=CARD, outline=BORDER)
    img.paste(crop, (px, py))
    b = BTN_BOX
    box1 = (px + int(b[0] * scale), py + int(b[1] * scale),
            px + int(b[2] * scale), py + int(b[3] * scale))
    d.rounded_rectangle(box1, radius=8, outline=RED, width=4)
    badge(d, (box1[0] + 84, box1[1] - 14), "1")

    y = py + crop.height + 16
    d.text((px + 2, y), "① 主界面 → 左下角「设置」→ 找到「TUN · 虚拟网卡接管（高级）」→ 点「开启 TUN」",
           font=F_B, fill=TEXT)
    y += 30
    d.text((px + 2, y), "② 屏幕上会弹出「用户账户控制」，点「是」——只有第一次会弹，以后不再弹",
           font=F_B, fill=TEXT)

    # ---- 第 2 步：UAC 示意 ----
    y += 44
    ux = px + 6
    box2 = draw_uac(d, ux, y, w=560, h=196)
    d.text((ux + 590, y + 12), "点这个「是」", font=F_H, fill=RED)
    d.text((ux + 590, y + 46), "它在弹窗右下角。", font=F_S, fill=MUTED)
    d.text((ux + 590, y + 72), "屏幕会临时变暗，", font=F_S, fill=MUTED)
    d.text((ux + 590, y + 98), "这是系统的授权提示，", font=F_S, fill=MUTED)
    d.text((ux + 590, y + 124), "不是软件出了问题。", font=F_S, fill=MUTED)

    # ---- 底部：还有别的路 ----
    y += 196 + 34
    d.line((34, y, W - 34, y), fill=BORDER, width=1)
    y += 16
    d.text((34, y), "不想走界面也行：双击桌面的「验证TUN（点一次授权）」——它把配置校验、装任务、",
           font=F_S, fill=MUTED)
    d.text((34, y + 24), "起内核、检查网卡全做一遍，跑完弹窗告诉你结果，报告写在 "
                         "D:\\AppProxy\\logs\\tun-verify.txt", font=F_S, fill=MUTED)

    img = img.crop((0, 0, W, y + 58))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print(f"{OUT} {img.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

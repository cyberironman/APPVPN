# -*- coding: utf-8 -*-
"""生成「托盘菜单预览图」：把托盘图标 + 实际菜单结构画成一张图。

输出：docs\\托盘菜单.png
内容取自当前注册表/档案（线路名、勾选状态都是真实的），不是手写示意图。
"""
from __future__ import annotations

import os

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from appproxy import icons, profiles, settings, sysproxy, tray  # noqa: E402
from appproxy.registry import Registry  # noqa: E402

OUT = Path(os.environ["APPPROXY_SHOT_DIR"]) / "托盘菜单.png" \
    if os.environ.get("APPPROXY_SHOT_DIR") else ROOT / "docs" / "托盘菜单.png"
BG = (244, 246, 249)
CARD = (255, 255, 255)
BORDER = (226, 230, 236)
TEXT = (28, 34, 44)
MUTED = (128, 138, 152)
ACCENT = (47, 107, 255)
HOVER = (231, 239, 255)
ROW_H = 38

F_TITLE = icons._font(28)
F_HEAD = icons._font(21)
F_ITEM = icons._font(20)
F_SMALL = icons._font(18)


def fit(d, text: str, font, maxw: float, tail: str = "...") -> str:
    """超宽就截断加省略号，避免文字跑出卡片。"""
    if d.textlength(text, font=font) <= maxw:
        return text
    while text and d.textlength(text + tail, font=font) > maxw:
        text = text[:-1]
    return text + tail


def plain(s: str) -> str:
    """预览图用安全字符：字体里没有的字形换掉。"""
    return (s.replace("·", "-").replace("▸", ">").replace("＋", "+")
             .replace("⚡", "").strip())


def card(d, x, y, w, rows, title: str = "") -> int:
    """画一个菜单卡片，返回高度。"""
    pad = 10 + (30 if title else 0)
    h = pad * 2 + ROW_H * len(rows)
    d.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=CARD,
                        outline=BORDER, width=1)
    cy = y + pad // 2 + 4
    if title:
        d.text((x + 18, cy), title, font=F_SMALL, fill=MUTED)
        cy += 28
    for row in rows:
        kind = row[0]
        if kind == "sep":
            d.line((x + 10, cy + ROW_H // 2, x + w - 10, cy + ROW_H // 2),
                   fill=BORDER, width=1)
            cy += ROW_H
            continue
        label = plain(row[1])
        hot = row[2] if len(row) > 2 else False
        if hot:
            d.rounded_rectangle((x + 6, cy + 3, x + w - 6, cy + ROW_H - 3),
                                radius=6, fill=HOVER)
        d.text((x + 18, cy + 7), fit(d, label, F_ITEM, w - 48), font=F_ITEM,
               fill=ACCENT if hot else TEXT)
        cy += ROW_H
    return h


def main() -> int:
    settings.ensure_dirs()
    reg = Registry().load()
    store = profiles.ProfileStore().load()
    # 演示图模式：不把本机真实的系统代理状态（可能带别人的端口）画进对外截图
    sp = {"enable": False, "server": ""} if os.environ.get("APPPROXY_SHOT_CLEAN") \
        else sysproxy.read()
    book = tray.Tray()
    _, summary = book.state()
    if not (settings.icons_dir() / "tray.ico").is_file():
        tray.save_tray_icon()

    W, H = 1010, 1200          # 先给足高度，画完按内容裁掉
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((34, 22), "分应用代理管家 - 托盘菜单", font=F_TITLE, fill=TEXT)
    d.text((34, 62), "线路名、勾选状态取自当前运行数据", font=F_SMALL, fill=MUTED)

    with Image.open(settings.icons_dir() / "tray.ico") as im:
        big = im.convert("RGBA").resize((92, 92), Image.LANCZOS)
    img.paste(big, (36, 108), big)
    for i, line in enumerate(("绿色 = 全部运行", "蓝色 = 系统代理已接管",
                             "黄色 = 部分运行", "灰色 = 全部停止")):
        d.text((36, 214 + i * 24), line, font=F_SMALL, fill=MUTED)

    # ---- 主菜单（顶层 6 项封顶：只放日常动作）----
    main_rows = [
        ("t", f"分应用代理管家 · {summary}", True),
        ("sep",),
        ("i", "打开主界面"), ("i", "设置…", True),
        ("sep",),
        ("i", "启动所有软件"), ("i", "关闭所有软件（并释放内核）"),
        ("sep",), ("i", "系统代理"), ("sep",), ("i", "退出"),
    ]
    h_main = card(d, 170, 108, 420, main_rows)

    d.text((170, 108 + h_main + 22),
           "配置类的东西都在主界面设置页：线路 / 程序库 / 开机自启 / 目录",
           font=F_SMALL, fill=MUTED)
    d.text((170, 108 + h_main + 48),
           "托盘点「设置…」= 主界面左下角「设置」= 右上角齿轮，同一页",
           font=F_SMALL, fill=MUTED)
    d.text((170, 108 + h_main + 74),
           "界面已开着时这两项的文案自动变：显示主界面（已在后台）/ 设置（切到界面并闪烁提示）",
           font=F_SMALL, fill=MUTED)

    # ---- 系统代理子菜单（顶层唯一保留的二级菜单）----
    sys_rows = [("i", f"当前：{sysproxy.describe(sp)}")]
    for l in reg.lines:
        mark = "✓" if sysproxy.matches_port(sp, l.http_port) else "　"
        sys_rows.append(("i", f"{mark} {l.name}（{l.http_port}）"))
    sys_rows += [("sep",), ("i", "关闭系统代理（还原原设置）")]
    y = 108 + card(d, 640, 108, 340, sys_rows, title="系统代理 >") + 18

    bottom = max(108 + h_main + 80, y) + 30
    img = img.crop((0, 0, W, bottom))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print(f"[OK] {OUT} ({img.size[0]}x{img.size[1]})")
    print(f"     状态：{summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""界面用的小工具：图标转 PNG、按可用节点生成配置。

放在独立模块里，是为了让 webui 不直接依赖 Pillow / 内核实现细节
——没装 Pillow 时界面还能正常跑，只是不显示头像。
"""
from __future__ import annotations

from pathlib import Path

from . import settings, singconf, xrayconf
from .models import Line, Node
from .profiles import AppProfile


def icon_png(profile: AppProfile) -> Path | None:
    """把档案的 .ico 转成界面能用的 .png（带缓存，只转一次）。"""
    src = profile.icon_path()
    if not src.is_file():
        return None

    out = settings.icons_dir() / "png" / f"{profile.id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return out

    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        with Image.open(src) as im:
            im.convert("RGBA").save(out, "PNG")
    except Exception:  # noqa: BLE001
        return None
    return out


def build_config(line: Line, nodes: list[Node], usable: set[str] | None = None) -> dict:
    """按线路引擎生成配置字典（测验通过的节点优先）。"""
    if (line.engine or "").lower().startswith("sing"):
        return singconf.build(line, nodes, usable=usable)
    return xrayconf.build(line, nodes)

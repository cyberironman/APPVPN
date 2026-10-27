# -*- coding: utf-8 -*-
"""开机自启：往「启动」文件夹里放一个快捷方式（不写注册表 Run 键）。

为什么用启动文件夹而不是注册表：
    * 不需要管理员权限；
    * 用户能在 `shell:startup` 里亲眼看到、手动删掉，比注册表透明；
    * 卸载/迁移时不会有残留的注册表项。

默认**不开启**：装好软件不会偷偷加自启，要在托盘菜单里手动勾（用户明确要求过这一点）。
"""
from __future__ import annotations

import os
from pathlib import Path

from . import selfrun, settings, shortcut

LNK_NAME = "分应用代理管家.lnk"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / r"Microsoft\Windows\Start Menu\Programs\Startup"
    return Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"


def target_lnk() -> Path:
    return startup_dir() / LNK_NAME


def tray_script() -> Path:
    """源码模式下托盘的入口脚本；打包后没有这个文件，改用 exe 的 tray 子命令。"""
    return settings.code_root() / "appproxy" / "tray.py"


def enabled() -> bool:
    return target_lnk().is_file()


def enable() -> Path:
    icon = settings.icons_dir() / "tray.ico"
    return shortcut.create_lnk(
        target_lnk(), shortcut.python_exe(), selfrun.self_args(["tray"]),
        selfrun.self_cwd(),
        str(icon if icon.is_file() else shortcut.python_exe()),
        "分应用代理管家（开机自启）")


def disable() -> bool:
    try:
        target_lnk().unlink()
        return True
    except OSError:
        return False


def toggle() -> bool:
    """返回切换后的状态：True = 现在开着自启。"""
    if enabled():
        disable()
        return False
    enable()
    return True


def status() -> dict:
    return {"enabled": enabled(), "lnk": str(target_lnk()),
            "startup_dir_exists": startup_dir().is_dir()}

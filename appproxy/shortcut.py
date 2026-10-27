# -*- coding: utf-8 -*-
"""生成 Windows 快捷方式（.lnk）—— 每个应用档案一个，双击就能带代理启动。

快捷方式指向的不是目标程序本身，而是我们的启动器子命令：
    源码运行：  pythonw.exe -m appproxy.cli profile launch <id>
    打包运行：  AppProxy.exe profile launch <id>
两种形态由 appproxy.selfrun 统一处理 —— 打包后没有 pythonw.exe 可用，
写死解释器路径的话，发出去的 exe 建出来的快捷方式会全部点不开。

本机桌面有两个（C:\\Users\\<user>\\Desktop 与 D:\\OneDrive\\Desktop），
用户实际看到的是 OneDrive 那个，所以两处都放（见 settings.desktop_dirs）。
"""
from __future__ import annotations

from pathlib import Path

from . import selfrun, settings


def python_exe() -> Path:
    """快捷方式的目标可执行文件。

    源码运行时是 pythonw.exe（无控制台窗口），打包后就是 AppProxy.exe 自己。
    名字保留为 python_exe 是为了不动老调用方；语义已经是「重新拉起本程序的 exe」。
    """
    import os
    fixed = os.environ.get("APPPROXY_PYTHON")
    if fixed and Path(fixed).is_file():
        return Path(fixed)
    return selfrun.self_exe()


def launcher_arguments(profile_id: str) -> str:
    """快捷方式的参数：走 `profile launch <id>` 子命令。

    早期版本这里拼的是 `launcher.py --profile <id>`，打包成 exe 后既没有
    pythonw.exe 也没有 .py 文件，快捷方式全部点不开，所以改由 selfrun 统一。
    """
    return selfrun.self_args(["profile", "launch", profile_id])


def create_lnk(lnk: Path, target: Path, arguments: str = "", working_dir: str = "",
               icon: str = "", description: str = "") -> Path:
    """写一个 .lnk。优先用系统 COM，失败再退回 pylnk3。"""
    lnk = Path(lnk)
    lnk.parent.mkdir(parents=True, exist_ok=True)
    try:
        import win32com.client  # type: ignore
        shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortCut(str(lnk))
        sc.TargetPath = str(target)
        if arguments:
            sc.Arguments = arguments
        if working_dir:
            sc.WorkingDirectory = working_dir
        sc.IconLocation = f"{icon or target},0"
        sc.Description = description or lnk.stem
        sc.WindowStyle = 1
        sc.Save()
        return lnk
    except Exception as com_err:  # noqa: BLE001  COM 不可用时兜底
        try:
            import pylnk3  # type: ignore
            item = pylnk3.Lnk()
            try:
                item.specify_local_location(str(target))
            except Exception:  # noqa: BLE001  老版本没有这个方法
                pass
            item.description = description or lnk.stem
            item.arguments = arguments
            item.working_dir = working_dir or str(Path(target).parent)
            item.save(str(lnk))
            return lnk
        except Exception as plain_err:  # noqa: BLE001
            raise RuntimeError(f"建快捷方式失败：COM（{com_err}）/ pylnk3（{plain_err}）") \
                from plain_err


def install(profile, *, desktops: bool = True) -> list[Path]:
    """建快捷方式：母版放 shortcuts\\，再往桌面投副本。返回生成的文件列表。"""
    icon = profile.icon_path()
    if not icon.is_file():
        icon = profile.exe_path()
    name = f"{profile.name}.lnk"
    made: list[Path] = []

    master = create_lnk(settings.shortcuts_dir() / name,
                        python_exe(), launcher_arguments(profile.id),
                        selfrun.self_cwd(), str(icon),
                        f"{profile.name}（{profile.line_id or '直连'}）")
    made.append(master)
    profile.shortcut = str(master)

    if desktops:
        for desk in settings.desktop_dirs():
            try:
                made.append(create_lnk(desk / name, python_exe(),
                                       launcher_arguments(profile.id),
                                       selfrun.self_cwd(), str(icon),
                                       f"{profile.name}（{profile.line_id or '直连'}）"))
            except Exception:  # noqa: BLE001  某个桌面写不进去（权限/只读）不影响其他
                continue
    return made


TRAY_NAME = "分应用代理管家.lnk"


def install_tray(desktops: bool = True) -> list[Path]:
    """给托盘程序本身建快捷方式（不是某个应用档案）。"""
    args = selfrun.self_args(["tray"])
    cwd = selfrun.self_cwd()
    icon = settings.icons_dir() / "tray.ico"
    if not icon.is_file():
        icon = python_exe()
    made = [create_lnk(settings.shortcuts_dir() / TRAY_NAME, python_exe(),
                       args, cwd, str(icon),
                       "分应用代理管家（托盘）")]
    if desktops:
        for desk in settings.desktop_dirs():
            try:
                made.append(create_lnk(desk / TRAY_NAME, python_exe(), args,
                                       cwd, str(icon),
                                       "分应用代理管家（托盘）"))
            except Exception:  # noqa: BLE001
                continue
    return made


def remove(profile) -> int:
    """删掉这个档案的快捷方式（母版 + 桌面副本），返回删掉的个数。"""
    targets = []
    if profile.shortcut:
        targets.append(Path(profile.shortcut))
    name = f"{profile.name}.lnk"
    targets.append(settings.shortcuts_dir() / name)
    targets.extend(desk / name for desk in settings.desktop_dirs())
    n = 0
    for p in dict.fromkeys(targets):
        try:
            if p.is_file():
                p.unlink()
                n += 1
        except OSError:
            continue
    return n

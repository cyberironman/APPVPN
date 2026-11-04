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

import os
from pathlib import Path

from . import selfrun, settings
from .registry import DEFAULT_LINE


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


def _pylnk3_write(lnk: Path, target: Path, arguments: str, working_dir: str,
                  icon: str, description: str) -> None:
    """用 pylnk3 直接写 lnk（不经过系统 COM，路径按字面写入）。

    三个坑都踩过：① HasLinkInfo 等标志位默认是关的，不开的话目标路径根本
    不会写进文件，双击毫无反应；② specify_local_location 不传 drive_type
    的话 drive_serial 是空串，save 直接 struct.error；③ local_base_path
    只支持 ANSI，中文路径会 UnicodeEncodeError —— 中文只能进 Unicode 的
    arguments 字段（比如「记事本 + 中文文件名」这种组合）。
    """
    import pylnk3  # type: ignore
    # Windows 外壳的 LinkInfo.LocalBasePath 只认反斜杠；路径里混进正斜杠
    # （Git Bash / 某些 pathlib 场景会产生）双击就会弹「没有关联的应用」。
    # 写入前统一 normpath 归一，icon、working_dir 同理。
    norm = lambda s: os.path.normpath(s) if s else s  # noqa: E731
    target = Path(norm(str(target)))
    icon = norm(icon)
    working_dir = norm(working_dir or str(target.parent))
    item = pylnk3.Lnk()
    flags = item.link_flags
    flags.HasLinkInfo = True
    flags.HasName = True
    flags.HasWorkingDir = True
    if arguments:
        flags.HasArguments = True
    flags.HasIconLocation = True
    flags.IsUnicode = True
    item.specify_local_location(str(target), drive_type=pylnk3.DRIVE_FIXED,
                                drive_serial=0x12345678, volume_label="APP")
    item.description = description or lnk.stem
    item.working_dir = working_dir or str(target.parent)
    if arguments:
        item.arguments = arguments
    item.icon = icon or str(target)
    item.icon_index = 0
    item.save(str(lnk))


def _lnk_local_base(lnk: Path) -> str:
    r"""纯结构解析 LinkInfo.LocalBasePath（不依赖 pylnk3 的宽容回读）。

    沙箱里 COM 会把 D:\ 解析成 \\主机名\盘 的 UNC，LocalBasePath 只剩
    残缺的「D:\」—— pylnk3 回读照样是个非空字符串，拦不住；必须按
    [MS-SHLLINK] 字节结构自己抠出来看。
    """
    try:
        d = Path(lnk).read_bytes()
        if len(d) < 76 or d[:4] != b"L\x00\x00\x00":
            return ""
        flags = int.from_bytes(d[20:24], "little")
        off = 76
        if flags & 0x1:  # HasTargetIDList
            off += 2 + int.from_bytes(d[off:off + 2], "little")
        if not (flags & 0x2):  # HasLinkInfo
            return ""
        lbpo = int.from_bytes(d[off + 16:off + 20], "little")
        if not lbpo:
            return ""
        raw = d[off + lbpo:].split(b"\x00")[0]
        return raw.decode("mbcs", "replace")
    except Exception:  # noqa: BLE001  读不回就当它坏了
        return ""


def _lnk_target_is_local(lnk: Path) -> bool:
    """判断 lnk 的目标是否为可用的本地路径。

    三种坏形态都拒收：UNC（\\机器名\盘）、正斜杠（D:/x/y.exe，Windows
    外壳不认，双击就是「没有关联的应用」）、裸盘根残缺路径（D:\，
    COM 被 UNC 化的典型残骸）。
    """
    path = _lnk_local_base(lnk)
    if not path or "/" in path or path.startswith("\\\\"):
        return False
    drive, rest = os.path.splitdrive(path)
    return bool(drive) and rest not in ("", "\\", "/")


def create_lnk(lnk: Path, target: Path, arguments: str = "", working_dir: str = "",
               icon: str = "", description: str = "") -> Path:
    """写一个 .lnk。优先用系统 COM，写完回读校验；失败或写歪（如被环境
    解析成 \\机器名\盘符 的网络路径）就用 pylnk3 按字面路径重写。
    """
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
        if _lnk_target_is_local(lnk):
            return lnk
        # COM 成功但目标被解析成了 UNC（沙箱/映射盘环境），不能交付
    except Exception:  # noqa: BLE001  COM 不可用或校验不过，走 pylnk3
        pass
    _pylnk3_write(lnk, target, arguments, working_dir, icon, description)
    if not _lnk_target_is_local(lnk):
        raise RuntimeError(f"快捷方式写完仍不是有效本地路径：{lnk}")
    return lnk


def _label(profile) -> str:
    """快捷方式上显示的线路名。

    `@default` 是内部值，直接显示出来用户看不懂 —— 说人话「默认线路」。
    """
    lid = (profile.line_id or "").strip()
    if not lid:
        return "直连"
    if lid == DEFAULT_LINE:
        return "默认线路"
    return lid


def install(profile, *, desktops: bool = True) -> list[Path]:
    """建快捷方式：母版放 shortcuts\\，再往桌面投副本。返回生成的文件列表。"""
    icon = profile.icon_path()
    if not icon.is_file():
        icon = profile.exe_path()
    name = f"{profile.name}.lnk"
    made: list[Path] = []
    label = f"{profile.name}（{_label(profile)}）"

    master = create_lnk(settings.shortcuts_dir() / name,
                        python_exe(), launcher_arguments(profile.id),
                        selfrun.self_cwd(), str(icon), label)
    made.append(master)
    profile.shortcut = str(master)

    if desktops:
        for desk in settings.desktop_dirs():
            try:
                made.append(create_lnk(desk / name, python_exe(),
                                       launcher_arguments(profile.id),
                                       selfrun.self_cwd(), str(icon), label))
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

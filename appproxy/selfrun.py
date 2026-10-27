# -*- coding: utf-8 -*-
"""「再跑一次自己」的统一入口 —— 源码运行 / 打包成 exe 两种情况都走这里。

为什么需要它：
    快捷方式、提权子进程、另起界面进程这三件事，本质都是「把本程序以某个
    子命令再启动一次」。源码运行时是
        pythonw.exe -m appproxy.cli <子命令>
    打包之后没有 Python 解释器可用了，必须是
        AppProxy.exe <子命令>
    如果不统一，打包出来会：快捷方式全指向不存在的 pythonw.exe、
    点「开启 TUN」弹了 UAC 也装不上任务、托盘切界面切不出来。

判断依据：PyInstaller 会给 sys 挂一个 frozen 属性。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 源码运行时用 CLI 模块入口；打包后 exe 自己按子命令分派
_MODULE_ENTRY = ("-m", "appproxy.cli")


def is_frozen() -> bool:
    """当前是不是打包出来的 exe 在跑。"""
    return bool(getattr(sys, "frozen", False))


def self_exe() -> Path:
    """能再次拉起本程序的 exe。

    源码运行时优先 pythonw.exe —— 重新拉起的进程不该弹黑框。
    """
    exe = Path(sys.executable)
    if is_frozen():
        return exe
    pyw = exe.with_name("pythonw.exe")
    return pyw if pyw.is_file() else exe


def self_argv(cli_argv: list[str]) -> list[str]:
    """拼一条完整的 argv，可以直接给 subprocess 用。"""
    head = [str(self_exe())]
    if not is_frozen():
        head += list(_MODULE_ENTRY)
    return head + [str(x) for x in cli_argv]


def self_args(cli_argv: list[str]) -> str:
    """只要参数部分（给 ShellExecuteW 的 lpParameters、给 .lnk 的 Arguments）。

    路径里带空格时 ShellExecuteW 不会自己补引号，所以这里逐个加。
    """
    parts = [] if is_frozen() else list(_MODULE_ENTRY)
    parts += [str(x) for x in cli_argv]
    return " ".join(f'"{p}"' if (" " in p and not p.startswith('"')) else p
                    for p in parts)


def self_cwd() -> str:
    """重新拉起时的好工作目录：源码模式要能 import appproxy。"""
    from . import settings
    if is_frozen():
        return str(Path(sys.executable).parent)
    return str(settings.code_root())


def self_process_names() -> set[str]:
    """「我们自己的进程」的 exe 名（小写）。

    进程探活和「扫描已安装程序」都要把本程序排除掉，否则打包成
    AppProxy.exe 之后，自己会被当成「某个档案正在运行」，内核永远不回收。
    """
    names = {"python.exe", "pythonw.exe", "py.exe", "appproxy.exe"}
    names.add(Path(sys.executable).name.lower())
    return names

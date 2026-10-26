# -*- coding: utf-8 -*-
"""档案进程存活探测：判断「这个档案的程序现在开着吗」。

为什么需要它
    内核不该常驻。它只应该在「有软件正在用这条线路」时活着。
    要自动判断这件事，就必须知道每个档案的程序是否还在跑。
    做到这一点，用户就不用再点「全部停止」——系统自己收摊。

两种匹配方式（按类型选，不混用）
    Chromium 系：靠档案专属的 `--user-data-dir=` 路径匹配。
        精确，同一个 chrome.exe 开三个实例也能分清谁是谁。
    通用程序：exe 完整路径相同就算在跑。
        多实例时无法区分，但「有没有在跑」这个判断是准的。

注意
    Chromium 系**不能**退回到按 exe 匹配 —— 三个档案共用同一个 chrome.exe，
    一退回就会互相误判成全都在跑。
"""
from __future__ import annotations

import re
from collections import defaultdict

import psutil

from .profiles import AppProfile

# 我们自己的进程，永远不算「某个档案在跑」
SELF_NAMES = {"python.exe", "pythonw.exe", "py.exe"}

# Chrome 对带空格的参数是**整个参数**加引号：
#     "--user-data-dir=D:\a b\data"
# 引号在 `--` 前面，不在 `=` 后面，所以不能用「= 后面跟引号」的正则去取，
# 那样路径会在第一个空格处被截成 `D:\a`，探活就永远失败、内核被误回收。
_DATA_DIR_KEY = "--user-data-dir="


def extract_data_dirs(cmdline: str) -> list[str]:
    """从一条命令行里取出所有 --user-data-dir 的值。

    规则：`--user-data-dir=` 前面紧挨着引号 => 整个参数带引号，值延伸到下一个引号；
    否则是裸参数，值延伸到下一个空格。
    """
    out: list[str] = []
    low = cmdline.lower()
    start = 0
    while True:
        i = low.find(_DATA_DIR_KEY, start)
        if i < 0:
            break
        j = i + len(_DATA_DIR_KEY)
        if i > 0 and cmdline[i - 1] == '"':
            k = cmdline.find('"', j)
            val = cmdline[j:k] if k > 0 else cmdline[j:]
        else:
            k = cmdline.find(" ", j)
            val = cmdline[j:k] if k > 0 else cmdline[j:]
        if val:
            out.append(val)
        start = j + max(len(val), 1)
    return out


def _norm(path: str) -> str:
    """统一路径写法，便于比较：反斜杠、去尾部分隔符、小写。"""
    s = str(path).replace("/", "\\").strip()
    if len(s) > 3:
        s = s.rstrip("\\")
    return s.lower()


class Snapshot:
    """一次进程遍历的结果。批量判断多个档案时只扫一遍。"""

    def __init__(self) -> None:
        self.by_exe: dict[str, list[int]] = defaultdict(list)
        self.by_data_dir: dict[str, list[int]] = defaultdict(list)
        self.cmdline: dict[int, str] = {}

    def alive(self, profile: AppProfile) -> bool:
        return bool(self.pids(profile))

    def pids(self, profile: AppProfile) -> list[int]:
        if profile.resolved_kind == "chromium":
            key = _norm(profile.data_path())
            return list(self.by_data_dir.get(key) or [])
        return list(self.by_exe.get(_norm(profile.exe)) or [])


def snapshot() -> Snapshot:
    """扫一遍全系统进程，建两个索引。约 50~120ms。"""
    snap = Snapshot()
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            info = p.info
            name = (info["name"] or "").lower()
            if name in SELF_NAMES:
                continue
            cmd = " ".join(info["cmdline"] or [])
            if cmd:
                snap.cmdline[p.pid] = cmd
                for path in extract_data_dirs(cmd):
                    snap.by_data_dir[_norm(path)].append(p.pid)
            exe = info["exe"]
            if exe:
                snap.by_exe[_norm(exe)].append(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return snap


def alive_map(profiles: list[AppProfile], snap: Snapshot | None = None) -> dict[str, bool]:
    """{档案 id: 是否在跑}。"""
    snap = snap or snapshot()
    return {p.id: snap.alive(p) for p in profiles}


def running_profiles(profiles: list[AppProfile], snap: Snapshot | None = None) -> list[AppProfile]:
    snap = snap or snapshot()
    return [p for p in profiles if snap.alive(p)]

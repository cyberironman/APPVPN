# -*- coding: utf-8 -*-
"""模拟双击桌面快捷方式：os.startfile 就是资源管理器双击的同一个调用。

验证：.lnk -> pythonw -> launcher -> 内核 -> 目标程序 这一整条链真的通。
跑完会把测试起的那个 Chrome 实例关掉（只关用它自己数据目录的那些进程）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

from appproxy import coreproc, profiles, settings  # noqa: E402
from appproxy.registry import Registry  # noqa: E402

PID = sys.argv[1] if len(sys.argv) > 1 else "chrome-direct"


def procs_with(keyword: str) -> list:
    out = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if keyword.lower() in cmd.lower():
                out.append((p.pid, cmd))
        except Exception:  # noqa: BLE001
            continue
    return out


def main() -> int:
    settings.ensure_dirs()
    store = profiles.ProfileStore().load()
    prof = store.require(PID)
    reg = Registry().load()
    line = reg.get(prof.line_id) if prof.line_id else None
    lnk = Path(prof.shortcut or "")
    print(f"档案 {prof.id} / 快捷方式 {lnk}")
    if not lnk.is_file():
        print("[!!] 快捷方式不存在")
        return 1

    data_dir = str(prof.data_path())
    before = {pid for pid, _ in procs_with(data_dir)}
    os.startfile(str(lnk))          # 等同双击

    hit: list[tuple[int, str]] = []
    for _ in range(40):
        time.sleep(0.5)
        hit = [x for x in procs_with(data_dir) if x[0] not in before]
        if hit:
            break
    ok_proc = bool(hit)
    print(f"{'[OK]' if ok_proc else '[!!]'} 目标程序已启动（{len(hit)} 个进程）")
    if hit:
        print(f"     {hit[0][1][:200]}")
    if line:
        up = coreproc.port_open(line.http_port)
        print(f"{'[OK]' if up else '[!!]'} 线路 {line.id} 端口 {line.http_port} "
              f"{'在监听' if up else '没监听'}")
    else:
        up = True
        print("[OK] 直连档案，不需要内核")

    # 收尾：只关这个数据目录的实例
    for pid, _ in procs_with(data_dir):
        try:
            psutil.Process(pid).terminate()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(1.5)
    for pid, _ in procs_with(data_dir):
        try:
            psutil.Process(pid).kill()
        except Exception:  # noqa: BLE001
            pass
    print("[OK] 已关闭测试实例")
    return 0 if (ok_proc and up) else 1


if __name__ == "__main__":
    sys.exit(main())

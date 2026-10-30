# -*- coding: utf-8 -*-
"""档案启动器：双击图标时真正执行的东西。

做三件事，然后退出：
    1. 该档案绑了线路 —— 内核没起来就先拉起来（配置缺了就先生成）；
    2. 按档案的代理参数组成启动命令；
    3. 启动程序（脱离父进程，启动器本身立刻退出，不占资源）。

单独运行（调试用）：
    python -m appproxy.launcher --profile chrome-work --dry-run
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pythonw.exe 下没有标准输出，print 会炸；先兜住
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = sys.stdout

from appproxy import (coreproc, pool, procwatch, profiles, settings,  # noqa: E402
                      shortcut, singconf, subscription, xrayconf)
from appproxy.models import Line  # noqa: E402
from appproxy.registry import Registry  # noqa: E402

DETACHED = 0x00000008 if os.name == "nt" else 0        # DETACHED_PROCESS
NEW_GROUP = 0x00000200 if os.name == "nt" else 0       # CREATE_NEW_PROCESS_GROUP


def log(msg: str) -> None:
    try:
        settings.logs_dir().mkdir(parents=True, exist_ok=True)
        path = settings.logs_dir() / "launcher.log"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")
    except OSError:
        pass
    print(msg)


def shout(msg: str) -> None:
    """界面化提示：启动器是 pythonw 起的，用户看不到控制台。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "分应用代理管家", 0x10)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- 内核
def ensure_core(reg: Registry, line: Line, wait: float = 12.0) -> str:
    """保证这条线路的本地端口在监听（兼容旧调用，实现在 pool 里）。

    走 pool 是为了登记「这条内核是为档案拉的」，之后没人在用时能被自动回收。
    """
    return pool.ensure_core(reg, line, wait=wait)


# --------------------------------------------------------------------------- 主流程
def launch(profile: profiles.AppProfile, reg: Registry, *, dry_run: bool = False,
           no_core: bool = False) -> int:
    line = reg.get(profile.line_id) if profile.line_id else None
    if profile.line_id and line is None:
        raise RuntimeError(f"档案 {profile.id} 绑定的线路 {profile.line_id} 不存在")

    if line and not no_core:
        # 按需起内核：约 1 秒。这里不做任何测速，慢的东西全在后台。
        log(pool.start_for_profile(reg, profile))

    p = profiles.plan(profile, line)
    log(f"启动 {profile.id}：{p.command_line()}")
    log(f"    数据隔离 {p.iso_mode} —— {p.iso_note}")
    if dry_run:
        for k, v in p.env.items():
            log(f"    env {k}={v or '<清空>'}")
        return 0

    if not profile.exists():
        raise RuntimeError(f"找不到程序：{profile.exe}")

    # 隔离目录必须先建好：程序第一次启动时如果发现目录不存在，
    # 有的会自己建，有的直接报错退出，别赌。
    if p.iso_mode in ("user-data-dir", "native"):
        profile.data_path().mkdir(parents=True, exist_ok=True)
    elif p.iso_mode == "env":
        base = profile.data_path()
        for sub in ("Roaming", "Local", "Profile"):
            (base / sub).mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(p.env)
    subprocess.Popen(p.argv, cwd=p.cwd or None, env=env,
                     creationflags=DETACHED | NEW_GROUP, close_fds=True)

    # 等程序真的出现在进程表里再退出。
    # 否则「按需回收」可能在程序启动的那两秒里判定"没人用"，把刚起的内核又停掉。
    if line and not no_core:
        wait_for_process(profile, timeout=20.0)
    return 0


def wait_for_process(profile: profiles.AppProfile, timeout: float = 20.0) -> bool:
    """轮询到档案进程出现为止。返回是否在超时前看到它。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if procwatch.snapshot().alive(profile):
                return True
        except Exception:  # noqa: BLE001  探活失败不该阻断启动
            return False
        time.sleep(0.4)
    log(f"[--] {timeout:.0f} 秒内没看到 {profile.id} 的进程，回收判断会靠宽限期兜底")
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="appproxy-launcher", description="按应用档案启动程序")
    ap.add_argument("--profile", required=True, help="档案 id")
    ap.add_argument("--dry-run", action="store_true", help="只打印命令，不启动")
    ap.add_argument("--no-core", action="store_true", help="不启动内核（内核已手动开着时用）")
    a = ap.parse_args(argv)

    try:
        settings.ensure_dirs()
        store = profiles.ProfileStore().load()
        profile = store.require(a.profile)
        reg = Registry().load()
        return launch(profile, reg, dry_run=a.dry_run, no_core=a.no_core)
    except Exception as e:  # noqa: BLE001
        log(f"[!!] {type(e).__name__}: {e}")
        shout(f"启动失败：\n\n{e}\n\n详细日志：{settings.logs_dir() / 'launcher.log'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

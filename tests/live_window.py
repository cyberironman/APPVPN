# -*- coding: utf-8 -*-
"""端到端（真窗口）：托盘点「设置…」→ 已开着的窗口真的切到设置页。

这个脚本会短暂弹出真实界面窗口（约 10 秒），跑完自动关掉自己的窗口，
不会碰用户自己开的窗口/Chrome。

    python tests/live_window.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from appproxy import gui, settings  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def chk(name: str, cond: bool, extra: str = "") -> None:
    RESULTS.append((name, bool(cond), extra))
    print(("  [OK] " if cond else "  [!!] ") + name + (f"  {extra}" if extra else ""))


def wait_for(fn, timeout: float, step: float = 0.2):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def cleanup() -> None:
    """只杀自己起的：带 gui-profile 的浏览器进程 + 跑 gui.py 的 python 进程。"""
    try:
        import psutil
    except ImportError:
        return
    script = str(settings.code_root() / "appproxy" / "gui.py").lower()
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or []).lower()
            if not cl or p.pid == __import__("os").getpid():
                continue
            if "gui-profile" in cl or (script in cl and "gui.py" in cl):
                p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def main() -> int:
    settings.ensure_dirs()
    if gui.running_port():
        print("[--] 你正开着主界面，先关掉它再跑这个脚本（避免打乱你正在看的页面）")
        return 0

    script = settings.code_root() / "appproxy" / "gui.py"
    proc = subprocess.Popen([sys.executable, str(script)], cwd=str(settings.code_root()),
                            creationflags=0x00000008)
    print(f"起界面进程 pid={proc.pid}（窗口会闪一下，跑完自动关）")
    try:
        port = wait_for(gui.running_port, 25)
        chk("界面服务起来（gui.port 路标可定位）", bool(port), f"端口 {port}")
        if not port:
            return 1

        # 前端加载完会自己上报一次当前页
        cur = wait_for(lambda: gui._get(port, "/api/view/current").get("view") == "profile", 15)
        chk("页面加载并上报当前页 = 主界面", bool(cur))

        # 托盘那一侧的动作
        msg = gui.open_view("settings")
        chk("托盘点「设置…」→ 找到已开窗口（不是新开一个）",
            "已切到已打开的窗口" in msg, msg)
        chk("返回里写明「任务栏闪烁提示」（点了有反馈）", "闪烁" in msg, msg)
        hwnd = gui._find_window()
        chk("按标题能找到界面窗口句柄", bool(hwnd), f"hwnd={hwnd}")
        chk("任务栏闪烁真的调成功了", gui.flash_window() is True)

        got = wait_for(lambda: gui._get(port, "/api/view/current").get("view") == "settings", 8)
        chk("界面真的切到了设置页（前端轮询消费了指令）", bool(got),
            f"当前 {gui._get(port, '/api/view/current').get('view')!r}")

        chk("切页指令是一次性的（信箱已空）",
            gui._get(port, "/api/view/pending").get("view") == "")

        # 再切回主界面，确认双向都通
        gui.open_view("profile")
        back = wait_for(lambda: gui._get(port, "/api/view/current").get("view") == "profile", 8)
        chk("再切回主界面", bool(back))
    finally:
        cleanup()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

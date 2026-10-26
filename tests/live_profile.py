# -*- coding: utf-8 -*-
"""步骤 2 的真实链路自检（会起内核、会起真实进程，跑完自己收尾）。

验证三件事：
    1. 档案启动器能把线路内核拉起来（端口真的在监听）；
    2. 通用程序按档案启动后进程在跑；
    3. 浏览器的命令行里确实带上了 --user-data-dir 和 --proxy-server，
       也就是「同一台机器上两个 Chrome，各走各的代理」。

用法：python tests/live_profile.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from appproxy import coreproc, launcher, profiles, settings  # noqa: E402
from appproxy.registry import Registry  # noqa: E402

OK, FAIL = "[OK]", "[!!]"
NOTEPAD = r"C:\Windows\System32\notepad.exe"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
LINE_ID = sys.argv[1] if len(sys.argv) > 1 else "alpha"
TMP_DIR = settings.home() / "apps" / "_livetest" / "data"


def find_procs(keyword: str) -> list:
    import psutil
    found = []
    for p in psutil.process_iter(["name", "cmdline", "create_time"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if keyword.lower() in cmd.lower():
                found.append(p)
        except Exception:  # noqa: BLE001
            continue
    return found


def main() -> int:
    settings.ensure_dirs()
    reg = Registry().load()
    line = reg.get(LINE_ID)
    if line is None:
        print(f"{FAIL} 没有线路 {LINE_ID}")
        return 1
    print(f"线路 {line.id}：http {line.http_port}，引擎 {line.engine}")

    # ------------------------------------------------ 1. 通用程序
    note = profiles.AppProfile(id="_live-notepad", name="记事本测试", exe=NOTEPAD,
                               line_id=line.id)
    print(f"\n[1] 通用程序档案：{note.resolved_kind}")
    plan = profiles.plan(note, line)
    print(f"    命令 {plan.command_line()}")
    print(f"    环境 HTTPS_PROXY={plan.env.get('HTTPS_PROXY')}")
    launcher.launch(note, reg)
    time.sleep(1.2)
    up = coreproc.port_open(line.http_port)
    print(f"{OK if up else FAIL} 内核端口 {line.http_port} {'已监听' if up else '没起来'}")
    procs = find_procs("notepad.exe")
    print(f"{OK if procs else FAIL} 记事本进程 {len(procs)} 个（pid {[p.pid for p in procs]}）")

    # ------------------------------------------------ 2. 浏览器
    print(f"\n[2] 浏览器档案：{CHROME}")
    chrome = profiles.AppProfile(id="_live-chrome", name="Chrome测试", exe=CHROME,
                                 line_id=line.id, data_dir=str(TMP_DIR),
                                 args=["--headless=new", "--disable-gpu"])
    plan = profiles.plan(chrome, line)
    print(f"    命令 {plan.command_line()}")
    launcher.launch(chrome, reg)
    seen_cmd = ""
    for _ in range(20):
        hits = find_procs(str(TMP_DIR))
        if hits:
            seen_cmd = " ".join(hits[0].info.get("cmdline") or [])
            break
        time.sleep(0.25)
    ok_dir = f"--user-data-dir={TMP_DIR}" in seen_cmd
    ok_proxy = f"--proxy-server=http://127.0.0.1:{line.http_port}" in seen_cmd
    print(f"{OK if ok_dir else FAIL} 命令行带 --user-data-dir")
    print(f"{OK if ok_proxy else FAIL} 命令行带 --proxy-server（端口 {line.http_port}）")
    print(f"    实际参数 {seen_cmd[:220] or '<没抓到进程>'}")
    if not seen_cmd:
        print("    （Chrome 可能起得太快就退了，参数正确性由单测覆盖）")

    # ------------------------------------------------ 3. 收尾
    print("\n[3] 收尾")
    for p in find_procs("notepad.exe"):
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass
    for p in find_procs(str(TMP_DIR)):
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass
    n = coreproc.stop(line.config_path())
    print(f"{OK} 已结束 {n} 个内核进程、清理测试进程")
    return 0 if (up and ok_dir and ok_proxy) else 1


if __name__ == "__main__":
    sys.exit(main())

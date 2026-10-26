# -*- coding: utf-8 -*-
"""端到端验证：分应用（按进程名）分流是否真的生效。

一次运行内完成：测速筛节点 -> 生成 dispatcher 配置 -> 校验 -> 起内核 ->
用 curl（curl.exe）和 python（python.exe）分别走同一端口 -> 比较出口 IP -> 收尾。

判定标准：
    curl.exe  命中规则 -> 出口是代理 IP
    python.exe 未命中规则 -> 出口是本地直连 IP（final = direct）
    两者不同 = 分应用分流成立。

用法（必须在同一个进程内跑，否则工具会话会把子进程收走）：
    python tests/live_dispatch.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from appproxy import coreproc, latency, settings, singconf  # noqa: E402
from appproxy.registry import Registry  # noqa: E402

PORT = 10803
# 必须选一个「国内直连也能通」的地址，否则 final=direct 那条腿测不出来
IP_URL = "http://ip-api.com/line/?fields=query"


def wait_port(port: int, timeout: float = 15) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def ip_via_curl(proxy: str | None) -> str:
    cmd = ["curl", "-sS", "-m", "25", IP_URL]
    if proxy:
        cmd += ["-x", proxy]
    else:
        cmd += ["--noproxy", "*"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    return (p.stdout or p.stderr).strip()[:60]


def ip_via_python(proxy: str | None) -> str:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
    try:
        with opener.open(IP_URL, timeout=25) as r:
            return r.read(64).decode("utf-8", "ignore").strip()
    except Exception as e:  # noqa: BLE001
        return f"失败({type(e).__name__})"


def main() -> int:
    settings.ensure_dirs()
    reg = Registry().load()
    line = reg.require("alpha")
    nodes, _ = reg.load_nodes(line)
    if not nodes:
        print("没有节点缓存，先跑 cli update")
        return 1
    binary = reg.binary("singbox")
    if binary is None:
        print("找不到 sing-box.exe")
        return 1

    print(f"1) 测速 {len(nodes)} 个节点 ...")
    res = latency.measure(nodes, binary, settings.DEFAULT_HEALTH_URL, timeout=7,
                          engine="singbox")
    alive = [n for n in nodes if res.get(n.uid) is not None]
    print(f"   可用 {len(alive)}/{len(nodes)}")
    if not alive:
        print("   没有可用节点，退出")
        return 1

    print("2) 生成分应用配置（curl.exe 走代理，其余直连）...")
    cfg = singconf.build_dispatcher({"alpha": alive}, PORT, {"curl.exe": "auto-alpha"})
    path = singconf.write(cfg, settings.runtime_dir() / "dispatch-demo.json")

    print("3) 校验配置 ...")
    ok, msg = coreproc.validate(path, binary, "singbox")
    print(f"   {'通过' if ok else '失败'} {msg}")
    if not ok:
        return 1

    print(f"4) 起内核（端口 {PORT}）...")
    log = settings.logs_dir() / "sing-dispatch-demo.log"
    with open(log, "ab") as lf:
        proc = subprocess.Popen([str(binary), "run", "-c", str(path)],
                                cwd=str(binary.parent), stdout=lf, stderr=lf,
                                creationflags=0x08000000)
    try:
        if not wait_port(PORT):
            print("   端口没起来，看日志", log)
            return 1
        print("5) 同端口、两个进程，看出口 IP ...")
        direct_ip = ip_via_curl(None)
        curl_ip = ip_via_curl(f"http://127.0.0.1:{PORT}")
        py_ip = ip_via_python(f"http://127.0.0.1:{PORT}")

        print(f"   本地直连      : {direct_ip}")
        print(f"   curl.exe 走端口: {curl_ip}")
        print(f"   python 走端口  : {py_ip}")

        routed = curl_ip not in ("", direct_ip) and "失败" not in curl_ip
        passthru = py_ip == direct_ip
        print()
        print(f"[{'OK' if routed else '!!'}] curl.exe 命中规则 -> 出口被切到代理：{routed}")
        print(f"[{'OK' if passthru else '!!'}] python.exe 未命中 -> 保持直连：{passthru}")
        return 0 if (routed and passthru) else 1
    finally:
        try:
            proc.terminate()
            proc.wait(5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        print("6) 已收尾")


if __name__ == "__main__":
    sys.exit(main())

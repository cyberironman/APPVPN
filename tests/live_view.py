# -*- coding: utf-8 -*-
"""端到端：界面服务的「视图深链 + 托盘切页」通道。

验证的是这一轮新增的那条链路：
    托盘点「设置…」→ 找到已经在跑的界面 → 往内存信箱丢一句话
    → 界面下一轮轮询取走 → 切到设置页

同时验证固定端口 1443 + gui.port 路标能不能被托盘准确定位。
不建窗口（窗口会挡住用户桌面），窗口那一步用 --shot 单独截图。

    python tests/live_view.py            # 只跑接口
    python tests/live_view.py --shot     # 顺便截一张设置页的图
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from appproxy import gui, settings  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def chk(name: str, cond: bool, extra: str = "") -> None:
    RESULTS.append((name, bool(cond), extra))
    print(("  [OK] " if cond else "  [!!] ") + name + (f"  {extra}" if extra else ""))


def main() -> int:
    argv = sys.argv[1:]
    settings.ensure_dirs()

    port = gui.GUI_PORT if not gui._port_open(gui.GUI_PORT, timeout=0.2) else gui._free_port()
    box = gui.ServerBox()
    box.port = port
    threading.Thread(target=gui.serve_http, args=(port, box), daemon=True).start()

    deadline = time.time() + 12
    while time.time() < deadline and not gui._port_open(port):
        time.sleep(0.1)
    if not gui._port_open(port):
        print("[!!] 服务没起来")
        return 1

    gui.port_file().write_text(str(port), encoding="utf-8")
    print(f"服务 http://127.0.0.1:{port}/")

    try:
        # ---- 定位：托盘能不能认出这是我们自己 -------------------------------
        chk("固定端口上认门（/api/ping 带 appproxy 标记）", gui._is_ours(port))
        chk("running_port 靠 gui.port 找到界面", gui.running_port() == port,
            f"得到 {gui.running_port()}")

        # ---- 切页信箱：投递一次、取走、不会反复劫持 -------------------------
        chk("初始没有待切视图", gui._get(port, "/api/view/pending").get("view") == "")
        gui._post(port, "/api/view", {"view": "settings"})
        got = gui._get(port, "/api/view/pending").get("view")
        chk("托盘点「设置…」→ 取到 settings", got == "settings", f"得到 {got!r}")
        chk("取走即清空（不会反复抢视图）",
            gui._get(port, "/api/view/pending").get("view") == "")

        # ---- 设置页要的数据齐不齐 -------------------------------------------
        st = gui._get(port, "/api/settings")
        chk("settings 返回了新档案默认线路字段", "default_line" in st)
        chk("settings 带线路/系统代理/自启/引擎",
            all(k in st for k in ("lines", "sysproxy", "autostart", "engines", "home")))
        chk("settings 带 TUN 状态与清单摘要",
            "tun" in st and "appdb" in st and "installed" in (st.get("tun") or {}))

        # ---- 步骤 5：接线入口 / TUN 状态 -------------------------------------
        su = gui._get(port, "/api/setup")
        chk("setup 接口给出「要不要引导」", "needs_setup" in su and "appdb" in su)
        tun = gui._get(port, "/api/tun")
        chk("tun 状态可读（含进程数与配置路径）",
            "on" in tun and "config" in tun and tun["config"].endswith("tun.json"),
            f"{tun.get('count')} 个进程")

        # ---- 页面本身能不能打开 ---------------------------------------------
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/")
        body = conn.getresponse().read().decode("utf-8", "replace")
        conn.close()
        chk("首页能取到", "分应用代理管家" in body, f"{len(body)} 字节")
        chk("前端有设置入口（左下角整行 + 右上齿轮）",
            'id="btn-settings"' in body and 'id="btn-settings-top"' in body)
        chk("前端支持 #settings 深链", 'HASH_VIEW' in body and "pollPendingView" in body)
        chk("前端有 TUN 卡片与接线引导",
            'id="tun-on"' in body and "viewSetup" in body
            and "/api/setup/subscribe" in body and "/api/setup/local" in body)

        if "--shot" in argv:
            exe = gui.find_browser()
            if exe is None:
                print("  [--] 没有 Chrome/Edge，跳过截图")
            else:
                profile = settings.home() / "shot-profile"
                for tag, out_name in (("", "主界面.png"), ("settings", "设置页.png")):
                    out = settings.code_root() / "docs" / out_name
                    url = f"http://127.0.0.1:{port}/" + (f"#{tag}" if tag else "")
                    cmd = [
                        str(exe), "--headless=new", "--disable-gpu",
                        f"--user-data-dir={profile}",
                        "--no-proxy-server", "--no-first-run",
                        "--hide-scrollbars", "--window-size=1000,720",
                        "--virtual-time-budget=5000",
                        f"--screenshot={out}", url,
                    ]
                    r = subprocess.run(cmd, capture_output=True, timeout=90)
                    ok = out.is_file() and out.stat().st_size > 8000
                    chk(f"截图 {out_name}", ok, f"{out.stat().st_size if out.is_file() else 0} 字节")
    finally:
        try:
            box.server.should_exit = True       # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        try:
            gui.port_file().unlink()
        except OSError:
            pass

    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    if bad:
        for name, _ok, extra in bad:
            print(f"  失败：{name} {extra}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

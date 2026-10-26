# -*- coding: utf-8 -*-
"""端到端：新用户第一次打开（一条线路都没有）会看到什么。

在**临时数据目录**里跑，绝不碰用户自己那份（真实订阅、真实档案）。
验证三件事：
    1. 没有线路时 /api/state 会置 needs_setup，界面渲染接线引导（不是空白页）
    2. 三种接线方式各自的错误提示是不是人话（不是 traceback）
    3. 引导页截图（docs/引导页.png）

    python tests/live_setup.py --shot
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 必须在导入 appproxy 之前换家目录
HOME = Path(tempfile.mkdtemp(prefix="appproxy-setup-"))
os.environ["APPPROXY_HOME"] = str(HOME)

from appproxy import gui, settings  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def chk(name: str, cond: bool, extra: str = "") -> None:
    RESULTS.append((name, bool(cond), extra))
    print(("  [OK] " if cond else "  [!!] ") + name + (f"  {extra}" if extra else ""))


def main() -> int:
    settings.ensure_dirs()
    port = gui._free_port()
    box = gui.ServerBox()
    box.port = port
    threading.Thread(target=gui.serve_http, args=(port, box), daemon=True).start()
    deadline = time.time() + 12
    while time.time() < deadline and not gui._port_open(port):
        time.sleep(0.1)
    if not gui._port_open(port):
        print("[!!] 服务没起来")
        return 1
    print(f"服务 http://127.0.0.1:{port}/ （临时数据目录 {HOME}）")

    try:
        st = gui._get(port, "/api/state")
        chk("空数据目录 → needs_setup = true", st.get("needs_setup") is True)
        chk("空数据目录 → 没有线路/档案",
            not st.get("lines") and not st.get("profiles"))

        su = gui._get(port, "/api/setup")
        chk("setup 接口说明为什么需要引导", "告诉我在哪" in (su.get("hint") or ""),
            su.get("hint", ""))
        chk("清单摘要带上了（命中即建议挂代理）", "条" in (su.get("appdb") or ""),
            su.get("appdb", ""))

        # 三种接线方式的「失败也要说人话」
        errs = {}
        for tag, path, body in (
            ("订阅", "/api/setup/subscribe", {"url": "ftp://x"}),
            ("分享链接", "/api/setup/uri", {"uri": "hello"}),
            ("本地端口", "/api/setup/local", {"protocol": "http", "port": 9}),
        ):
            r = gui._post(port, path, body)
            errs[tag] = r.get("detail") or r.get("message") or str(r)
        chk("三条路都给了人话提示（不是 traceback）",
            all(v and "Traceback" not in v and "Error" not in v for v in errs.values()),
            " / ".join(f"{k}:{v}" for k, v in errs.items()))

        # 本地端口指向一个真实在跑的东西才应该成功
        import http.server
        import socketserver

        with socketserver.TCPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler) as srv:
            real_port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            # 这一步会真起内核（约 1~2 秒），默认 0.8 秒超时不够
            ok = gui._post(port, "/api/setup/local",
                           {"protocol": "http", "port": real_port}, timeout=40)
            chk("本地端口存在 → 建线路并拉起内核", "已添加线路" in (ok.get("message") or ""),
                (ok.get("message") or "")[:80])
            st2 = gui._get(port, "/api/state")
            chk("建好后 needs_setup 自动变 false", st2.get("needs_setup") is False)
            srv.shutdown()

        if "--shot" in sys.argv:
            exe = gui.find_browser()
            if exe is None:
                print("  [--] 没有 Chrome/Edge，跳过截图")
            else:
                out = settings.code_root() / "docs" / "引导页.png"
                # 先清掉刚建的线路，让截图回到「新用户」状态
                from appproxy.registry import Registry
                reg = Registry().load()
                reg.lines = []
                reg.save()
                cmd = [str(exe), "--headless=new", "--disable-gpu",
                       f"--user-data-dir={HOME / 'shot'}",
                       "--no-proxy-server", "--no-first-run", "--hide-scrollbars",
                       "--window-size=1000,720", "--virtual-time-budget=5000",
                       f"--screenshot={out}", f"http://127.0.0.1:{port}/"]
                subprocess.run(cmd, capture_output=True, timeout=90)
                chk("截图 引导页.png", out.is_file() and out.stat().st_size > 8000,
                    f"{out.stat().st_size if out.is_file() else 0} 字节")
    finally:
        try:
            box.server.should_exit = True       # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for name, _ok, extra in bad:
        print(f"  失败：{name} {extra}")
    print(f"\n临时目录留着方便看：{HOME}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

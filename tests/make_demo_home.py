# -*- coding: utf-8 -*-
"""生成一份**演示数据**（假线路、假节点、假订阅地址），专门用来出截图。

为什么需要它：真实数据里有你自己的订阅地址（带 token）和线路名，截图对外发布
等于把账号公开。这个脚本造出来的全是保留示例地址（example.com）和文档专用
IP 段（198.51.100.0/24、203.0.113.0/24），可以放心贴到任何地方。

用法（默认写到 D:\\AppProxy-demo）：

    python tests/make_demo_home.py
    set APPPROXY_HOME=D:\\AppProxy-demo
    python tests/live_view.py --shot

演示数据不是用来真的连网的 —— 节点地址是文档保留段，连不通是正常的。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from appproxy import gui, icons, profiles, settings  # noqa: E402
from appproxy.models import Line, Node  # noqa: E402
from appproxy.registry import Registry  # noqa: E402

DEMO_HOME = Path(os.environ.get("APPPROXY_DEMO_HOME") or r"D:\AppProxy-demo")

# 文档保留地址（RFC 5737），永远不会和真实服务器撞上
IPS = ["198.51.100.%d" % i for i in range(10, 28)] + \
      ["203.0.113.%d" % i for i in range(10, 16)]
REGIONS = ["US-LA", "US-SEA", "JP-TYO", "SG-SIN", "HK-HKG", "DE-FRA",
           "UK-LON", "TW-TPE", "KR-SEL", "CA-TOR"]


def _nodes(count: int, prefix: str) -> list[Node]:
    out = []
    for i in range(count):
        out.append(Node(
            scheme="vless",
            address=IPS[i % len(IPS)],
            port=443,
            secret="00000000-0000-0000-0000-%012d" % i,
            remark="%s %02d" % (REGIONS[i % len(REGIONS)], i + 1),
            network="ws",
            security="tls",
            sni="%s.example.com" % prefix,
            host="%s.example.com" % prefix,
            path="/ws",
        ))
    return out


def main() -> int:
    home = DEMO_HOME
    os.environ["APPPROXY_HOME"] = str(home)
    settings.ensure_dirs()

    reg = Registry()
    reg.lines = [
        Line(id="airport", name="示例机场",
             sub_url="https://sub.example.com/api/v1/client/subscribe?token=DEMO",
             mode="auto", engine="singbox"),
        Line(id="backup", name="备用线路",
             sub_url="https://mirror.example.net/api/v1/client/subscribe?token=DEMO",
             mode="auto", engine="singbox"),
    ]
    # 端口走真实的随机分配（截图里必须是随机号，不能又是 10801 那种祖传端口，
    # 否则文档和程序实际行为对不上）
    for line in reg.lines:
        line.http_port, line.socks_port = reg.alloc_ports()
    reg.save()
    reg.save_nodes(reg.lines[0], _nodes(18, "edge"), {"profile-title": "示例机场"})
    reg.save_nodes(reg.lines[1], _nodes(6, "mirror"), {"profile-title": "备用线路"})
    # 订阅状态也造一份：截图里那一行「上次更新 x 小时前 · 每 12 小时…」
    # 是自动更新功能唯一的可见证据，空着等于没做
    now_ts = int(time.time())
    for line, hours in zip(reg.lines, (2, 5)):
        line.sub_ok_at = line.sub_checked_at = now_ts - hours * 3600
    reg.save()

    chrome = gui.find_browser()
    chrome_exe = str(chrome) if chrome else r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    telegram_exe = r"C:\Users\%USERNAME%\AppData\Roaming\Telegram Desktop\Telegram.exe"

    store = profiles.ProfileStore()
    store.profiles = [
        profiles.AppProfile(id="chrome-airport", name="Chrome·示例机场",
                            exe=chrome_exe, line_id="airport", url="https://example.com"),
        profiles.AppProfile(id="chrome-backup", name="Chrome·备用线路",
                            exe=chrome_exe, line_id="backup"),
        profiles.AppProfile(id="chrome-direct", name="Chrome·直连", exe=chrome_exe),
        profiles.AppProfile(id="telegram", name="Telegram（需代理）",
                            exe=telegram_exe, line_id="airport"),
    ]
    store.save()

    if icons.available():
        for p in store.profiles:
            icons.render(p)

    # 写几行演示日志：截图里「最近日志」那块空着不太好看，
    # 也顺便让读者一眼看懂「点启动」背后发生了什么。
    now = time.time()
    p1, p2 = reg.lines[0].http_port, reg.lines[1].http_port
    demo_log = [
        ("内核已启动  pid=18420  端口=%d  日志=%s\\logs\\sing-box-airport.log" % (p1, home),
         "内核已启动（端口 %d）" % p1),
        ("启动  chrome-airport：\"%s\" --user-data-dir=%s\\apps\\chrome-airport\\data "
         "--no-first-run --proxy-server=http://127.0.0.1:%d" % (chrome_exe, home, p1),
         "启动 chrome-airport"),
        ("chrome-airport 已在运行（端口 %d）" % p1, None),
        ("内核已启动  pid=19004  端口=%d" % p2, "内核已启动（端口 %d）" % p2),
        ("启动  chrome-backup：\"%s\" --proxy-server=http://127.0.0.1:%d" % (chrome_exe, p2),
         "启动 chrome-backup"),
    ]
    log_path = settings.logs_dir() / "launcher.log"
    rows = []
    for i, (line, _) in enumerate(demo_log):
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - (len(demo_log) - i) * 7))
        rows.append("[%s] %s" % (ts, line))
    log_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    print("演示数据已写入：", home)
    for line in reg.lines:
        nodes, _ = reg.load_nodes(line)
        print("  线路 %-8s %-6s 端口 %d  节点 %d" % (line.id, line.name, line.http_port, len(nodes)))
    print("  档案：", "、".join(p.name for p in store.profiles))
    return 0


if __name__ == "__main__":
    sys.exit(main())

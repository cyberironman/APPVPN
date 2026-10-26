# -*- coding: utf-8 -*-
"""节点测速：一次拉起临时内核，给每个节点分一个独立本地入口，并发打目标站。

为什么要这么绕：
    节点快不快只能靠"真的发一次请求"知道；内核自带的探活只针对已上线的线路，
    没法给"还没启用的节点"排序。这里复用本机已验证的做法：
    一个临时内核进程 + 每节点一个 inbound + 按 inbound 路由到对应 outbound。

xray 与 sing-box 都支持这套做法，只是配置字段不同，故 engine 参数区分。
"""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from . import settings
from .models import PRIVATE_CIDRS, Node

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def _reserve_ports(count: int) -> tuple[list[int], list[socket.socket]]:
    ports: list[int] = []
    socks: list[socket.socket] = []
    try:
        for _ in range(count):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", 0))
            s.settimeout(1)
            ports.append(s.getsockname()[1])
            socks.append(s)
        return ports, socks
    except Exception:
        for s in socks:
            s.close()
        raise


def _probe_config_xray(batch: list[Node], ports: list[int]) -> dict:
    inbounds, rules = [], []
    for i, node in enumerate(batch):
        inbounds.append({"tag": f"in{i}", "listen": "127.0.0.1", "port": ports[i],
                         "protocol": "http", "settings": {"timeout": 0}})
        rules.append({"type": "field", "inboundTag": [f"in{i}"], "outboundTag": node.tag})
    return {
        "log": {"loglevel": "error", "access": "none"},
        "inbounds": inbounds,
        "outbounds": [n.outbound() for n in batch] + [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {"domainStrategy": "AsIs",
                    "rules": rules + [{"type": "field", "outboundTag": "direct",
                                       "ip": list(PRIVATE_CIDRS)}]},
    }


def _probe_config_sing(batch: list[Node], ports: list[int]) -> dict:
    from . import singconf

    inbounds, rules = [], []
    for i, node in enumerate(batch):
        inbounds.append({"type": "mixed", "tag": f"in{i}", "listen": "127.0.0.1",
                         "listen_port": ports[i]})
        rules.append({"inbound": [f"in{i}"], "action": "route", "outbound": node.tag})
    return {
        "log": {"level": "error"},
        "inbounds": inbounds,
        "outbounds": [singconf.outbound(n) for n in batch] + [
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"},
        ],
        "route": {"rules": rules, "final": "direct"},
    }


def measure(nodes: list[Node], binary: Path, target: str | None = None, *,
            timeout: float = 8.0, batch_size: int = 24,
            engine: str = "xray",
            progress: "Callable[[int, int], None] | None" = None) -> dict[str, int | None]:
    """返回 {Node.uid: 毫秒 或 None(不通)}。

    progress(done, total) 每测完一批回调一次，给界面显示进度用。
    """
    target = target or settings.DEFAULT_HEALTH_URL
    binary = Path(binary)
    if not binary.is_file():
        raise RuntimeError(f"找不到内核：{binary}")

    result: dict[str, int | None] = {n.uid: None for n in nodes}
    for start in range(0, len(nodes), batch_size):
        batch = nodes[start:start + batch_size]
        _measure_batch(batch, binary, target, timeout, result, engine)
        if progress is not None:
            try:
                progress(min(start + batch_size, len(nodes)), len(nodes))
            except Exception:  # noqa: BLE001  进度回调出错不该毁掉测速
                pass
    return result


def _measure_batch(batch: list[Node], binary: Path, target: str, timeout: float,
                   result: dict[str, int | None], engine: str) -> None:
    ports, socks = _reserve_ports(len(batch))
    cfg = (_probe_config_sing if (engine or "").lower().startswith("sing")
           else _probe_config_xray)(batch, ports)
    cfg_path = Path(tempfile.gettempdir()) / f"appproxy-ping-{os.getpid()}-{int(time.time())}.json"
    with io.open(cfg_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False)

    for s in socks:      # 必须先释放，否则内核绑不上
        s.close()

    proc = subprocess.Popen([str(binary), "run", "-c", str(cfg_path)],
                            cwd=str(binary.parent),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW)
    lock = threading.Lock()

    def one(node: Node, port: int) -> None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}))
        req = urllib.request.Request(target, headers={"User-Agent": BROWSER_UA})
        t0 = time.time()
        try:
            with opener.open(req, timeout=timeout) as r:
                r.read(64)
                ms = int((time.time() - t0) * 1000)
                ok = r.status in (200, 204)
        except Exception:  # noqa: BLE001
            ok, ms = False, None
        if ok:
            with lock:
                result[node.uid] = ms

    try:
        for port in ports:
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    socket.create_connection(("127.0.0.1", port), 0.25).close()
                    break
                except OSError:
                    time.sleep(0.2)
        threads = [threading.Thread(target=one, args=(n, ports[i]), daemon=True)
                   for i, n in enumerate(batch)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout + 3)
    finally:
        try:
            proc.terminate()
            proc.wait(4)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            cfg_path.unlink()
        except OSError:
            pass


def rank(nodes: list[Node], result: dict[str, int | None]) -> list[tuple[Node, int | None]]:
    """按毫秒升序，不通的排最后。"""
    return sorted(((n, result.get(n.uid)) for n in nodes),
                  key=lambda p: (p[1] is None, p[1] or 10 ** 9))

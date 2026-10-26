# -*- coding: utf-8 -*-
"""新用户第一次用：把「用户自己的代理」接进来。

为什么要有这一步
    这个软件**不带任何代理服务**。它的价值是「你已经有代理，我帮你按软件分」。
    所以第一次打开必须问清楚：你的代理在哪？三种最常见的答案都收：

        A. 订阅链接       https://...  （机场/自建，最常见）
        B. 分享链接       vless:// vmess:// trojan:// ss://  （单个节点）
        C. 本地端口       v2rayN / Clash 已经在跑，填它的 http 或 socks 端口

三种都落到同一个模型：一条 `Line`（线路）+ 它的节点快照。之后所有功能
（分应用、TUN、系统代理）都按同一条路走，不需要为「本地端口」写特例。

这一步**不绑定任何服务商**，也不收集任何凭据；token 只留在本机 lines.json 里。
"""
from __future__ import annotations

import re
from typing import Any

from . import gui_support, pool, subscription, xrayconf
from .models import Line, Node
from .registry import Registry

ALLOWED_LOCAL = ("http", "socks")


class SetupError(RuntimeError):
    pass


def _make_line(reg: Registry, name: str, *, engine: str = "singbox") -> Line:
    http_port, socks_port = reg.alloc_ports()
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "line"
    lid = base
    n = 2
    while reg.get(lid):
        lid = f"{base}-{n}"
        n += 1
    return Line(id=lid, name=name, http_port=http_port, socks_port=socks_port,
                engine=engine)


def _activate(reg: Registry, line: Line, nodes: list[Node], meta: dict | None) -> str:
    """存节点 → 生成配置 → 把内核拉起来（用户等约 1 秒）。"""
    reg.save_nodes(line, nodes, meta or {})
    cfg = gui_support.build_config(line, nodes, None)
    xrayconf.write(cfg, line.config_path())
    reg.upsert(line)
    reg.save()
    try:
        pool.ensure_core(reg, line)
        tail = f"内核已启动，本地端口 {line.http_port}"
    except Exception as e:  # noqa: BLE001  起不来也要把线路留下，用户可再点启动
        tail = f"线路已建好，但内核没起来（{type(e).__name__}: {e}）"
    return f"已添加线路「{line.name}」（{len(nodes)} 个节点）；{tail}"


def from_subscription(url: str) -> str:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise SetupError("订阅地址要以 http:// 或 https:// 开头")
    reg = Registry().load()
    try:
        nodes, meta = subscription.load(url)
    except Exception as e:  # noqa: BLE001
        raise SetupError(f"拉订阅失败：{type(e).__name__}: {e}") from e
    if not nodes:
        raise SetupError("订阅里没解析出节点。确认是订阅地址（不是网页），或者换 C. 本地端口试试")

    # 线路名用订阅域名，别把整条 URL（含 token）暴露在界面上
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").replace("www.", "")
    name = host.split(".")[0][:12] or "我的代理"
    line = _make_line(reg, name)
    line.sub_url = url
    nodes = nodes[:200]
    return _activate(reg, line, nodes, meta if isinstance(meta, dict) else {})


def from_uri(uri: str) -> str:
    uri = (uri or "").strip()
    if not uri:
        raise SetupError("把节点分享链接粘进来")
    node = subscription.parse_uri(uri)
    if node is None or not node.healthy:
        raise SetupError("这条链接没认出来。支持 vless:// vmess:// trojan:// ss://")
    reg = Registry().load()
    line = _make_line(reg, node.remark[:12] or node.region)
    line.mode = "single"
    line.node_key = node.uid
    return _activate(reg, line, [node], {"source": "uri"})


def from_local(protocol: str, port: int, host: str = "127.0.0.1") -> str:
    """用户本机已经跑着 v2rayN / Clash，只要把端口告诉我们就行。"""
    proto = (protocol or "").strip().lower()
    if proto not in ALLOWED_LOCAL:
        raise SetupError("本地代理只支持 http 或 socks5")
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise SetupError("端口要填数字") from None
    if not 0 < port < 65536:
        raise SetupError("端口超出范围")
    host = (host or "127.0.0.1").strip()

    from . import coreproc
    if not coreproc.port_open(port):
        raise SetupError(f"{host}:{port} 上没有程序在监听。先把你的代理客户端打开，再点确定")

    node = Node(scheme=proto, address=host, port=port,
                remark=f"本地 {proto.upper()} :{port}")
    reg = Registry().load()
    line = _make_line(reg, f"本地 {proto.upper()} {port}")
    line.mode = "single"
    line.node_key = node.uid
    return _activate(reg, line, [node], {"source": "local"})


def describe() -> dict[str, Any]:
    """给界面判断「要不要弹引导」。"""
    reg = Registry().load()
    return {
        "lines": len(reg.lines),
        "needs_setup": not reg.lines,
        "hint": "" if reg.lines else "还没有代理线路 —— 先告诉我在哪，才能开始分应用",
    }

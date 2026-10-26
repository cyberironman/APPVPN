# -*- coding: utf-8 -*-
"""由节点列表生成 xray 运行配置。

两种模式：
    auto   —— 全部节点都做成 outbound，observatory 主动探活，balancer leastPing 自动挑最快；
              （订阅按天轮换时，手选单节点必然踩到慢节点，所以默认用这个）
    single —— 只挂选中的那一个节点，行为可预期，适合"我就要这条出口"的场景。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import settings
from .models import PRIVATE_CIDRS, Line, Node


class ConfigError(RuntimeError):
    pass


def _inbounds(line: Line) -> list[dict[str, Any]]:
    inbounds: list[dict[str, Any]] = []
    if line.http_port:
        inbounds.append({"tag": "http-in", "listen": "127.0.0.1", "port": int(line.http_port),
                         "protocol": "http", "settings": {"timeout": 0}})
    if line.socks_port:
        inbounds.append({"tag": "socks-in", "listen": "127.0.0.1", "port": int(line.socks_port),
                         "protocol": "socks",
                         "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"}})
    if not inbounds:
        raise ConfigError("线路没有配置任何本地端口（http_port / socks_port 全为 0）")
    return inbounds


def pick_node(nodes: list[Node], node_key: str) -> Node:
    if not nodes:
        raise ConfigError("节点列表为空，先更新订阅")
    if not node_key:
        raise ConfigError("single 模式必须指定节点")
    for n in nodes:
        if n.uid == node_key or n.tag == node_key:
            return n
    raise ConfigError(f"找不到节点 {node_key}（订阅可能已更新，请重新选择）")


def build(line: Line, nodes: list[Node], *, log_dir: Path | None = None) -> dict[str, Any]:
    """返回完整 xray 配置字典（不写盘）。"""
    if not nodes:
        raise ConfigError("节点列表为空，先更新订阅")
    log_dir = log_dir or settings.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    inbounds = _inbounds(line)
    outbounds = [n.outbound() for n in nodes]
    outbounds += [{"tag": "direct", "protocol": "freedom"},
                  {"tag": "block", "protocol": "blackhole"}]

    routing: dict[str, Any] = {
        "domainStrategy": "AsIs",
        "rules": [
            {"type": "field", "outboundTag": "direct", "ip": list(PRIVATE_CIDRS)},
            {"type": "field", "outboundTag": "block",
             "domain": ["domain:localhost"]},
        ],
    }

    cfg: dict[str, Any] = {
        "log": {"loglevel": "warning", "access": "none",
                "error": str(log_dir / f"xray-{line.id}.log")},
        "inbounds": inbounds,
        "outbounds": outbounds,
    }

    if line.mode == "single":
        node = pick_node(nodes, line.node_key)
        routing["rules"].append({"type": "field", "outboundTag": node.tag,
                                 "network": "tcp,udp"})
    else:
        cfg["observatory"] = {
            "subjectSelector": ["n-"],
            "probeUrl": line.health_url or settings.DEFAULT_HEALTH_URL,
            "probeInterval": "30s",
            "enableConcurrency": True,
        }
        routing["balancers"] = [{"tag": "auto", "selector": ["n-"],
                                 "strategy": {"type": "leastPing"}}]
        routing["rules"].append({"type": "field", "balancerTag": "auto",
                                 "network": "tcp,udp"})

    cfg["routing"] = routing
    return cfg


def write(cfg: dict[str, Any], path: Path) -> Path:
    """xray 不认带 BOM 的 JSON，这里显式 UTF-8 无 BOM + LF。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path

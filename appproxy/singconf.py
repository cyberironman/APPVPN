# -*- coding: utf-8 -*-
"""sing-box 配置生成（主引擎）。

为什么选 sing-box 而不是自己写协议层：
    * 开源（GPL-3.0-or-later），单文件 exe，无依赖；
    * 原生支持 `process_name` 路由规则 —— 这是「分应用分代理」的关键：
      同一台机器、甚至同一个本地端口，可以按进程把流量分给不同线路；
    * 原生支持 urltest 自动择优、mixed 入口（http+socks 同端口）。

本模块只负责"把我们的线路模型翻译成 sing-box 配置"，不启动进程。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from . import settings
from .models import Line, Node


class ConfigError(RuntimeError):
    pass


# --------------------------------------------------------------------------- 节点 -> outbound
def _tls(node: Node) -> dict[str, Any] | None:
    if node.security not in ("tls", "reality"):
        return None
    sni = node.sni or node.host or node.address
    tls: dict[str, Any] = {"enabled": True, "server_name": sni,
                           "utls": {"enabled": True, "fingerprint": node.fp or "chrome"}}
    if node.alpn:
        tls["alpn"] = list(node.alpn)
    if node.security == "reality":
        tls["reality"] = {"enabled": True, "public_key": node.pbk, "short_id": node.sid}
    return tls


def _transport(node: Node) -> dict[str, Any] | None:
    if node.network == "ws":
        t: dict[str, Any] = {"type": "ws", "path": node.path or "/"}
        host = node.host or node.sni
        if host:
            t["headers"] = {"Host": host}
        return t
    if node.network == "grpc":
        return {"type": "grpc", "service_name": node.service_name or node.path.strip("/")}
    if node.network == "httpupgrade":
        t = {"type": "httpupgrade", "path": node.path or "/"}
        if node.host:
            t["host"] = node.host
        return t
    if node.network == "h2":
        t = {"type": "http", "path": node.path or "/"}
        if node.host:
            t["host"] = [node.host]
        return t
    return None


def outbound(node: Node, tag: str | None = None) -> dict[str, Any]:
    tag = tag or node.tag
    ob: dict[str, Any] = {"tag": tag, "server": node.address, "server_port": int(node.port)}
    if node.scheme == "vless":
        ob["type"] = "vless"
        ob["uuid"] = node.secret
        if node.flow:
            ob["flow"] = node.flow
    elif node.scheme == "vmess":
        ob["type"] = "vmess"
        ob["uuid"] = node.secret
        ob["security"] = node.scy or "auto"
        ob["alter_id"] = int(node.aid or 0)
    elif node.scheme == "trojan":
        ob["type"] = "trojan"
        ob["password"] = node.secret
    elif node.scheme == "ss":
        ob["type"] = "shadowsocks"
        ob["method"] = node.method
        ob["password"] = node.secret
    elif node.scheme in ("socks", "http"):
        # 本地已有代理客户端（v2rayN / Clash 之类）时用这个：
        # 我们把流量原样转给它，用户什么都不用改。本地端口不做认证。
        ob["type"] = node.scheme
        if node.scheme == "socks":
            ob["version"] = "5"
    else:
        raise ConfigError(f"sing-box 不支持这个协议：{node.scheme}")

    tls = _tls(node)
    if tls:
        ob["tls"] = tls
    tr = _transport(node)
    if tr:
        ob["transport"] = tr
    return ob


# --------------------------------------------------------------------------- 单线路
def build(line: Line, nodes: list[Node], *, per_app: dict[str, str] | None = None,
          usable: set[str] | None = None, log_dir: Path | None = None) -> dict[str, Any]:
    """一条线路的独立配置：这条端口上的一切流量都归这条线路。

    per_app: {"chrome.exe": "line-id"} —— 预留，用于「共用端口 + 按进程分流」。
    """
    if not nodes:
        raise ConfigError("节点列表为空，先更新订阅")
    if usable:
        kept = [n for n in nodes if n.uid in usable]
        if kept:
            nodes = kept
    log_dir = log_dir or settings.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    outs = [outbound(n) for n in nodes]
    tags = [o["tag"] for o in outs]
    if not line.http_port and not line.socks_port:
        raise ConfigError("线路没有配置本地端口")

    inbounds: list[dict[str, Any]] = []
    if line.http_port:
        inbounds.append({"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1",
                         "listen_port": int(line.http_port)})
    if line.socks_port:
        inbounds.append({"type": "socks", "tag": "socks-in", "listen": "127.0.0.1",
                         "listen_port": int(line.socks_port)})

    route: dict[str, Any] = {"auto_detect_interface": True, "rules": [], "final": "direct"}

    if line.mode == "single":
        node = _pick(nodes, line.node_key)
        route["final"] = node.tag
    else:
        outs.append({"type": "urltest", "tag": "auto", "outbounds": tags,
                     "url": line.health_url or settings.DEFAULT_HEALTH_URL,
                     "interval": "1m", "tolerance": 50})
        route["final"] = "auto"

    cfg = {
        "log": {"level": "warn", "timestamp": True,
                "output": str(log_dir / f"sing-{line.id}.log")},
        "inbounds": inbounds,
        "outbounds": outs + [{"type": "direct", "tag": "direct"},
                             {"type": "block", "tag": "block"}],
        "route": route,
    }
    if per_app:
        cfg["route"]["rules"].extend(process_rules(per_app))
    return cfg


def _pick(nodes: list[Node], key: str) -> Node:
    for n in nodes:
        if n.uid == key or n.tag == key:
            return n
    raise ConfigError(f"找不到节点 {key}（订阅可能已更新，请重新选择）")


# --------------------------------------------------------------------------- 分应用调度
def process_rules(mapping: dict[str, str]) -> list[dict[str, Any]]:
    """{"chrome.exe": "auto-alpha"} -> sing-box 路由规则（按进程名出网）。"""
    rules: list[dict[str, Any]] = []
    for proc, outbound_tag in mapping.items():
        if not proc or not outbound_tag:
            continue
        rules.append({"process_name": [proc], "action": "route", "outbound": outbound_tag})
    return rules


def build_dispatcher(groups: dict[str, list[Node]], port: int, app_rules: dict[str, str], *,
                     health_url: str | None = None, log_dir: Path | None = None,
                     default_group: str | None = None) -> dict[str, Any]:
    """分应用调度配置：**一个本地端口**，按进程把流量分给不同线路。

    groups:    {"alpha": [节点...], "line-b": [节点...]}
    app_rules: {"chrome.exe": "auto-alpha", "Telegram.exe": "auto-line-b"}
    未匹配进程走 default_group（默认 direct，避免误接管别的软件）。
    """
    if not groups:
        raise ConfigError("没有任何线路可用")
    log_dir = log_dir or settings.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    outs: list[dict[str, Any]] = []
    seen: set[str] = set()
    urltest_tags: dict[str, str] = {}
    for line_id, nodes in groups.items():
        tags = []
        for n in nodes:
            if n.tag in seen:
                tags.append(n.tag)
                continue
            seen.add(n.tag)
            outs.append(outbound(n))
            tags.append(n.tag)
        if not tags:
            continue
        tag = f"auto-{line_id}"
        urltest_tags[line_id] = tag
        outs.append({"type": "urltest", "tag": tag, "outbounds": tags,
                     "url": health_url or settings.DEFAULT_HEALTH_URL,
                     "interval": "3m", "tolerance": 50})

    missing = {v for v in app_rules.values() if v not in urltest_tags.values()}
    if missing:
        raise ConfigError(f"应用规则引用了不存在的线路：{sorted(missing)}")

    final = "direct"
    if default_group:
        final = urltest_tags.get(default_group, "direct")

    return {
        "log": {"level": "warn", "timestamp": True,
                "output": str(log_dir / "sing-dispatcher.log")},
        "inbounds": [
            {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1",
             "listen_port": int(port)},
        ],
        "outbounds": outs + [{"type": "direct", "tag": "direct"},
                             {"type": "block", "tag": "block"}],
        "route": {"auto_detect_interface": True,
                  "rules": [{"action": "sniff"}] + process_rules(app_rules),
                  "final": final},
    }


def write(cfg: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path


def clone(cfg: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(cfg)

# -*- coding: utf-8 -*-
"""TUN 模式配置（虚拟网卡接管整机流量，再按进程分流）。

和「给程序塞代理参数」的区别
    塞参数（现在的主用法）：程序认 HTTP_PROXY / --proxy-server 才生效。
        好处是零权限、不碰网卡；坏处是很多程序不认（游戏、部分 Electron 应用）。
    TUN（本模块）：在系统里建一块虚拟网卡，把整机流量先接住，再按**进程名**分流。
        认不认代理参数都跑不掉。代价是要管理员权限，个别反作弊会拦。

分流原则（用户 2026-10-26 定下的）
    * 建过档案的软件 → 走它自己那条线路；
    * 没建档案的软件 → **一律直连**（final = direct），不越权接管别人的流量；
    * 断线保护（kill-switch）暂不做，避免内核一抖整机没网。

只生成配置，不启动进程 —— 方便单测和 `sing-box check` 校验。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import settings
from .models import Node
from .singconf import ConfigError, outbound, process_rules

# 国内可达的公共 DNS（直连用）；走代理的那条用加密 DNS，避免被劫持
LOCAL_DNS = "223.5.5.5"
REMOTE_DNS = "1.1.1.1"
TUN_ADDRESS = "172.19.0.1/30"
TUN_IFACE = "AppProxy"


def build(groups: dict[str, list[Node]], app_rules: dict[str, str], *,
          mtu: int = 9000, iface: str = TUN_IFACE, stack: str = "mixed",
          health_url: str | None = None, log_dir: Path | None = None,
          log_name: str = "sing-tun.log") -> dict[str, Any]:
    """groups: {"alpha": [节点...]}；app_rules: {"chrome.exe": "auto-alpha"}。

    app_rules 的 outbound 必须是 `auto-<线路 id>`（和 build_dispatcher 一致）。
    """
    if not groups:
        raise ConfigError("没有任何线路可用，先添加代理")
    log_dir = log_dir or settings.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    outs: list[dict[str, Any]] = []
    seen: set[str] = set()
    auto_tags: dict[str, str] = {}
    for line_id, nodes in groups.items():
        tags: list[str] = []
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
        auto_tags[line_id] = tag
        outs.append({"type": "urltest", "tag": tag, "outbounds": tags,
                     "url": health_url or settings.DEFAULT_HEALTH_URL,
                     "interval": "1m", "tolerance": 50})

    if not auto_tags:
        raise ConfigError("线路里没有可用节点，先更新订阅")

    bad = {v for v in app_rules.values() if v not in auto_tags.values()}
    if bad:
        raise ConfigError(f"应用规则引用了不存在的线路：{sorted(bad)}")

    # DNS：直连走国内 DNS；要走代理的进程用加密 DNS 并经那条线路出去，
    # 否则解析结果照样被污染，代理了也打不开。
    first_tag = next(iter(auto_tags.values()))
    dns = {
        "servers": [
            {"type": "udp", "tag": "local-dns", "server": LOCAL_DNS},
            {"type": "tls", "tag": "remote-dns", "server": REMOTE_DNS,
             "detour": first_tag},
        ],
        "rules": [],
        "final": "local-dns",
        "strategy": "prefer_ipv4",
    }
    proxied = sorted({p for p in app_rules if p})
    if proxied:
        dns["rules"].append({"process_name": proxied, "server": "remote-dns"})

    rules: list[dict[str, Any]] = [
        {"action": "sniff"},
        {"protocol": "dns", "action": "hijack-dns"},
        # 局域网与本机地址别往代理里塞
        {"ip_is_private": True, "action": "route", "outbound": "direct"},
    ]
    rules += process_rules(app_rules)

    return {
        "log": {"level": "warn", "timestamp": True,
                "output": str(log_dir / log_name)},
        "dns": dns,
        "inbounds": [{
            "type": "tun",
            "tag": "tun-in",
            "interface_name": iface,
            "address": [TUN_ADDRESS],
            "mtu": int(mtu),
            "auto_route": True,
            "strict_route": True,
            "stack": stack,
        }],
        "outbounds": outs + [{"type": "direct", "tag": "direct"},
                             {"type": "block", "tag": "block"}],
        "route": {
            "auto_detect_interface": True,
            "rules": rules,
            # sing-box 1.12+ 必须显式给一个解析器，否则 check 直接报错：
            # 只用来解析 outbound 里写成域名的服务器地址，跟嗅探出来的域名无关。
            "default_domain_resolver": "local-dns",
            # 没建档案的软件一律直连 —— 这是「不越权接管」的底线
            "final": "direct",
        },
    }


def describe(cfg: dict[str, Any]) -> str:
    """一句话说清这条 TUN 配置干什么（日志/界面用）。"""
    rules = (cfg.get("route") or {}).get("rules") or []
    procs = [p for r in rules for p in (r.get("process_name") or [])]
    return f"{len(procs)} 个进程走代理，其余直连"

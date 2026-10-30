# -*- coding: utf-8 -*-
"""线路注册表 + 节点缓存（纯本地 JSON，无数据库依赖）。

lines.json   线路定义（订阅地址、端口、模式、选中节点）
nodes-<id>.json  该线路最近一次拉到的节点快照（离线可用、切换节点不用重拉）
"""
from __future__ import annotations

import json
import random
import re
import socket
import time
from pathlib import Path
from typing import Any

from . import settings
from .models import Line, Node

SCHEMA_VERSION = 1


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _port_free(port: int) -> bool:
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", int(port)))
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- 端口分配
# 为什么随机：
#   以前是从 10840 起按 +2 递增，于是**每个装了这个软件的人**都拿到同一批端口
#   （10840/10841、10842/10843…）。只要机器上还有别的程序占了这一段，或者用户
#   装了两份、或者跟别的代理工具撞车，就是「端口被占 → 内核起不来 → 那个软件
#   没有网络」。随机散开以后，两台机器撞到同一个端口的概率比中彩票还低。
#
# 区间为什么是 10000–29998：
#   * 低于 1024 要管理员权限（本项目主用法是零权限）；
#   * Windows 默认动态端口从 49152 起，落在那里面等于跟系统临时连接抢端口；
#   * 10800–10999 是各种代理工具的祖传段（v2rayN 10808/10809、clash 7890、
#     我们自己的另一个项目 10878 / 10888 / 10948…），整段避开。
PORT_MIN = 10000
PORT_MAX = 29998          # 偶数上限，保证 port+1 也不越界

_AVOID_RANGES: tuple[tuple[int, int], ...] = (
    (1080, 1081),          # 最常见的单端口代理
    (7890, 7899),          # Clash 系
    (10800, 10999),        # v2rayN / v2rayN 衍生 / 本项目其它组件
    (18488, 18489),        # 本机既有服务已占的一段
)

_excluded_cache: tuple[float, list[tuple[int, int]]] | None = None
_EXCLUDED_TTL = 300.0      # 秒；保留段不常变，没必要每次分端口都去问系统


def _excluded_ranges() -> list[tuple[int, int]]:
    """系统保留 / Hyper-V 排除的 TCP 端口段（`WSAEACCES` 的那些）。

    拿不到就返回空表 —— 宁可少一层保护，也不能因为一条诊断命令失败就
    让用户加不了线路。
    """
    global _excluded_cache
    now = time.time()
    if _excluded_cache and now - _excluded_cache[0] < _EXCLUDED_TTL:
        return _excluded_cache[1]
    ranges: list[tuple[int, int]] = []
    try:
        import subprocess
        out = subprocess.run(
            ["netsh", "int", "ipv4", "show", "excludedportrange", "protocol=tcp"],
            capture_output=True, text=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        for line in out.splitlines():
            m = re.match(r"^\s*(\d+)\s+(\d+)\s*$", line)
            if m:
                ranges.append((int(m.group(1)), int(m.group(2))))
    except Exception:            # noqa: BLE001  没有 netsh / 被策略拦，都不影响分配
        ranges = []
    _excluded_cache = (now, ranges)
    return ranges


def _in_ranges(port: int, ranges) -> bool:
    return any(lo <= port <= hi for lo, hi in ranges)


class Registry:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else settings.registry_path()
        self.defaults: dict[str, Any] = {"xray": "", "health_url": settings.DEFAULT_HEALTH_URL}
        self.lines: list[Line] = []

    # ---------------------------------------------------------------- 读写
    def load(self) -> "Registry":
        raw = _read_json(self.path)
        self.defaults.update({k: v for k, v in (raw.get("defaults") or {}).items() if v})
        self.lines = []
        for item in raw.get("lines") or []:
            try:
                self.lines.append(Line.from_dict(item))
            except (TypeError, ValueError):
                continue
        return self

    def save(self) -> Path:
        _write_json(self.path, {
            "version": SCHEMA_VERSION,
            "defaults": self.defaults,
            "lines": [l.to_dict() for l in self.lines],
        })
        return self.path

    # ---------------------------------------------------------------- 线路
    def get(self, line_id: str) -> Line | None:
        return next((l for l in self.lines if l.id == line_id), None)

    def require(self, line_id: str) -> Line:
        line = self.get(line_id)
        if line is None:
            raise KeyError(f"没有这条线路：{line_id}（先 add）")
        return line

    def upsert(self, line: Line) -> Line:
        for i, old in enumerate(self.lines):
            if old.id == line.id:
                self.lines[i] = line
                return line
        self.lines.append(line)
        return line

    def remove(self, line_id: str) -> bool:
        before = len(self.lines)
        self.lines = [l for l in self.lines if l.id != line_id]
        return len(self.lines) != before

    def alloc_ports(self, base: int | None = None, *, rng: random.Random | None = None,
                    attempts: int = 240) -> tuple[int, int]:
        """分一对「http / socks」端口，默认**随机**落在 10000–29998。

        判据四层，缺一不可：
            1. 没被本机已有线路占用（同一台机器内不打架）；
            2. 没被系统保留段圈走（Hyper-V / WSL 那种 `WSAEACCES`）；
            3. 不在代理工具的祖传段里（10800–10999、7890… 撞车重灾区）；
            4. 真的 bind 得起来（此刻没别人在听）。
        连续撞墙 attempts 次后，退化成确定性扫描 —— 宁可给个靠前的端口，
        也不能让用户卡在「加不了线路」。
        """
        used = {l.http_port for l in self.lines} | {l.socks_port for l in self.lines}
        banned = list(_AVOID_RANGES) + _excluded_ranges()

        def ok(port: int) -> bool:
            return (port not in used and (port + 1) not in used
                    and not _in_ranges(port, banned) and not _in_ranges(port + 1, banned)
                    and _port_free(port) and _port_free(port + 1))

        if base is not None:                      # 排障/测试用的确定性模式
            for port in range(base, base + 400, 2):
                if ok(port):
                    return port, port + 1
            raise RuntimeError(f"{base} 起 400 个端口内找不到空闲端口对")

        pick = rng or random.SystemRandom()
        for _ in range(attempts):
            port = pick.randrange(PORT_MIN, PORT_MAX, 2)   # 只取偶数，成对分配
            if ok(port):
                return port, port + 1

        for port in range(PORT_MIN, PORT_MAX, 2):
            if ok(port):
                return port, port + 1
        raise RuntimeError("找不到空闲端口对")

    @property
    def xray(self) -> Path | None:
        return settings.find_xray(self.defaults.get("xray") or None)

    def binary(self, engine: str = "singbox") -> Path | None:
        key = "singbox" if (engine or "").lower().startswith("sing") else "xray"
        return settings.find_engine(key, self.defaults.get(key) or None)

    # ---------------------------------------------------------------- 节点缓存
    def save_nodes(self, line: Line, nodes: list[Node], meta: dict | None = None) -> Path:
        path = line.nodes_path()
        _write_json(path, {
            "updated": int(time.time()),
            "count": len(nodes),
            "meta": meta or {},
            "nodes": [n.to_dict() for n in nodes],
        })
        return path

    def load_nodes(self, line: Line) -> tuple[list[Node], dict]:
        raw = _read_json(line.nodes_path())
        nodes = [Node.from_dict(d) for d in raw.get("nodes") or []]
        meta = {"updated": raw.get("updated", 0), "count": raw.get("count", len(nodes)),
                **(raw.get("meta") or {})}
        return nodes, meta

# -*- coding: utf-8 -*-
"""线路注册表 + 节点缓存（纯本地 JSON，无数据库依赖）。

lines.json   线路定义（订阅地址、端口、模式、选中节点）
nodes-<id>.json  该线路最近一次拉到的节点快照（离线可用、切换节点不用重拉）
"""
from __future__ import annotations

import json
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

    def alloc_ports(self, base: int = 10840) -> tuple[int, int]:
        """在 base 附近找一对空闲且未被其他线路占用的端口。"""
        used = {l.http_port for l in self.lines} | {l.socks_port for l in self.lines}
        port = base
        while port < base + 400:
            if port not in used and _port_free(port) and _port_free(port + 1) \
                    and (port + 1) not in used:
                return port, port + 1
            port += 2
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

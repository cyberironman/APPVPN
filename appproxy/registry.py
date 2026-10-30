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
# 区间为什么是 15002–19998（2026-10-30 二次收窄）：
#   * 低于 1024 要管理员权限（本项目主用法是零权限）；
#   * **本机 Windows 动态端口段被调成了 1024–15000**（`netsh int ipv4
#     show dynamicport tcp` 实测：启动 1024、端口数 13977）。出站连接会从这
#     一段里随机取本地端口，listen 端口落进去迟早撞 `bind: EADDRINUSE`。
#     所以下限必须压在 15000 以上 —— 这是本项目与动态段之间唯一的护栏。
#   * 49152 以上是 Windows 默认动态段的位置，保守整段避开；
#   * 10800–10999 是各种代理工具的祖传段（v2rayN 10808/10809、clash 7890、
#     我们自己的另一个项目 10878 / 10888 / 10948…），整段避开；
#   * **本机还有两个兄弟程序**（Codex 管家、Chrome 管家）。三段划分见下方
#     `_AVOID_RANGES` 上方的表，同时开也不会挑到同一个端口。
PORT_MIN = 15002
PORT_MAX = 19998          # 偶数上限，保证 port+1 也不越界


def _audit(msg: str) -> None:
    """端口变更写一份流水（logs/ports.log），出事能倒查哪天换了什么。"""
    try:
        d = settings.logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "ports.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass

# 本机三个代理项目的端口段（2026-10-30 定稿，**改一处必须同步另两处**）：
#     分应用代理管家 AppProxy    15002 – 19998
#     分资料代理 Chrome 管家     20000 – 39998
#     分应用代理 Codex 管家      40000 – 49148
# 三段首尾相接、互不重叠，全部落在「动态段之上、49152 之下」的安全区。
_AVOID_RANGES: tuple[tuple[int, int], ...] = (
    (1, 1023),             # 需要管理员权限
    (1080, 1081),          # 最常见的单端口代理
    (7890, 7899),          # Clash 系
    (1024, 15000),         # 本机动态端口段（出站连接会随机占用）
    (10800, 10999),        # v2rayN / v2rayN 衍生 / 本项目其它组件
    (18488, 18489),        # 本机既有服务已占的一段
    (49152, 65535),        # Windows 默认动态段，保守整段避开
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
        # 端口体检：老版本留下的 109xx 固定端口、被外部占用、两线重复，
        # 在这里一次性换掉（换完会 save，见 heal_ports）。
        try:
            self.heal_ports(save=True)
        except Exception as e:  # noqa: BLE001  体检失败不能挡住启动
            _audit(f"端口体检异常：{e}")
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

    # ---------------------------------------------------------------- 端口体检
    def _core_running_on(self, line: Line, port: int) -> bool:
        """这个端口是不是**本线路自己的内核**在听（是的话不能动它）。"""
        try:
            from . import coreproc
            if not coreproc.port_open(port):
                return False
            return bool(coreproc.pids_by_config(line.config_path()))
        except Exception:  # noqa: BLE001
            return False

    def heal_ports(self, *, save: bool = True) -> list[tuple[str, int, int]]:
        """启动时给已有线路做端口体检，把「不该用 / 用不了」的换掉。

        会重挑的四种情况（都实际踩到过）：
          1. 端口在 10800–10999 这类祖传段 —— 和 v2rayN 抢；
          2. 端口被别的程序占着 —— 内核起不来，表现是「这个软件没网」；
          3. 两条线路分了同一个端口 —— 后起的那条永远起不来；
          4. 端口落在随机区间之外（老版本留下的 10930/10940/10950）。

        自家内核正跑在端口上的**不动**，避免「体检把用户踢断网」；
        等那条线路停掉、下次启动时自然换过来。

        返回 [(线路 id, 旧端口, 新端口)]。
        """
        used: set[int] = set()
        changes: list[tuple[str, int, int]] = []
        for line in self.lines:
            p = int(line.http_port or 0)
            s = int(line.socks_port or 0)
            reason = ""
            if not p or not s:
                reason = "端口未分配"
            elif not (PORT_MIN <= p <= PORT_MAX - 1):
                reason = f"不在随机区间 {PORT_MIN}-{PORT_MAX}"
            elif _in_ranges(p, _AVOID_RANGES) or _in_ranges(p + 1, _AVOID_RANGES):
                if _in_ranges(p, ((1024, 15000),)):
                    reason = "落在系统动态端口段（1024-15000）"
                elif _in_ranges(p, ((10800, 10999), (7890, 7899), (1080, 1081))):
                    reason = "落在代理工具祖传段（108xx / 7890）"
                else:
                    reason = "落在系统保留 / 需管理员权限的段"
            elif p in used or s in used:
                reason = "与本机其它线路重复"
            elif not (_port_free(p) and _port_free(p + 1)):
                if self._core_running_on(line, p):
                    used.update((p, p + 1))
                    continue
                reason = "已被其他程序占用"
            if not reason:
                used.update((p, s or p + 1))
                continue
            try:
                np, ns = self.alloc_ports(exclude=used)
            except RuntimeError as e:      # 端口全满，先用着，至少别崩
                _audit(f"线路 {line.id} 端口体检失败：{e}（继续沿用 {p}）")
                if p:
                    used.update((p, p + 1))
                continue
            changes.append((line.id, p, np))
            line.http_port, line.socks_port = np, ns
            used.update((np, ns))
            _audit(f"线路 {line.id}（{line.name}）端口 {p or '未分配'} → {np}；原因：{reason}")
        if changes:
            for lid, old, new in changes:
                print(f"[端口] 线路 {lid}：{old or '未分配'} → {new}（随机分配，避免冲突）")
                self._drop_stale_config(lid, old)
            if save:
                self.save()
        return changes

    def _drop_stale_config(self, line_id: str, old_port: int = 0) -> None:
        """端口换了，编译好的内核配置就作废了 —— 必须删掉。

        这是 v0.6.4 埋下的坑：`heal_ports` 只改了 `lines.json` 里的端口，
        而 `runtime/<id>.json` 里 inbounds 还是旧端口。`ensure_core` 见配置已存在
        就直接拿它起内核，于是内核去听一个没人认的旧端口，界面表现成
        「25 秒内端口未就绪」，日志还是空的 —— 极难查。
        配置是派生文件，删了下次启动会按最新节点+端口重建。
        """
        line = self.get(line_id)
        if line is None:
            return
        if old_port and self._core_running_on(line, int(old_port)):
            return                      # 自家内核还在旧端口上跑，别动它的现场
        try:
            p = line.config_path()
            if p.exists():
                p.unlink()
                _audit(f"线路 {line_id} 端口已换，旧配置已作废（{p.name}）")
        except OSError:
            pass

    def alloc_ports(self, base: int | None = None, *, rng: random.Random | None = None,
                    attempts: int = 240, exclude: set[int] | None = None) -> tuple[int, int]:
        """分一对「http / socks」端口，默认**随机**落在 10000–19998。

        判据四层，缺一不可：
            1. 没被本机已有线路占用（同一台机器内不打架）；
            2. 没被系统保留段圈走（Hyper-V / WSL 那种 `WSAEACCES`）；
            3. 不在代理工具的祖传段里（10800–10999、7890… 撞车重灾区）；
            4. 真的 bind 得起来（此刻没别人在听）。
        连续撞墙 attempts 次后，退化成确定性扫描 —— 宁可给个靠前的端口，
        也不能让用户卡在「加不了线路」。

        exclude 用于体检（heal_ports）时把「已经重新分配好的端口」排除掉，
        否则同一轮里可能把两条线分到同一对端口。
        """
        used = {l.http_port for l in self.lines} | {l.socks_port for l in self.lines}
        used |= set(exclude or ())
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

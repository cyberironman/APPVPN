# -*- coding: utf-8 -*-
"""本机自用端口的随机分配 —— 单实例锁、控制口、界面服务**唯一**的出处。

为什么要随机
------------
以前这三个端口是写死的：托盘锁 `10896`、界面锁 `10897`、界面服务 `1443`。
前两个落在 `10800-10999` —— 各种代理工具的祖传段（v2rayN 10808/10809、
Clash 7890、本机另外两个管家 10888/10898/10878…），谁先绑谁活；`1443` 更糟，
跟 SQL Server 的 1433 只差十个数，装了数据库客户端的机器很可能先占掉。

端口被占的表现和「端口冲突」看起来毫无关系：
  * 单实例锁抢不到 → 程序以为「已经有一个在跑」，**静默退出** → 双击没反应；
  * 界面服务端口绑不上 → 窗口打不开，或开着开着整个界面白屏。
日志里只有一句「bind 失败」，很难想到是别的软件占了某个固定号段。

现行规则
--------
1. 三个槽位各自随机，范围 **15002-19998**（AppProxy 自己的段）。另外两个兄弟
   程序在 20000-39999（Chrome 管家）和 40000-49148（Codex 管家），三段不重叠，
   同时开着也不会挑到同一个号。
   **下限不能低于 15002**：本机 Windows 动态端口段被调成了 1024-15000
   （`netsh int ipv4 show dynamicport tcp` 实测），出站连接会从这一段里随机
   拿本地端口，listen 端口落进去迟早撞上 `bind: address already in use`。
2. 只取偶数，并且整段避开代理工具的祖传端口（`registry._AVOID_RANGES`）。
3. 结果持久化到 `data/runtime/localports.json`，**重启沿用**。端口一跳，
   托盘就找不到已经开着的界面，等于把用户锁在门外。
4. 每次启动复核：持久化的值不在区间内、或者此刻真 bind 不上 → 重挑。
5. 三个槽位之间互不重复。
"""
from __future__ import annotations

import json
import os
import random
import socket
import time
from pathlib import Path

from appproxy import settings

#: 槽位名。加新槽位只要写进这里，分配/持久化自动生效。
SLOTS = ("ctl", "gui_lock", "gui_http")

#: 随机区间（含左不含右）。偶数 = 成对分配的端口 +1 也不会越界。
#: 起点 15002 是硬下限 —— 必须高于本机动态端口段上界 15000。
PORT_MIN = 15002
PORT_MAX = 19998

#: 从 registry 借同一份「整段避开」清单，避免两处各写一套标准。
try:
    from appproxy.registry import _AVOID_RANGES as AVOID_RANGES
except Exception:                                   # pragma: no cover - 兜底
    AVOID_RANGES = ((1, 1023), (1080, 1081), (7890, 7899), (1024, 15000),
                    (10800, 10999), (18488, 18489), (49152, 65535))


# ------------------------------------------------------------------ 状态文件
def state_file() -> Path:
    return settings.runtime_dir() / "localports.json"


def load_state() -> dict[str, int]:
    try:
        raw = json.loads(state_file().read_text(encoding="utf-8"))
    except Exception:                               # noqa: BLE001  文件坏了就当没有
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        if isinstance(v, (int, float, str)):
            try:
                out[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
    return out


def save_state(state: dict[str, int]) -> None:
    """原子写。写不成只影响「下次重启还认不认得这个号」，不该让启动失败。"""
    try:
        f = state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        payload = {k: int(v) for k, v in state.items() if not k.startswith("_")}
        payload["_updated"] = int(time.time())
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, f)
    except Exception:                               # noqa: BLE001
        pass


def get(slot: str, default: int = 0) -> int:
    """读某个槽位已分配的端口（没分配过返回 default）。不 bind、不改文件。"""
    try:
        return int(load_state().get(slot) or default)
    except Exception:                               # noqa: BLE001
        return default


def forget(slot: str) -> None:
    st = load_state()
    if slot in st:
        st.pop(slot, None)
        save_state(st)


# ------------------------------------------------------------------ 挑选
def _in_range(port: int) -> bool:
    return PORT_MIN <= int(port) <= PORT_MAX - 1


def _avoided(port: int) -> bool:
    return any(lo <= int(port) <= hi for lo, hi in AVOID_RANGES)


def _usable(port: int) -> bool:
    return _in_range(port) and not _avoided(port)


def can_bind(port: int) -> bool:
    """127.0.0.1:port 此刻能不能绑上（= 没有别的程序在听）。

    Windows 上 **绝不能** 设 SO_REUSEADDR —— 它会让「已被监听」也绑成功，
    于是单实例锁形同虚设（两个托盘同时活着，菜单互相打架）。
    """
    if not (1 <= int(port) <= 65535):
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def _pick(reserved: set[int], tries: int = 300) -> int:
    """在区间里随机挑一个空闲端口；极端情况下顺序扫一遍。挑不到抛 RuntimeError。"""
    rnd = random.SystemRandom()                     # 不跟启动时序挂钩，两台机器不会同批
    for _ in range(tries):
        p = rnd.randrange(PORT_MIN, PORT_MAX, 2)
        if p in reserved or not _usable(p):
            continue
        if can_bind(p):
            return p
    for p in range(PORT_MIN, PORT_MAX, 2):
        if p in reserved or not _usable(p):
            continue
        if can_bind(p):
            return p
    raise RuntimeError(f"找不到可用端口（{PORT_MIN}-{PORT_MAX} 全被占用？）")


def alloc(slot: str) -> int:
    """给槽位挑一个端口并**立刻**写回，不等 bind。

    用于「等会儿才用得上」的场景（界面服务要先把端口号传给浏览器）。
    真正需要占住的时候用 `bind()`。
    """
    st = load_state()
    old = int(st.get(slot) or 0)
    reserved = {v for k, v in st.items() if k != slot and not k.startswith("_")}
    if _usable(old) and old not in reserved and can_bind(old):
        return old
    p = _pick(reserved)
    st[slot] = p
    save_state(st)
    return p


def bind(slot: str) -> socket.socket | None:
    """给槽位拿一个**已 listen** 的 socket 当锁用。

    优先沿用上次的端口；绑不上就先只换这一个槽位，不动别的槽位 ——
    界面在 10020 跑着、托盘重启时，绝不能把界面的端口也一起换掉。
    彻底挑不到返回 None（调用方按「无锁运行」降级，不要退出）。
    """
    st = load_state()
    old = int(st.get(slot) or 0)
    reserved = {v for k, v in st.items() if k != slot and not k.startswith("_")}

    candidates: list[int] = []
    if _usable(old) and old not in reserved:
        candidates.append(old)

    for p in candidates:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            s.listen(4)
            s.settimeout(1.0)
            return s
        except OSError:
            s.close()

    # 上次的号不能用 → 重新挑
    try:
        p = _pick(reserved)
    except RuntimeError:
        return None
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", p))
        s.listen(4)
        s.settimeout(1.0)
    except OSError:
        s.close()
        return None
    st[slot] = p
    save_state(st)
    return s

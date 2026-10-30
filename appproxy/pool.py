# -*- coding: utf-8 -*-
"""内核池：按「有没有软件在用」决定内核的生死。

本产品与全局代理软件的分水岭
    全局代理：内核常驻，系统里一套设置，所有程序共享 —— 想换只能整体换。
    本产品：内核是**按需的**。软件 A 要用才拉起它那条线路，软件 A 关掉、
    而且没别的软件在用同一条线路，内核就自己退场。整机不设代理也行。

三条硬规则
    1. 启动路径上**绝不做测速**。测速是独立的后台任务，慢也只慢自己，
       不拖累任何软件的启动。冷启动内核实测约 1 秒，用户可接受。
    2. 只有「登记为本产品为档案拉的」内核才会被自动回收。
       用户手动起的线路（比如要给系统代理用）不受 GC 影响。
    3. 判活靠进程，不靠内存里的计数器 —— 主界面关掉再打开、托盘重启，
       判断依然正确。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from . import coreproc, procwatch, settings, singconf, subscription, xrayconf
from .models import Line
from .profiles import AppProfile, ProfileStore
from .registry import Registry


# --------------------------------------------------------------------------- 登记表
def _ledger_path() -> Path:
    return settings.data_dir() / "pool.json"


def _read_ledger() -> dict[str, Any]:
    path = _ledger_path()
    if not path.exists():
        return {"lines": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"lines": {}}
    raw.setdefault("lines", {})
    return raw


def _write_ledger(data: dict[str, Any]) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ledger() -> dict[str, dict[str, Any]]:
    return _read_ledger().get("lines") or {}


def _mark(line_id: str, profile_id: str) -> None:
    data = _read_ledger()
    entry = data["lines"].setdefault(line_id, {"by": [], "since": int(time.time())})
    if profile_id and profile_id not in entry["by"]:
        entry["by"].append(profile_id)
    _write_ledger(data)


def _unmark(line_id: str, profile_id: str = "", drop: bool = False) -> None:
    data = _read_ledger()
    entry = data["lines"].get(line_id)
    if entry is None:
        return
    if drop:
        data["lines"].pop(line_id, None)
    elif profile_id:
        entry["by"] = [x for x in entry["by"] if x != profile_id]
        # 最后一个使用者走了，登记也一起撤掉
        if not entry["by"]:
            data["lines"].pop(line_id, None)
    _write_ledger(data)


# --------------------------------------------------------------------------- 起内核
def _config_port_ok(cfg: Path, port: int) -> bool:
    """配置里到底有没有监听这个端口？

    sing-box 写 `"listen_port": N`，xray 写 `"port": N`；这里只做文本匹配，
    不按两套 schema 分别解析 —— 目的只是发现「配置和线路不是同一代」，
    不必保证语义完全正确（真正的校验有 `sing-box check` / `xray test`）。
    """
    try:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(re.search(rf'"(?:listen_)?port"\s*:\s*{int(port)}\b', text))


def ensure_core(reg: Registry, line: Line, *, wait: float = 12.0,
                allow_fetch: bool = True) -> str:
    """保证这条线路的端口在监听。**不测速**，只用已有节点快照。

    无配置时用上一次拉到的节点直接生成（秒级）；连节点快照都没有，
    才同步拉一次订阅（首次使用的一次性代价）。
    """
    if coreproc.port_open(line.http_port):
        return f"内核已在运行（端口 {line.http_port}）"

    binary = reg.binary(line.engine)
    if binary is None:
        raise RuntimeError(f"找不到 {line.engine} 内核，线路 {line.id} 无法启动")

    cfg = line.config_path()
    # 配置里的监听端口和线路对不上 → 这份配置是旧的（端口体检换过号、
    # 手改过 lines.json、或者从别的机器搬过来），直接作废重建。
    # 不处理的话内核会去听旧端口，界面只看到「启动超时」，日志还是空的。
    if cfg.exists() and line.http_port and not _config_port_ok(cfg, line.http_port):
        try:
            cfg.unlink()
        except OSError:
            pass
    # 订阅在后台更新过、而这份配置还是按旧节点编译的 —— 先重写再用。
    # 不在这里处理的话，「自动更新」就等于只更新了缓存，永远不生效。
    if cfg.exists() and getattr(line, "sub_stale", False):
        nodes, _ = reg.load_nodes(line)
        if nodes:
            data = (singconf.build(line, nodes) if line.engine.lower().startswith("sing")
                    else xrayconf.build(line, nodes))
            xrayconf.write(data, cfg)
            line.sub_stale = False
            try:
                reg.save()
            except OSError:
                pass
    if not cfg.exists():
        nodes, _ = reg.load_nodes(line)
        if not nodes and allow_fetch and line.sub_url:
            nodes, meta = subscription.load(line.sub_url)
            reg.save_nodes(line, nodes, meta)
        if not nodes:
            raise RuntimeError(f"线路 {line.id} 没有可用节点，先更新订阅")
        data = (singconf.build(line, nodes) if line.engine.lower().startswith("sing")
                else xrayconf.build(line, nodes))
        xrayconf.write(data, cfg)

    h = coreproc.start(binary, cfg, line.http_port, wait=wait)
    return f"内核已启动（端口 {line.http_port}，PID {h.pid}）"


def start_for_profile(reg: Registry, profile: AppProfile) -> str:
    """给一个档案准备代理。直连档案不需要内核。"""
    if not profile.line_id:
        return "直连档案，不需要内核"
    line = reg.get(profile.line_id)
    if line is None:
        raise RuntimeError(f"档案 {profile.id} 绑定的线路 {profile.line_id} 不存在")
    msg = ensure_core(reg, line)
    _mark(line.id, profile.id)
    return msg


# --------------------------------------------------------------------------- 回收
def stop_line(line: Line) -> int:
    n = coreproc.stop(line.config_path())
    _unmark(line.id, drop=True)
    return n


def users_of(line_id: str, profiles: list[AppProfile]) -> list[AppProfile]:
    return [p for p in profiles if p.line_id == line_id]


# 宽限期：刚登记的内核先留着。
# 程序从 Popen 到真的出现在进程表里有一两秒空档，这段空档不能让 GC 误判成"没人用"。
GRACE_SECONDS = 60.0


def gc(reg: Registry | None = None, store: ProfileStore | None = None,
       *, snap: procwatch.Snapshot | None = None) -> list[dict[str, Any]]:
    """回收「本产品为档案拉起、但已没人用」的内核。返回回收动作列表。

    只碰登记过的线路；用户手动启动的线路一律不动。
    """
    reg = reg or Registry().load()
    store = store or ProfileStore().load()
    marked = ledger()
    if not marked:
        return []

    snap = snap or procwatch.snapshot()
    alive = procwatch.alive_map(store.profiles, snap)
    now = time.time()
    done: list[dict[str, Any]] = []

    for line_id in list(marked):
        line = reg.get(line_id)
        if line is None:                       # 线路被删了，登记也清掉
            _unmark(line_id, drop=True)
            continue
        if now - float(marked[line_id].get("since") or 0) < GRACE_SECONDS:
            continue                            # 刚起来，先别动
        users = users_of(line_id, store.profiles)
        if any(alive.get(p.id) for p in users):
            continue                            # 还有软件在用，保留
        n = coreproc.stop(line.config_path())
        _unmark(line_id, drop=True)
        done.append({"line": line_id, "stopped": n, "port": line.http_port})
    return done


# --------------------------------------------------------------------------- 自愈
# 判死要慢，抢救要快 —— 这是被教训出来的：
#   1. 刚起的内核要一两秒才开始监听端口。把「端口还没通」当成「死了」，
#      就变成每轮杀一次活着的内核，整机网络抖到没法用。
#   2. 所以先看**进程在不在**（按命令行里的 config 路径认，不会误认别人的内核）；
#      进程在 = 还在启动，什么都不做。
#   3. 进程不在、端口也不通，连续 HEAL_AFTER 轮才判定真死了。
#   4. 重启失败按 60 → 120 → 240 → 300 秒退避，不允许每分钟硬撞一次。
_HEAL_STATE: dict[str, dict[str, Any]] = {}
HEAL_AFTER = 8                    # 托盘 6 秒一轮 ≈ 48 秒，够慢
HEAL_BACKOFF = (60, 120, 240, 300)


def heal(reg: Registry | None = None, store: ProfileStore | None = None,
         *, snap: procwatch.Snapshot | None = None, now: float | None = None) -> list[str]:
    """把半路死掉的内核拉回来。只救「还有程序在用」的线路，返回给人看的说明。"""
    reg = reg or Registry().load()
    store = store or ProfileStore().load()
    snap = snap or procwatch.snapshot()
    alive = procwatch.alive_map(store.profiles, snap)
    now = time.time() if now is None else now
    out: list[str] = []

    for line in reg.lines:
        users = users_of(line.id, store.profiles)
        if not any(alive.get(p.id) for p in users):
            _HEAL_STATE.pop(line.id, None)     # 没人用了，交回给 gc
            continue
        st = _HEAL_STATE.setdefault(
            line.id, {"fails": 0, "next_at": 0.0, "restarts": 0})
        if now < st["next_at"]:
            continue
        if coreproc.port_open(line.http_port):
            st.update(fails=0, restarts=0, next_at=0.0)
            continue
        if coreproc.pids_by_config(line.config_path()):
            continue                            # 进程还在，只是还没开始听，别动
        st["fails"] += 1
        if st["fails"] < HEAL_AFTER:
            continue
        try:
            msg = ensure_core(reg, line, allow_fetch=False)
            out.append(f"{line.name} 的内核掉线了，已自动拉起（{msg}）")
            st.update(fails=0, next_at=0.0)
        except Exception as e:                  # noqa: BLE001  救不活也要说清楚
            wait = HEAL_BACKOFF[min(st["restarts"], len(HEAL_BACKOFF) - 1)]
            st["next_at"] = now + wait
            st["restarts"] = min(st["restarts"] + 1, len(HEAL_BACKOFF) - 1)
            out.append(f"{line.name} 的内核起不来：{e}（{wait} 秒后再试）")
    return out


# --------------------------------------------------------------------------- 关闭
def release(reg: Registry, store: ProfileStore, profile: AppProfile,
            *, force: bool = False) -> str:
    """一个档案不再需要代理：撤掉它的登记，没人用了就停内核。

    这是「关闭按钮」的后半段。前半段（结束程序）在 close_profile 里。
    """
    if not profile.line_id:
        return "直连档案，无需处理"
    line = reg.get(profile.line_id)
    if line is None:
        _unmark(profile.line_id, profile.id)
        return "绑定的线路已不存在"

    snap = procwatch.snapshot()
    others = [p for p in store.profiles
              if p.line_id == line.id and p.id != profile.id and snap.alive(p)]
    _unmark(line.id, profile.id)

    if others and not force:
        return f"仍有 {len(others)} 个软件在用 {line.name}，内核保留"
    n = coreproc.stop(line.config_path())
    _unmark(line.id, drop=True)
    return f"{line.name} 已停止（结束 {n} 个内核进程）"


def close_profile(reg: Registry, store: ProfileStore, profile: AppProfile) -> dict[str, Any]:
    """「关闭这个软件」：结束它的进程，再让它那条线路按需收摊。"""
    snap = procwatch.snapshot()
    pids = snap.pids(profile)
    ended = 0
    for pid in pids:
        try:
            import psutil
            proc = psutil.Process(pid)
            proc.terminate()
            ended += 1
        except Exception:  # noqa: BLE001  进程可能自己退了
            continue
    # 给它一点时间自己退，剩下的强杀
    if ended:
        deadline = time.time() + 4
        while time.time() < deadline:
            left = procwatch.snapshot().pids(profile)
            if not left:
                break
            time.sleep(0.25)
        for pid in procwatch.snapshot().pids(profile):
            try:
                import psutil
                psutil.Process(pid).kill()
            except Exception:  # noqa: BLE001
                continue

    msg = release(reg, store, profile)
    return {"ended": ended, "message": msg}


# --------------------------------------------------------------------------- 状态快照
def state(reg: Registry | None = None, store: ProfileStore | None = None) -> dict[str, Any]:
    """给界面用的一份完整快照：线路、档案、内核、登记。"""
    reg = reg or Registry().load()
    store = store or ProfileStore().load()
    snap = procwatch.snapshot()
    marked = ledger()

    lines = []
    for line in reg.lines:
        up = coreproc.port_open(line.http_port)
        users = users_of(line.id, store.profiles)
        running_users = [p.id for p in users if snap.alive(p)]
        lines.append({
            "id": line.id,
            "name": line.name,
            "engine": line.engine,
            "mode": line.mode,
            "http_port": line.http_port,
            "socks_port": line.socks_port,
            "running": up,
            "config_exists": line.config_path().exists(),
            "users": [p.id for p in users],
            "alive_users": running_users,
            "auto": line.id in marked,
        })

    profiles = []
    for p in store.profiles:
        line = reg.get(p.line_id) if p.line_id else None
        profiles.append({
            "id": p.id,
            "name": p.name,
            "exe": p.exe,
            "line_id": p.line_id,
            "line_name": line.name if line else ("直连" if not p.line_id else p.line_id),
            "kind": p.resolved_kind,
            "proxy": f"127.0.0.1:{line.http_port}" if line and line.http_port else "",
            "alive": snap.alive(p),
            "icon": p.icon_path().exists(),
            "url": p.url,
            "data_dir": str(p.data_path()),
        })

    return {
        "lines": lines,
        "profiles": profiles,
        "kernel_running": [l["id"] for l in lines if l["running"]],
        "lease": marked,
        "ts": int(time.time()),
    }

# -*- coding: utf-8 -*-
"""订阅自动更新：把「节点过期了得自己点一下」改成后台自己干。

为什么值得单独一个模块
    手动更新那条路（界面上的「更新」按钮）会**拉订阅 → 全节点测速 → 重建配置
    → 重启内核**，全套跑完几十秒，是给用户主动操作设计的。自动更新绝不能照抄：
    每 12 小时在后台抖一次测速，托盘会跟着卡；重启内核更会把正在用的连接掐断。

三条硬规则
    1. **不测速**。只拉订阅、只换节点缓存。延迟测量交给用户点「更新」时做。
    2. **不主动断内核**。新节点拉到、内核正在服务时，只记一笔 `sub_stale`
       （界面显示「有新节点 · 重启内核后生效」）。下次内核自己重启（自愈、
       用户点启动）时自然切过去 —— 更新节点不该以掐断连接为代价。
    3. **失败要退避**。订阅站偶尔 502，每 10 秒重试一次等于拿用户 IP 去砸门。
       连续失败按 5 分钟 → 15 分 → 45 分 → 2 小时 → 6 小时 退避，成功后清零。

从外部看只有四个函数：due / describe / refresh / run_due。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from . import coreproc, subscription
from .models import Line
from .registry import Registry

DEFAULT_INTERVAL_H = 12
# 连续失败后的重试间隔（秒）。最后一项是上限，不再往上涨。
BACKOFF = (300, 900, 2700, 7200, 21600)
MIN_INTERVAL_H = 1
MAX_INTERVAL_H = 168          # 一周；再久就等于没开


def global_interval(reg: Registry) -> int:
    """全局默认间隔（小时）。**0 是「全部关掉」，不能被 `or` 吃掉。**"""
    raw = reg.defaults.get("sub_interval_h", None)
    if raw is None:
        return DEFAULT_INTERVAL_H
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_H


def interval_of(line: Line, default: int = DEFAULT_INTERVAL_H) -> int:
    """这条线路的更新间隔（小时）。-1 跟随全局，0 关闭。"""
    try:
        own = int(getattr(line, "sub_interval_h", -1))
    except (TypeError, ValueError):
        own = -1
    if own < 0:
        # 注意不能写 `default or DEFAULT_INTERVAL_H`：全局设成 0（全部关闭）时
        # 会被 `or` 悄悄换成 12，界面就会出现「已关闭」却还在更新。
        own = DEFAULT_INTERVAL_H if default is None else int(default)
    if own == 0:
        return 0
    return max(MIN_INTERVAL_H, min(own, MAX_INTERVAL_H))


def _wait_seconds(line: Line, default: int) -> float:
    """距离下次该拉取还有多久（秒）。<=0 表示现在就该拉。"""
    wait = interval_of(line, default) * 3600
    fails = int(getattr(line, "sub_fail", 0) or 0)
    if fails:
        wait = min(wait, BACKOFF[min(fails, len(BACKOFF)) - 1])
    return wait


def due(line: Line, *, now: float | None = None, default: int = DEFAULT_INTERVAL_H) -> bool:
    """这条线路现在要不要拉订阅。

    从没拉过的（sub_checked_at=0）算到期 —— 第一次跑就把缓存填上，
    否则「自动更新开着」却永远不动。
    """
    if not (getattr(line, "sub_url", "") or "").strip():
        return False
    if interval_of(line, default) <= 0:
        return False
    now = time.time() if now is None else now
    last = max(int(getattr(line, "sub_checked_at", 0) or 0),
               int(getattr(line, "sub_ok_at", 0) or 0))
    if not last:                      # 从没拉过 —— 立刻拉一次，别等满一个周期
        return True
    return now - last >= _wait_seconds(line, default)


def describe(line: Line, *, now: float | None = None,
             default: int = DEFAULT_INTERVAL_H) -> str:
    """一句话讲清这条线路的订阅状态（面板和 CLI 共用同一套措辞）。"""
    now = time.time() if now is None else now
    if not (getattr(line, "sub_url", "") or "").strip():
        return "没有订阅地址（手动导入的节点）"
    iv = interval_of(line, default)
    if iv <= 0:
        return "自动更新已关闭"
    fails = int(getattr(line, "sub_fail", 0) or 0)
    if fails:
        reason = (getattr(line, "sub_error", "") or "未知原因").strip()
        left = _wait_seconds(line, default) - (now - int(line.sub_checked_at or 0))
        tail = f"，约 {_human_delta(left)}后重试" if left > 0 else "，马上重试"
        return f"更新失败（连续 {fails} 次）：{reason}{tail}"
    ok_at = int(getattr(line, "sub_ok_at", 0) or 0)
    if not ok_at:
        return f"还没更新过 · 每 {iv} 小时自动更新"
    spent = now - ok_at
    nxt = _wait_seconds(line, default) - spent
    tail = f"，{_human_delta(nxt)}后自动更新" if nxt > 0 else "，即将自动更新"
    return f"上次更新 {_human_delta(spent)}前 · 每 {iv} 小时{tail}"


def _human_delta(sec: float) -> str:
    sec = max(0.0, float(sec))
    if sec < 90:
        return "不到 1 分钟"
    if sec < 3600:
        return f"{int(sec // 60)} 分钟"
    if sec < 86400:
        return f"{int(sec // 3600)} 小时"
    return f"{int(sec // 86400)} 天"


# --------------------------------------------------------------------------- 干活
def _drop_config(line: Line) -> None:
    """内核没在跑时删掉编译好的配置 —— 下次启动自然用新节点重建。"""
    try:
        cfg = line.config_path()
        if cfg.exists():
            cfg.unlink()
    except OSError:
        pass


def refresh(reg: Registry, line: Line, *, loader: Callable | None = None,
            now: float | None = None) -> dict[str, Any]:
    """拉一次订阅并写回状态。**不测速、不重启内核。**

    返回 {line, ok, count, changed, running, error}，失败也返回而不抛异常 ——
    调用方（托盘后台线程）不该因为一个订阅站挂了就整轮中断。
    """
    loader = loader or subscription.load
    now = time.time() if now is None else now
    line.sub_checked_at = int(now)
    try:
        nodes, meta = loader(line.sub_url)
    except Exception as e:                          # noqa: BLE001  什么错都翻成一句话
        line.sub_fail = int(line.sub_fail or 0) + 1
        line.sub_error = str(e).strip()[:200] or type(e).__name__
        reg.save()
        return {"line": line.id, "ok": False, "error": line.sub_error,
                "count": 0, "changed": False, "running": False}

    old_nodes, _ = reg.load_nodes(line)
    old_uids = {n.uid for n in old_nodes}
    new_uids = {n.uid for n in nodes}
    changed = bool(old_nodes) and old_uids != new_uids

    reg.save_nodes(line, nodes, meta)
    line.sub_fail = 0
    line.sub_error = ""
    line.sub_ok_at = int(now)

    running = False
    try:
        running = bool(coreproc.port_open(line.http_port))
    except Exception:                               # noqa: BLE001
        running = False
    if changed:
        if running:
            line.sub_stale = True                   # 界面提示，下次重启内核生效
        else:
            _drop_config(line)                      # 没人用，直接让旧配置作废
    reg.save()
    return {"line": line.id, "ok": True, "count": len(nodes),
            "changed": changed, "running": running}


def run_due(reg: Registry | None = None, *, now: float | None = None,
            loader: Callable | None = None, limit: int = 4) -> list[dict[str, Any]]:
    """把所有到期的线路拉一遍（每轮最多 limit 条，避免一轮打满带宽）。"""
    reg = reg or Registry().load()
    now = time.time() if now is None else now
    default = global_interval(reg)
    out: list[dict[str, Any]] = []
    for line in reg.lines:
        if len(out) >= limit:
            break
        if not due(line, now=now, default=default):
            continue
        out.append(refresh(reg, line, loader=loader, now=now))
    return out


def status(reg: Registry | None = None, *, now: float | None = None) -> list[dict[str, Any]]:
    """给界面/CLI 的一份只读快照。"""
    reg = reg or Registry().load()
    now = time.time() if now is None else now
    default = global_interval(reg)
    return [{
        "id": line.id,
        "name": line.name,
        "enabled": interval_of(line, default) > 0,
        "interval_h": interval_of(line, default),
        "due": due(line, now=now, default=default),
        "ok_at": int(getattr(line, "sub_ok_at", 0) or 0),
        "checked_at": int(getattr(line, "sub_checked_at", 0) or 0),
        "fails": int(getattr(line, "sub_fail", 0) or 0),
        "error": getattr(line, "sub_error", "") or "",
        "stale": bool(getattr(line, "sub_stale", False)),
        "text": describe(line, now=now, default=default),
    } for line in reg.lines]

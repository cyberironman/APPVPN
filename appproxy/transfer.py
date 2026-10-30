# -*- coding: utf-8 -*-
"""配置导入导出：换电脑不用整目录搬。

为什么不能直接拷 data 目录
    搬整个 `data` 会把不该跟人走的东西也搬过去：
      * 端口（每台机器要重新随机分，别人的端口到你这里大概率被占）；
      * 节点缓存（几千行，几 MB，而且是会过期的）；
      * 内核登记表、系统代理备份、TUN 状态（都是本机状态，搬过去只会错乱）。
    所以导出的是**一份配置文件**：线路定义 + 应用档案，只留真正属于
    「我的配置」的字段。

两档导出
    include_secrets=False（默认）  订阅地址整条打掉 —— 适合发给别人看、贴教程。
    include_secrets=True           带订阅地址 —— 只适合自己在本机之间搬。
    文件头上有 `contains_secrets` 标记，导入时据此提醒。

导入行为
    * 线路：id 已存在 → 更新可配置字段，**端口保持本机原值**；id 不存在 → 新建，
      端口当场随机分（不沿用导出方的端口）。
    * 档案：id 已存在 → 更新；不存在 → 新增。绑定的线路在导出方存在、本机没有
      → 改成直连并记一条 warning，不让档案指向不存在的线路。
    * 导入完不自动起内核 —— 让用户自己决定什么时候开。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import settings
from .models import Line
from .profiles import AppProfile, ProfileStore
from .registry import Registry

BUNDLE_MAGIC = "appproxy-bundle"
BUNDLE_VERSION = 1

# 导出时丢掉的本机字段：端口要重分，其余是运行痕迹
_DROP_LINE = {
    "http_port", "socks_port", "node_key",
    "sub_checked_at", "sub_ok_at", "sub_fail", "sub_error", "sub_stale",
}
_DROP_PROFILE = {"data_dir", "icon", "shortcut", "created"}


def default_path(tag: str = "config") -> Path:
    """默认落在数据目录下的 exports/，用户一眼能找到（不是临时目录）。"""
    d = settings.home() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"appproxy-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.json"


def export_bundle(reg: Registry, store: ProfileStore, *,
                  include_secrets: bool = False, now: float | None = None) -> dict[str, Any]:
    """把当前配置整理成可搬运的一份 dict（纯计算，不写盘）。"""
    now = time.time() if now is None else now
    lines = []
    for line in reg.lines:
        d = {k: v for k, v in line.to_dict().items() if k not in _DROP_LINE}
        if not include_secrets:
            d["sub_url"] = ""
        lines.append(d)
    profiles = []
    for p in store.profiles:
        d = {k: v for k, v in p.to_dict().items() if k not in _DROP_PROFILE}
        profiles.append(d)
    return {
        "appproxy_bundle": BUNDLE_VERSION,
        "app": "appproxy",
        "exported_at": int(now),
        "exported_at_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "contains_secrets": bool(include_secrets),
        "note": ("含订阅地址，请勿公开分享" if include_secrets
                 else "不含订阅地址，导入后需要重新填订阅"),
        "lines": lines,
        "profiles": profiles,
    }


def dump(bundle: dict[str, Any], path: Path | None = None) -> Path:
    target = Path(path) if path else default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)
    return target


def load(path: Path) -> dict[str, Any]:
    """读一份导出文件并做基本校验。格式不对就抛 ValueError（带人话）。"""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"找不到文件：{p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"不是有效的 JSON：{e}") from e
    if not isinstance(raw, dict) or "appproxy_bundle" not in raw:
        raise ValueError("这不是本软件导出的配置文件（缺少 appproxy_bundle 标记）")
    if int(raw.get("appproxy_bundle") or 0) > BUNDLE_VERSION:
        raise ValueError(f"配置版本 {raw['appproxy_bundle']} 比本软件支持的 {BUNDLE_VERSION} 新，"
                         "请先升级软件")
    raw.setdefault("lines", [])
    raw.setdefault("profiles", [])
    return raw


def import_bundle(reg: Registry, store: ProfileStore, bundle: dict[str, Any], *,
                  fetch_sub: bool = False, loader=None) -> dict[str, Any]:
    """把一份配置合并进本机。返回一份人话报告。

    fetch_sub=True 时，带订阅地址的新线路顺手拉一次节点（导入即可用）。
    拉不到只记 warning，不影响导入本身。
    """
    added_l: list[str] = []
    updated_l: list[str] = []
    added_p: list[str] = []
    updated_p: list[str] = []
    warnings: list[str] = []
    line_ids = {l.id for l in reg.lines}

    for d in bundle.get("lines") or []:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        known = {f for f in Line.__dataclass_fields__}      # type: ignore[attr-defined]
        payload = {k: v for k, v in d.items() if k in known and k not in _DROP_LINE}
        try:
            new = Line(**payload)
        except (TypeError, ValueError) as e:
            warnings.append(f"线路 {d.get('id')} 跳过：{e}")
            continue
        old = reg.get(new.id)
        if old is not None:
            old.name = new.name or old.name
            old.mode = new.mode
            old.engine = new.engine
            old.health_url = new.health_url or old.health_url
            old.sub_interval_h = new.sub_interval_h
            old.enabled = new.enabled
            if new.sub_url:                 # 导出时没带地址就保留本机原来的
                old.sub_url = new.sub_url
            reg.upsert(old)
            updated_l.append(old.id)
            continue
        try:
            new.http_port, new.socks_port = reg.alloc_ports()
        except RuntimeError as e:
            warnings.append(f"线路 {new.id} 没有可用端口：{e}")
            continue
        new.node_key = ""
        new.sub_stale = False
        reg.upsert(new)
        line_ids.add(new.id)
        added_l.append(new.id)
        if fetch_sub and new.sub_url:
            from . import subscription
            try:
                nodes, meta = (loader or subscription.load)(new.sub_url)
                reg.save_nodes(new, nodes, meta)
                new.sub_ok_at = new.sub_checked_at = int(time.time())
                new.sub_fail = 0
                new.sub_error = ""
            except Exception as e:          # noqa: BLE001  订阅拉不到不影响导入
                new.sub_fail = 1
                new.sub_error = str(e)[:200]
                warnings.append(f"线路 {new.id} 订阅拉取失败：{e}")
    reg.save()

    for d in bundle.get("profiles") or []:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        known = {f for f in AppProfile.__dataclass_fields__}   # type: ignore[attr-defined]
        payload = {k: v for k, v in d.items() if k in known and k not in _DROP_PROFILE}
        if payload.get("line_id") and payload["line_id"] not in line_ids:
            warnings.append(f"档案 {payload['id']} 绑的线路 {payload['line_id']} 本机没有，已改为直连")
            payload["line_id"] = ""
        try:
            new_p = AppProfile(**payload)
        except (TypeError, ValueError) as e:
            warnings.append(f"档案 {d.get('id')} 跳过：{e}")
            continue
        old_p = store.get(new_p.id)
        if old_p is not None:
            old_p.name = new_p.name or old_p.name
            old_p.exe = new_p.exe or old_p.exe
            old_p.line_id = new_p.line_id or old_p.line_id
            old_p.kind = new_p.kind
            old_p.url = new_p.url or old_p.url
            if new_p.args:
                old_p.args = list(new_p.args)
            store.upsert(old_p)
            updated_p.append(old_p.id)
            continue
        store.upsert(new_p)
        added_p.append(new_p.id)

    store.save()
    return {
        "lines_added": added_l,
        "lines_updated": updated_l,
        "profiles_added": added_p,
        "profiles_updated": updated_p,
        "warnings": warnings,
    }


def summary_text(r: dict[str, Any]) -> str:
    """把导入报告翻成一段能直接打印的话。"""
    def _join(items: list[str]) -> str:
        return "、".join(items) if items else "无"

    parts = [
        f"线路：新增 {len(r['lines_added'])} 条（{_join(r['lines_added'])}），"
        f"更新 {len(r['lines_updated'])} 条（{_join(r['lines_updated'])}）",
        f"档案：新增 {len(r['profiles_added'])} 个（{_join(r['profiles_added'])}），"
        f"更新 {len(r['profiles_updated'])} 个（{_join(r['profiles_updated'])}）",
    ]
    if r["warnings"]:
        parts.append("需要留意：\n  " + "\n  ".join(r["warnings"]))
    return "\n".join(parts)

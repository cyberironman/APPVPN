# -*- coding: utf-8 -*-
"""首次启动就把常用软件摆到左边，别让用户对着一片空白发呆。

为什么要有这个
    新用户装完打开，左侧只有一列空列表和一个「＋」。他得先想清楚「我要代理
    哪个软件」，再去几百个程序里找 —— 大多数人走到这一步就关掉了。
    所以第一次启动直接建好一屏常用档案：Telegram、WhatsApp、Messenger、
    Instagram、X、Facebook、YouTube。打开就在左边，点一下就能用。

三条自我约束（都是用户画过的红线）
    1. **只建档案，不带软件本体**：本机装了桌面版就指向它的 exe；没装就给
       网页版 —— 一个专属浏览器窗口，点开直达网站，跟平时用的浏览器互不干扰。
    2. **不往桌面塞快捷方式**：桌面是用户自己的地方，预置只出现在软件自己的
       列表里。想要桌面图标，详情页里点一下就有。
    3. **只做一次**：版本号记在 profiles.json 里。用户删掉某个预置条目是明确
       的意思表示，下次启动不许再长回来。

线路怎么绑
    预置档案绑的是 `@default`（跟随默认线路），不是某条具体线路 —— 装完这个
    软件时用户通常还没导入订阅，等导入之后，这些档案自动跟着默认线路走，
    不用回来一个个改。
"""
from __future__ import annotations

from typing import Any

from . import appdb, appfind, profiles
from .registry import DEFAULT_LINE

# 预置清单的版本。以后想给老用户补几条新条目，把它 +1 并给清单加 preset 序号；
# 用户删掉的条目靠 `_removed` 记录保住，不会复活。
PRESET_VERSION = 1


def wanted(table: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """清单里标了 `preset` 的条目，按序号升序（序号就是左侧的排列顺序）。"""
    rows = [e for e in appdb.entries() if int(e.get("preset") or 0) > 0]
    rows.sort(key=lambda e: int(e.get("preset") or 0))
    return rows


def _browser(table: dict[str, list[str]]) -> str:
    try:
        return appfind.browser(table) or ""
    except OSError:
        return ""


def resolve(exe_names: list[str], web: str,
            table: dict[str, list[str]]) -> dict[str, str]:
    """一条清单条目能变成什么样的档案：桌面版优先，其次网页版，都不行就是空。

    返回 {"kind": exe|web|"", "exe": ..., "url": ...}
    """
    exe = appfind.locate_any(exe_names, table) if exe_names else None
    if exe:
        return {"kind": "exe", "exe": exe, "url": ""}
    br = _browser(table)
    if web and br:
        return {"kind": "web", "exe": br, "url": web}
    return {"kind": "", "exe": "", "url": ""}


def ensure(store: profiles.ProfileStore | None = None, *,
           table: dict[str, list[str]] | None = None,
           version: int = PRESET_VERSION,
           line_id: str = DEFAULT_LINE,
           force: bool = False) -> dict[str, Any]:
    """把常用软件建进档案表。已经做过（同版本）就直接返回，不重复建。

    force=True 时忽略版本号，只补「现在缺的那些」——给界面上的
    「恢复常用软件」按钮用，用户自己建的档案一个都不动。
    """
    store = store or profiles.ProfileStore().load()
    if force:
        # 「补回来」= 用户改主意了。清掉删除记录，否则建完下次启动又被跳过。
        store.preset_removed = []
    if not force and int(store.preset_version) >= version:
        return {"ran": False, "created": [], "skipped": []}

    t = table if table is not None else appfind.load()
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in wanted(t):
        key = profiles.slug(item.get("key") or item.get("name") or "")
        if not key or key == "profile":
            continue
        if key in store.preset_removed and not force:
            # 用户亲手删过 —— 这是「我不要」的意思，不许自动长回来。
            skipped.append({"key": key, "why": "你之前删掉了它"})
            continue
        old = store.get(key)
        if old is not None:
            # 用户手上已经有了（不管是不是预置的），一律不动。
            skipped.append({"key": key, "why": "已有同名档案"})
            continue

        hit = resolve(list(item.get("exe") or []), item.get("web") or "", t)
        if not hit["kind"]:
            # 桌面版没装、又找不到浏览器 —— 建了也是个点开就报错的条目。
            skipped.append({"key": key, "why": "本机既没装也没有可用的浏览器"})
            continue

        prof = profiles.AppProfile(
            id=key,
            name=item.get("name") or key,
            exe=hit["exe"],
            line_id=line_id,
            url=hit["url"],
            preset=True,
        )
        store.upsert(prof)
        created.append({
            "id": prof.id, "key": key, "name": prof.name, "kind": hit["kind"],
            "exe": hit["exe"], "url": hit["url"],
            "why": "本机已装桌面版" if hit["kind"] == "exe" else "给的网页版入口",
        })

    store.preset_version = max(int(store.preset_version), version)
    store.save()
    return {"ran": True, "created": created, "skipped": skipped}


def missing(store: profiles.ProfileStore | None = None,
            table: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """哪些常用软件现在不在左侧列表里（界面用来说「还能补几个」）。"""
    store = store or profiles.ProfileStore().load()
    have = {p.id for p in store.profiles}
    out = []
    for item in wanted(table):
        key = profiles.slug(item.get("key") or item.get("name") or "")
        if key and key not in have:
            out.append({"key": key, "name": item.get("name") or key})
    return out


def info(store: profiles.ProfileStore | None = None,
         table: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """给界面看的预置状态：一共几个、列表里还剩几个、缺哪几个。"""
    store = store or profiles.ProfileStore().load()
    want = wanted(table)
    miss = missing(store, table)
    return {
        "version": PRESET_VERSION,
        "applied": int(store.preset_version),
        "total": len(want),
        "in_list": len(want) - len(miss),
        "missing": miss,
        "line": DEFAULT_LINE,
    }


def describe(table: dict[str, list[str]] | None = None) -> str:
    """命令行/设置页用的一句话。"""
    names = [e.get("name") or e.get("key") or "" for e in wanted(table)]
    return f"常用软件 {len(names)} 个：{'、'.join(n for n in names if n)}"


def preset_of(key: str) -> dict[str, Any] | None:
    k = (key or "").strip().lower()
    return next((e for e in wanted() if (e.get("key") or "").lower() == k), None)

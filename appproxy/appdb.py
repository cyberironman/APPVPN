# -*- coding: utf-8 -*-
"""「哪些软件明显需要代理」——进程级清单。

为什么需要它
    新用户装完这个软件，第一件事永远是「我该把哪个软件挂上去」。
    让他自己在几百个程序里翻是不现实的，所以内置一份清单：
    命中（比如 chrome.exe / telegram.exe / codex.exe）就直接建议挂代理。

数据从哪来
    `data/apps_need_proxy.json` 是随包发的内置清单；
    `%HOME%/apps_need_proxy.json`（数据目录）可以覆盖它，方便以后在线更新，
    也方便用户自己加条目 —— 不用改程序文件。

两条硬规则
    1. **只按进程名匹配**，不看路径。同一个 exe 装在哪都是同一个程序。
    2. 匹配不上就返回 None，绝不猜。宁可少提示，不要乱接管。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import settings

BUILTIN = Path(__file__).resolve().parent / "data" / "apps_need_proxy.json"
OVERRIDE_NAME = "apps_need_proxy.json"

_cache: dict[str, Any] = {"stamp": None, "data": None}


def override_path() -> Path:
    return settings.data_dir() / OVERRIDE_NAME


def _pick_source() -> Path:
    """数据目录里有就用它（可在界面/命令行更新），否则用内置的。"""
    try:
        data = override_path()
        if data.is_file() and data.stat().st_size > 0:
            return data
    except OSError:
        pass
    return BUILTIN


def _stamp(path: Path) -> tuple[str, float, int]:
    try:
        st = path.stat()
        return str(path), st.st_mtime, st.st_size
    except OSError:
        return str(path), 0.0, 0


def load(reload: bool = False) -> dict[str, Any]:
    """读清单（带缓存，文件改了自动失效）。"""
    path = _pick_source()
    stamp = _stamp(path)
    if not reload and _cache["data"] is not None and _cache["stamp"] == stamp:
        return _cache["data"]
    data: dict[str, Any] = {"version": "", "apps": [], "groups": {}, "note": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    data.setdefault("apps", [])
    data.setdefault("groups", {})
    _cache.update(stamp=stamp, data=data)
    return data


def entries() -> list[dict[str, Any]]:
    return list(load().get("apps") or [])


def groups() -> dict[str, str]:
    return dict(load().get("groups") or {})


def _norm(exe: str) -> list[str]:
    """'C:\\X\\Chrome.EXE' -> ['chrome.exe', 'chrome']；两种都要能匹配。"""
    if not exe:
        return []
    name = exe.replace("\\", "/").rstrip("/").split("/")[-1].strip().lower()
    if not name:
        return []
    out = [name]
    if name.endswith(".exe"):
        out.append(name[:-4])
    return out


def find(exe: str) -> dict[str, Any] | None:
    """按进程名（或 exe 路径）查清单，命中返回条目，否则 None。"""
    names = set(_norm(exe))
    if not names:
        return None
    for item in entries():
        for cand in item.get("exe") or []:
            if set(_norm(cand)) & names:
                return item
    return None


def needed(exe: str) -> bool:
    return find(exe) is not None


def get(key: str) -> dict[str, Any] | None:
    """按稳定标识（key）取条目；老清单没有 key 时按名字兜底。"""
    k = (key or "").strip().lower()
    if not k:
        return None
    for item in entries():
        if (item.get("key") or "").lower() == k:
            return item
    for item in entries():
        if (item.get("name") or "").lower() == k:
            return item
    return None


def group_order() -> list[str]:
    """清单自己声明的分组顺序（JSON 里 dict 保序）。"""
    return list((load().get("groups") or {}).keys())


def suggest(have_exes: set[str] | None = None,
            table: dict[str, list[str]] | None = None,
            have_urls: set[str] | None = None) -> list[dict[str, Any]]:
    """「常用软件」这一屏的数据：清单全量 + 本机找没找到 + 建过档案没有。

    这是「傻瓜选择」的核心 —— 用户不需要翻开始菜单，也不需要知道自己装的
    是绿色版还是 Store 版。本机找到就一键建档；没找到就给官网下载地址。

    have_urls 是「已经建过的网页版档案」的站点地址（去掉 www 与末尾斜杠），
    否则 Facebook 这种条目每次进来都显示成「还没加」。
    """
    from . import appfind

    have = {e.lower() for e in (have_exes or set())}
    haurl = {u.rstrip("/").lower() for u in (have_urls or set())}
    t = table if table is not None else appfind.load()
    br = appfind.browser(t)
    out: list[dict[str, Any]] = []

    for item in entries():
        exes = list(item.get("exe") or [])
        exe = appfind.locate_any(exes, t) if exes else None
        store = "" if exe else (appfind.store_only(exes, t) if exes else "") or ""
        web = item.get("web") or ""
        br_exe = br or ""
        can_web = bool(web and br_exe)

        # 三种形态，决定了界面上那个按钮是什么：
        #   exe —— 本机有桌面版，点一下就建快捷方式
        #   web —— 本来就是网页服务（Facebook / YouTube），建专属浏览器图标
        #   ""  —— 有桌面版但本机没装，按钮是「去官网安装」（装完回来再点）
        if exe:
            kind = "exe"
        elif not exes and can_web:
            kind = "web"
        else:
            kind = ""

        have_it = (exe.lower() in have) if exe else (web.rstrip("/").lower() in haurl)
        badge = "" if (exe or can_web) else ("商店版" if store else "未安装")
        out.append({
            "key": item.get("key") or "",
            "name": item.get("name") or "",
            "group": item.get("group") or "",
            "level": item.get("level") or "often",
            "why": item.get("why") or "",
            "download": item.get("download") or "",
            "web": web,
            "exe": exe or "",
            "found": bool(exe),
            "store": store,
            "kind": kind,
            "can_web": can_web,
            "browser": br_exe,
            "have": bool(have_it),
            "badge": badge,
        })

    order = group_order()
    level_rank = {"must": 0, "often": 1}

    def sort_key(it: dict[str, Any]) -> tuple:
        # 先归组（界面按组渲染，打散会出现好几个同名标题），
        # 组内把本机装了的顶上去 —— 第一眼就看到自己能加什么。
        gi = order.index(it["group"]) if it["group"] in order else len(order)
        return (gi, not it["found"], level_rank.get(it["level"], 2), it["name"].lower())

    out.sort(key=sort_key)
    return out


def suggest_stats(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        "found": sum(1 for r in rows if r["found"]),
        # 「点一下就能加」= 桌面版已装 或 纯网页服务（有浏览器）
        "ready": sum(1 for r in rows if r["kind"] and not r["have"]),
        "installed_web": sum(1 for r in rows if r["kind"] == "web"),
        # 没装、但有官网下载 —— 这些是「去装」而不是「能加」
        "need_install": sum(1 for r in rows if not r["kind"] and not r["have"]),
    }


def mark(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给 appscan 的扫描结果打标：needed / group / why。

    顺手把命中项排到前面 —— 「＋」列表第一屏就该是用户真正想加的那些。
    """
    out: list[dict[str, Any]] = []
    for raw in items:
        it = dict(raw)
        hit = find(it.get("exe") or it.get("name") or "")
        it["needed"] = bool(hit)
        if hit:
            # 不改 group：那是扫描器分好的「浏览器/通讯/开发…」，界面靠它排序。
            # 清单自己的分组单独放一个字段，只用于展示。
            it["db_group"] = hit.get("group") or ""
            it["why"] = hit.get("why") or ""
        out.append(it)
    out.sort(key=lambda x: (not x.get("needed"), (x.get("name") or "").lower()))
    return out


def web_only() -> list[dict[str, Any]]:
    """只有网页版、没桌面客户端的那些（说明用，不能匹配进程）。"""
    return [e for e in entries() if not (e.get("exe") or [])]


def save_override(data: dict[str, Any]) -> Path:
    """把一份清单写到数据目录（盖过内置的）。更新在线清单时用。"""
    path = override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _cache.update(stamp=None, data=None)
    return path


def update_from(url: str, timeout: float = 20.0) -> str:
    """从网址拉一份同格式的清单覆盖内置版（自建镜像/社区维护版都行）。"""
    from . import subscription

    text, _ = subscription.fetch(url, timeout=timeout)
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
        raise ValueError("这份清单格式不对：顶层要是对象，且含 apps 数组")
    path = save_override(data)
    return f"已更新到 {path}（{len(data['apps'])} 条）"


def summary() -> str:
    n_exe = sum(1 for e in entries() if e.get("exe"))
    return f"{len(entries())} 条（其中 {n_exe} 条可按进程识别）"

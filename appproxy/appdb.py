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

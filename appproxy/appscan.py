# -*- coding: utf-8 -*-
"""扫描本机已安装的程序 —— 给「从程序列表里挑一个建档案」用。

来源：开始菜单里的 .lnk（用户 + 公共两处），解析出目标 exe。
      比扫注册表 Uninstall 键靠谱：那里全是卸载程序，而不是能启动的程序。

解析 .lnk 的 COM 对象开销大，所以：
    * 全程复用同一个 WScript.Shell；
    * 结果缓存到 `data\\apps.json`，托盘菜单直接用缓存，重新扫描放后台。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Iterable

from . import settings

# 明显不是「要代理的程序」的东西
SKIP_WORDS = (
    "uninstall", "卸载", "readme", "帮助", "help", "更新", "update", "upgrade",
    "changelog", "release notes", "license", "许可", "documentation", "文档",
    "官方网站", "官网", "website", "manual", "手册", "repair", "修复",
    "modify", "更改", "setup", "安装", "cmd.exe", "命令提示符", "系统配置",
    "属性设置", "设置向导",
)

# 这些 exe 出现在开始菜单里也没意义（要么是我们自己，要么是系统工具/壳）
SKIP_EXES = {"python.exe", "pythonw.exe", "appproxy.exe", "unins000.exe", "uninst.exe",
             "chrome_proxy.exe", "powershell.exe", "pwsh.exe", "wscript.exe",
             "cscript.exe", "mshta.exe", "conhost.exe", "rundll32.exe"}

GROUP_ORDER = ("浏览器", "通讯", "开发", "办公", "影音", "工具", "游戏", "其他")

# 按名字/路径猜分类（开始菜单的文件夹名是厂商名，不是分类）
GROUP_WORDS = {
    "浏览器": ("chrome", "edge", "firefox", "brave", "opera", "vivaldi", "thorium",
               "浏览器", "browser", "360se", "qqbrowser", "sogou"),
    "通讯": ("wechat", "微信", "qq", "telegram", "discord", "slack", "whatsapp",
             "zoom", "teams", "skype", "line", "飞书", "feishu", "钉钉", "dingtalk"),
    "开发": ("code", "visual studio", "pycharm", "idea", "git", "terminal", "putty",
             "xshell", "navicat", "postman", "docker", "android", "unity", "node"),
    "办公": ("word", "excel", "powerpoint", "office", "wps", "onenote", "acrobat",
             "pdf", "obsidian", "notion", "typora"),
    "影音": ("potplayer", "vlc", "spotify", "剪映", "kap", "obs", "premiere", "网易云",
             "music", "video", "播放器"),
    "游戏": ("steam", "epic", "game", "游戏", "wegame"),
}


class ScanError(RuntimeError):
    pass


def start_menu_dirs() -> list[Path]:
    import os
    dirs: list[Path] = []
    appdata, programdata = os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")
    if appdata:
        dirs.append(Path(appdata) / r"Microsoft\Windows\Start Menu\Programs")
    if programdata:
        dirs.append(Path(programdata) / r"Microsoft\Windows\Start Menu\Programs")
    return [d for d in dirs if d.is_dir()]


# --------------------------------------------------------------------------- 解析
def _com_resolver():
    """返回一个可复用的 .lnk 解析函数；COM 不可用时返回 None。"""
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:  # noqa: BLE001
        return None

    def resolve(lnk: Path) -> str | None:
        try:
            sc = shell.CreateShortCut(str(lnk))
            target = (sc.TargetPath or "").strip()
            if target.lower().endswith(".exe") and Path(target).is_file():
                return target
        except Exception:  # noqa: BLE001
            return None
        return None

    return resolve


def guess_group(name: str, exe: str) -> str:
    low = f"{name} {Path(exe).name}".lower()
    for group, words in GROUP_WORDS.items():
        if any(w in low for w in words):
            return group
    return "工具"


def iter_links(dirs: Iterable[Path] | None = None) -> Iterable[Path]:
    """产出开始菜单下所有 .lnk 路径。"""
    for root in (dirs if dirs is not None else start_menu_dirs()):
        yield from root.rglob("*.lnk")


def _skip(name: str, exe: str) -> bool:
    low = (name + " " + exe).lower()
    if Path(exe).name.lower() in SKIP_EXES:
        return True
    return any(w in low for w in SKIP_WORDS)


def scan(*, resolver: Callable[[Path], str | None] | None = None,
         dirs: list[Path] | None = None) -> list[dict]:
    """扫一遍开始菜单，返回可启动的程序列表（按 exe 去重）。"""
    resolve = resolver or _com_resolver()
    if resolve is None:
        raise ScanError("读不了快捷方式（需要 win32com），改用 `profile add --exe <路径>` 手动指定")
    found: dict[str, dict] = {}
    for lnk in iter_links(dirs):
        exe = resolve(lnk)
        if not exe:
            continue
        name = lnk.stem
        if _skip(name, exe):
            continue
        key = exe.lower()
        if key in found:
            continue
        found[key] = {"name": name, "exe": exe, "group": guess_group(name, exe)}
    items = list(found.values())
    items.sort(key=lambda d: (GROUP_ORDER.index(d["group"]) if d["group"] in GROUP_ORDER
                              else len(GROUP_ORDER), d["name"].lower()))
    return items


# --------------------------------------------------------------------------- 缓存
def cache_path() -> Path:
    return settings.data_dir() / "apps.json"


def save_cache(items: list[dict]) -> Path:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"updated": int(time.time()), "count": len(items),
                                "apps": items}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def load_cache() -> list[dict]:
    path = cache_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return raw.get("apps") or []


def cached_or_scan(refresh: bool = False) -> list[dict]:
    if not refresh:
        cached = load_cache()
        if cached:
            return cached
    items = scan()
    save_cache(items)
    return items


def grouped(items: list[dict], per_group: int = 12) -> dict[str, list[dict]]:
    """按分组切好，每组最多 per_group 个（托盘菜单别太长）。"""
    out: dict[str, list[dict]] = {}
    for it in items:
        bucket = out.setdefault(it.get("group") or "其他", [])
        if len(bucket) < per_group:
            bucket.append(it)
    return {g: out[g] for g in sorted(out, key=lambda g: GROUP_ORDER.index(g)
                                      if g in GROUP_ORDER else len(GROUP_ORDER))}

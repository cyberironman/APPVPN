# -*- coding: utf-8 -*-
"""在本机找出「清单里这个程序装在哪」。

为什么不能只靠开始菜单
    `appscan` 解析的是开始菜单的 .lnk。三种情况会漏：
      1. 绿色版 / 便携版 —— 根本没有快捷方式；
      2. 微软商店版（Store）—— 装在 WindowsApps 里，那目录不允许枚举；
      3. 装完没重启 —— 开始菜单缓存还没刷新。
    而「常用软件」这一屏必须做到「点一下就加」，不能因为找不到路径就卡住。

三种来源，按可靠性排序
    1. 注册表 `App Paths\\<exe>` —— 官方安装包几乎都会写，最权威；
    2. 开始菜单缓存（`appscan` 扫过的那份）—— 有精确路径；
    3. 常见安装根的浅层扫描 —— 兜住绿色版和厂商自定义目录。

扫描代价
    只在缓存缺失或过期（默认 7 天）时做一次，结果写 `data\\exe_index.json`。
    深度限制 4 层、只记文件名不 stat，几千个 exe 大约 1~2 秒，后台跑。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Iterable

from . import settings

CACHE_NAME = "exe_index.json"
MAX_AGE = 7 * 86400          # 索引多久算过期
MAX_DEPTH = 4                # 往下钻几层

# 这些目录要么是数据不是程序，要么量大到没意义，直接跳过
SKIP_DIRS = {
    "windowsapps", "$recycle.bin", "system volume information", "node_modules",
    ".git", "__pycache__", "cache", "caches", "temp", "tmp", "logs", "log",
    "crashpad", "crashreports", "installer", "installers", "uninstall",
    "update", "updates", "backup", "backups", "resources", "locales",
    "webcache", "gpucache", "service worker",
    # 自动化工具自带的浏览器/运行时不认识用户，装在这里是给人跑脚本用的
    "ms-playwright", "playwright", "puppeteer", "selenium", "webdriver",
}

# 上面那些工具也可能装在别的地方，路径里带这些字样的一律当「可疑」，
# 只有在找不到正常版本时才拿来兜底 —— 它们的目录会被工具升级时整片删掉。
_SUSPECT_MARKERS = (
    "\\ms-playwright\\", "\\playwright\\", "\\puppeteer\\",
    "\\selenium\\", "\\webdriver\\", "\\appdata\\local\\temp\\",
)
_SUSPECT_RE = re.compile(r"\\chromium-\d+|\\node_modules\\|\\\.cache\\", re.I)

# 浅扫的根目录（顺序即优先级：用户级装的一般比系统级的新）
_ROOT_ENVS = (
    ("LOCALAPPDATA", "Programs"),
    ("LOCALAPPDATA", ""),
    ("PROGRAMFILES", ""),
    ("PROGRAMFILES(X86)", ""),
    ("APPDATA", ""),
    ("PROGRAMDATA", ""),
)

# App Paths 在注册表里的位置（64 位与 32 位视图各一份）
_APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"

_lock = {"held": False}


def cache_path() -> Path:
    return settings.data_dir() / CACHE_NAME


def _norm(name: str) -> str:
    """统一成小写文件名，带 .exe。"""
    n = (name or "").replace("\\", "/").rstrip("/").split("/")[-1].strip().lower()
    if n and not n.endswith(".exe"):
        n += ".exe"
    return n


# 微软商店（Store）装的应用落地在 WindowsApps 下。那个目录受 ACL 保护，
# 直接用 CreateProcess 拉起来会 Access Denied —— 只能通过 shell 激活。
# 而 shell 激活没法塞 `--proxy-server` 之类的参数，对我们毫无意义，
# 所以一律视为「不可用」，改提示用户装官网桌面版。
_BLOCKED = ("\\windowsapps\\", "/windowsapps/")


def is_store(path: str | Path) -> bool:
    low = str(path).replace("/", "\\").lower()
    return "\\windowsapps\\" in low


def usable(path: str | None) -> bool:
    if not path:
        return False
    if is_store(path):
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


def suspect(path: str | None) -> bool:
    """这个 exe 是不是「工具自己带的」，不该推荐给用户当默认浏览器。"""
    if not path:
        return True
    low = str(path).replace("/", "\\").lower()
    if any(m in low for m in _SUSPECT_MARKERS):
        return True
    return bool(_SUSPECT_RE.search(low))


def roots() -> list[Path]:
    out: list[Path] = []
    for env, sub in _ROOT_ENVS:
        base = os.environ.get(env)
        if not base:
            continue
        p = Path(base) / sub if sub else Path(base)
        if p.is_dir():
            out.append(p)
    # 去重但保序
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


# --------------------------------------------------------------------------- 注册表
def _from_registry(name: str) -> str | None:
    """查 App Paths，HKCU 优先（用户装的比系统装的更可能是想要的）。"""
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, f"{_APP_PATHS}\\{name}",
                                    0, winreg.KEY_READ | view) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    val = str(val).strip().strip('"')
                    if val.lower().endswith(".exe") and Path(val).is_file():
                        return val
            except OSError:
                continue
    return None


# --------------------------------------------------------------------------- 浅扫
def _walk(root: Path, depth: int = 0) -> Iterable[Path]:
    if depth > MAX_DEPTH:
        return
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False):
                if e.name.lower() not in SKIP_DIRS:
                    yield from _walk(Path(e.path), depth + 1)
            elif e.is_file(follow_symlinks=False) and e.name.lower().endswith(".exe"):
                yield Path(e.path)
        except OSError:
            continue


def scan_roots(extra_roots: Iterable[Path] | None = None) -> dict[str, list[str]]:
    """扫常见安装根，返回 {exe 文件名: [路径…]}。"""
    table: dict[str, list[str]] = {}
    for root in (list(extra_roots) if extra_roots is not None else roots()):
        for exe in _walk(root):
            table.setdefault(exe.name.lower(), []).append(str(exe))
    return table


# --------------------------------------------------------------------------- 索引
def _merge_shortcuts(table: dict[str, list[str]]) -> None:
    """把开始菜单缓存里的精确路径并进来（绿色版多半靠这一条）。"""
    try:
        from . import appscan
        for it in appscan.load_cache():
            exe = (it.get("exe") or "").strip()
            if exe:
                key = Path(exe).name.lower()
                bucket = table.setdefault(key, [])
                if exe not in bucket:
                    bucket.insert(0, exe)     # 快捷方式给的路径最准，排前面
    except Exception:  # noqa: BLE001
        pass


def build(extra_roots: Iterable[Path] | None = None) -> dict[str, list[str]]:
    table = scan_roots(extra_roots)
    _merge_shortcuts(table)
    data = {"built": int(time.time()), "count": len(table),
            "roots": [str(p) for p in (roots() if extra_roots is None else extra_roots)],
            "map": table}
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
    return table


def load(force: bool = False) -> dict[str, list[str]]:
    """读索引；缺失或过期就重建。"""
    path = cache_path()
    if not force:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if int(raw.get("built") or 0) + MAX_AGE > time.time():
                m = raw.get("map")
                if isinstance(m, dict):
                    return {str(k): list(v) for k, v in m.items() if isinstance(v, list)}
        except (OSError, ValueError, TypeError):
            pass
    return build()


def stale() -> bool:
    try:
        raw = json.loads(cache_path().read_text(encoding="utf-8"))
        return int(raw.get("built") or 0) + MAX_AGE <= time.time()
    except (OSError, ValueError, TypeError):
        return True


def clear() -> None:
    try:
        cache_path().unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- 查
def locate(name: str, table: dict[str, list[str]] | None = None) -> str | None:
    """给一个 exe 名（或路径），返回本机真实可用、能带参数启动的完整路径。

    商店版（WindowsApps 下）会被跳过 —— 它只能用 shell 激活，
    塞不进 `--proxy-server`，对我们没有意义。
    """
    if not name:
        return None
    if os.path.sep in name or "/" in name:
        return name if usable(name) else None
    key = _norm(name)
    if not key:
        return None
    # 显式传了 table 就只在表里找（测试与批量场景要可预期）；
    # 不传才动用注册表 + 缓存索引这套完整逻辑。
    if table is None:
        hit = _from_registry(key)
        if usable(hit):
            return hit
        t = load()
    else:
        t = table
    # 同一个 exe 名可能落在好几个地方（真浏览器 + 自动化工具自带的）。
    # 先给「干净」的那些，实在没有才退回可疑路径 —— 总比找不到好。
    cands = [c for c in (t.get(key) or []) if usable(c)]
    for c in cands:
        if not suspect(c):
            return c
    return cands[0] if cands else None


def locate_any(names: Iterable[str],
               table: dict[str, list[str]] | None = None) -> str | None:
    t = table if table is not None else load()
    for n in names or ():
        hit = locate(n, t)
        if hit:
            return hit
    return None


def store_only(names: Iterable[str],
               table: dict[str, list[str]] | None = None) -> str | None:
    """本机只有商店版时返回那个路径，用来把提示说得更准。"""
    t = table if table is not None else load()
    for n in names or ():
        key = _norm(n)
        for cand in ([_from_registry(key)] + list(t.get(key) or [])):
            if cand and is_store(cand):
                return cand
    return None


# 「网页版」条目要建浏览器档案时，按这个顺序挑一个本机有的
BROWSER_ORDER = ("chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe",
                 "chromium.exe", "firefox.exe")

# 常见浏览器的标准安装位置。比「翻索引碰运气」可靠得多 ——
# 索引里同一个 chrome.exe 可能先命中 Playwright / Puppeteer 自带的那份，
# 那个目录会被工具升级时整片删掉，用它建的快捷方式过几天就点了没反应。
_BROWSER_PATHS = (
    r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
    r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
    r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
    r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%PROGRAMFILES%\Vivaldi\Application\vivaldi.exe",
    r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe",
    r"%PROGRAMFILES%\Mozilla Firefox\firefox.exe",
    r"%PROGRAMFILES(X86)%\Mozilla Firefox\firefox.exe",
)


def browser(table: dict[str, list[str]] | None = None) -> str | None:
    """本机用来开网页的浏览器（标准安装位置优先，其次索引）。"""
    if table is None:
        for tpl in _BROWSER_PATHS:
            hit = os.path.expandvars(tpl)
            if usable(hit) and not suspect(hit):
                return hit
    hit = locate_any(BROWSER_ORDER, table)
    return hit if usable(hit) else None

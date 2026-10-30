# -*- coding: utf-8 -*-
"""应用档案（App Profile）：一个程序 + 一条线路 + 一份独立数据目录 = 一个独立图标。

与「全局代理」的根本区别
    全局代理：系统里只有一套代理设置，所有程序一起走，想换只能整体换。
    应用档案：每个档案自己带着代理参数启动，互不影响，可以同时开着、各走各的。

为什么每个档案必须独占一个端口（本产品的关键设计）
    sing-box 的 `process_name` 规则按**进程名**匹配。同一个 exe 开两个实例，
    进程名完全相同（chrome.exe / chrome.exe），内核在规则层面无法区分它们。
    所以「同一个软件、多个代理、多个图标」不能只靠按进程分流，
    必须让每个档案连**自己的本地端口** —— 端口不同，上游线路就不同。
    （进程名分流仍然有用：一个端口覆盖一整类程序，见 singconf.build_dispatcher。）

本模块只做纯计算（拼参数、算路径），不启动进程、不写磁盘以外的副作用，
方便单测。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import settings
from .models import Line

# 认得出「能用 --user-data-dir / --proxy-server 参数」的 Chromium 系浏览器
CHROMIUM_NAMES = {
    "chrome.exe", "chromium.exe", "msedge.exe", "brave.exe", "vivaldi.exe",
    "thorium.exe", "opera.exe", "slimjet.exe", "yandex.exe", "360se.exe",
    "360chrome.exe", "qqbrowser.exe", "sogouexplorer.exe", "maxthon.exe",
}

# Chromium 系必须隔离的参数：不同 --user-data-dir 才允许多开
CHROMIUM_BASE_ARGS = ("--no-first-run", "--no-default-browser-check")

ENV_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy")

# --------------------------------------------------------------------------- 多开隔离
# 同一个软件开两个实例，默认会共用 %APPDATA% 里那一套配置：轻则点第二个图标
# 只是把第一个的窗口顶到前面（根本没多开），重则两边互相覆盖登录态、聊天记录、
# 草稿箱。唯一的正解是让程序把「自己的用户数据」放进档案自己的目录 ——
# 也就是 Chrome 快捷方式那一套 `--user-data-dir`，只不过要按程序各自支持的写法来。
#
# 代价说清楚：磁盘上每个档案多一份完整用户数据（Chrome 一个档案几百 MB 起）。
# 换来的是**真的互不干扰**：可以同时登录两个账号、一个崩了另一个不受影响、
# 删掉某个档案不会碰其他档案的数据。这是用户明确要的取舍。
#
# 表里没有的程序，就是它不接受外部指定数据目录。**不硬来** —— 塞个它不认的
# 参数，运气好被忽略，运气差直接起不来。这种情况老老实实告诉用户
# 「这个软件没法真多开，两个档案会共用配置」。
DATA_DIR_ARGS: dict[str, tuple[str, ...]] = {
    # Telegram Desktop 自己的写法
    "telegram.exe": ("-workdir", "{dir}"),
    # Electron 应用继承 Chromium 的开关，通用
    "whatsapp.exe": ("--user-data-dir={dir}",),
    "discord.exe": ("--user-data-dir={dir}",),
    "slack.exe": ("--user-data-dir={dir}",),
    "notion.exe": ("--user-data-dir={dir}",),
    "figma.exe": ("--user-data-dir={dir}",),
    "obsidian.exe": ("--user-data-dir={dir}",),
    "lm studio.exe": ("--user-data-dir={dir}",),
    "chatgpt.exe": ("--user-data-dir={dir}",),
    "claude.exe": ("--user-data-dir={dir}",),
    "code.exe": ("--user-data-dir", "{dir}"),
    "cursor.exe": ("--user-data-dir={dir}",),
    "windsurf.exe": ("--user-data-dir={dir}",),
    "postman.exe": ("--user-data-dir={dir}",),
    "spotify.exe": ("--user-data-dir={dir}",),
    # Firefox / Thunderbird 要配 -no-remote，否则第二个实例只会唤起第一个
    "firefox.exe": ("-profile", "{dir}", "-no-remote"),
    "thunderbird.exe": ("-profile", "{dir}", "-no-remote"),
}

ISOLATE_MODES = ("auto", "full", "off")


def isolation(exe: str | Path, kind: str = "auto") -> dict[str, Any]:
    """这个程序怎么隔离用户数据？返回 {mode, tpl, note, can}。

    mode:
        user-data-dir  Chromium / Electron 系，最干净
        native         程序自己的参数（-workdir / -profile …）
        env            没法带参数，只能试着重定向 APPDATA（不保证有效）
        none           什么都不做
    """
    name = Path(str(exe)).name.lower()
    k = detect_kind(exe) if kind in ("", "auto") else kind

    if k == "chromium":
        return {"mode": "user-data-dir", "can": True,
                "tpl": ("--user-data-dir={dir}",),
                "note": "Chromium 系，每个档案一份独立用户数据，可同时登录多个账号"}
    tpl = DATA_DIR_ARGS.get(name)
    if tpl:
        return {"mode": "native", "can": True, "tpl": tpl,
                "note": f"{Path(str(exe)).stem} 自带指定数据目录的启动参数，可以真多开"}
    return {"mode": "none", "can": False, "tpl": (),
            "note": "这个程序不接受外部指定数据目录，多个档案会共用它自己的配置"} 


def detect_kind(exe: str | Path) -> str:
    """按 exe 文件名猜类型：chromium（浏览器，支持参数级代理）或 generic。"""
    name = Path(str(exe)).name.lower()
    return "chromium" if name in CHROMIUM_NAMES else "generic"


def slug(text: str) -> str:
    """档案 id 只允许安全字符，避免路径里出现中文标点或空格。"""
    s = re.sub(r"[^0-9A-Za-z_.-]+", "-", (text or "").strip()).strip("-_. ")
    return s.lower() or "profile"


@dataclass
class AppProfile:
    id: str
    name: str
    exe: str
    line_id: str = ""            # 绑定的线路 id；留空 = 直连（不走代理）
    kind: str = "auto"           # auto / chromium / generic
    data_dir: str = ""           # 档案独立数据目录；留空则用 apps/<id>/data
    args: list[str] = field(default_factory=list)
    url: str = ""                # 启动后打开的地址（浏览器常用）
    icon: str = ""               # 生成的 .ico
    shortcut: str = ""           # 最近一次生成的 .lnk
    created: int = 0
    isolate: str = "auto"        # auto / full / off —— 多开时怎么隔离用户数据
    preset: bool = False         # 首次启动自动建的（用户删了就不再长回来）

    def __post_init__(self) -> None:
        self.id = slug(self.id)
        self.name = self.name or self.id
        self.kind = (self.kind or "auto").lower()
        if self.kind not in ("auto", "chromium", "generic"):
            raise ValueError(f"kind 只能是 auto / chromium / generic：{self.kind}")
        self.isolate = (self.isolate or "auto").lower()
        if self.isolate not in ISOLATE_MODES:
            self.isolate = "auto"
        if not self.created:
            self.created = int(time.time())
        if isinstance(self.args, str):  # 从命令行/JSON 传进来的字符串
            self.args = [a for a in self.args.split("\x1f") if a]

    # ---------------------------------------------------------------- 派生
    @property
    def resolved_kind(self) -> str:
        return detect_kind(self.exe) if self.kind == "auto" else self.kind

    @property
    def is_direct(self) -> bool:
        return not self.line_id

    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else (settings.apps_dir() / self.id / "data")

    def icon_path(self) -> Path:
        return Path(self.icon) if self.icon else (settings.icons_dir() / f"{self.id}.ico")

    def exe_path(self) -> Path:
        return Path(self.exe)

    def exists(self) -> bool:
        return self.exe_path().is_file()

    def short_code(self) -> str:
        """图标角标上的短码：直连 D，其他取线路 id 前 3 位。"""
        return "D" if self.is_direct else (self.line_id or "?")[:3].upper()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AppProfile":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class LaunchPlan:
    """启动一个档案要用的全部信息（可打印、可测试，不直接执行）。"""

    argv: list[str]
    env: dict[str, str]
    cwd: str
    proxy: str = ""              # 展示用，如 http://127.0.0.1:10801
    kind: str = "chromium"
    iso_mode: str = "none"       # user-data-dir / native / env / none
    iso_note: str = ""           # 人话说明，直接显示在界面上

    def command_line(self) -> str:
        return " ".join(f'"{a}"' if " " in a else a for a in self.argv)


def plan(profile: AppProfile, line: Line | None = None) -> LaunchPlan:
    """把「档案 + 线路」翻译成一条启动命令。

    line 为 None 或线路没有端口 -> 直连档案（浏览器显式 --no-proxy-server，
    免得上一次留下的系统代理又把它带走）。

    数据隔离在这里落地：把程序自己的用户数据指到 `apps/<id>/data`，
    这样同一个软件的两个档案才真的是两个独立实例。
    """
    exe = str(profile.exe_path())
    port = int(line.http_port or 0) if line else 0
    proxy = f"http://127.0.0.1:{port}" if port else ""
    kind = profile.resolved_kind
    env: dict[str, str] = {}
    argv = [exe]

    iso = isolation(profile.exe, kind)
    data = profile.data_path()
    iso_mode, iso_note = "none", iso["note"]

    if profile.isolate == "off":
        iso_mode = "none"
        iso_note = "已关闭数据隔离：多个档案会共用这个软件自己的配置"
    elif iso["can"]:
        iso_mode = iso["mode"]
        for tpl in iso["tpl"]:
            argv.append(tpl.replace("{dir}", str(data)))
    elif profile.isolate == "full":
        # 程序不认数据目录参数时，最后的手段是把两个已知文件夹掰到档案目录。
        # 只对这个进程有效（CreateProcess 带自己的环境块）；但如果程序用
        # SHGetFolderPath 读注册表，它照样看系统那份 —— 所以只能算尽力而为，
        # 界面上也要如实说，不能让人以为已经隔离干净了。
        iso_mode = "env"
        iso_note = ("这个程序不接受数据目录参数，已尝试重定向 APPDATA / "
                    "LOCALAPPDATA（不保证生效）")
        env["APPDATA"] = str(data / "Roaming")
        env["LOCALAPPDATA"] = str(data / "Local")
        env["USERPROFILE"] = str(data / "Profile")

    if kind == "chromium":
        argv.extend(CHROMIUM_BASE_ARGS)
        if port:
            argv.append(f"--proxy-server={proxy}")
        else:
            argv.append("--no-proxy-server")
    else:
        for key in ENV_PROXY_KEYS:
            env[key] = proxy

    argv.extend(profile.args)
    if profile.url:
        argv.append(profile.url)

    return LaunchPlan(argv=argv, env=env, cwd=str(profile.exe_path().parent),
                      proxy=proxy, kind=kind,
                      iso_mode=iso_mode, iso_note=iso_note)


# --------------------------------------------------------------------------- 存储
class ProfileStore:
    """profiles.json：应用档案表。与线路表分开存，互不干扰。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else settings.profiles_path()
        self.profiles: list[AppProfile] = []
        # 预置清单做到第几版了（见 preset.py）。0 = 从没预置过。
        self.preset_version = 0
        # 用户主动删掉的预置条目。删了就等于说「我不要这个」，
        # 以后升级预置清单也不能把它长回来（只能用户自己在设置里点恢复）。
        self.preset_removed: list[str] = []

    def load(self) -> "ProfileStore":
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            self.preset_version = int(raw.get("preset_version") or 0)
            removed = raw.get("preset_removed") or []
            self.preset_removed = [str(x) for x in removed if x]
            for item in raw.get("profiles") or []:
                try:
                    self.profiles.append(AppProfile.from_dict(item))
                except (TypeError, ValueError):
                    continue
        return self

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"version": 1, "preset_version": self.preset_version,
                       "preset_removed": self.preset_removed,
                       "profiles": [p.to_dict() for p in self.profiles]},
                      f, ensure_ascii=False, indent=2)
        return self.path

    def get(self, pid: str) -> AppProfile | None:
        return next((p for p in self.profiles if p.id == pid), None)

    def require(self, pid: str) -> AppProfile:
        p = self.get(pid)
        if p is None:
            raise KeyError(f"没有这个应用档案：{pid}（先 profile add）")
        return p

    def upsert(self, profile: AppProfile) -> AppProfile:
        for i, old in enumerate(self.profiles):
            if old.id == profile.id:
                self.profiles[i] = profile
                return profile
        self.profiles.append(profile)
        return profile

    def remove(self, pid: str) -> bool:
        before = len(self.profiles)
        gone = next((p for p in self.profiles if p.id == pid), None)
        self.profiles = [p for p in self.profiles if p.id != pid]
        if gone is not None and gone.preset and pid not in self.preset_removed:
            # 删掉预置条目 = 明确表示「不要这个」。记下来，以后升级预置清单
            # 也不能把它长回来 —— 用户想找回可以自己在设置里点「补回来」。
            self.preset_removed.append(pid)
        return len(self.profiles) != before

    def by_line(self, line_id: str) -> list[AppProfile]:
        return [p for p in self.profiles if p.line_id == line_id]

    def unique_id(self, base: str) -> str:
        base = slug(base)
        if not self.get(base):
            return base
        n = 2
        while self.get(f"{base}-{n}"):
            n += 1
        return f"{base}-{n}"

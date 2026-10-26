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

    def __post_init__(self) -> None:
        self.id = slug(self.id)
        self.name = self.name or self.id
        self.kind = (self.kind or "auto").lower()
        if self.kind not in ("auto", "chromium", "generic"):
            raise ValueError(f"kind 只能是 auto / chromium / generic：{self.kind}")
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

    def command_line(self) -> str:
        return " ".join(f'"{a}"' if " " in a else a for a in self.argv)


def plan(profile: AppProfile, line: Line | None = None) -> LaunchPlan:
    """把「档案 + 线路」翻译成一条启动命令。

    line 为 None 或线路没有端口 -> 直连档案（浏览器显式 --no-proxy-server，
    免得上一次留下的系统代理又把它带走）。
    """
    exe = str(profile.exe_path())
    port = int(line.http_port or 0) if line else 0
    proxy = f"http://127.0.0.1:{port}" if port else ""
    kind = profile.resolved_kind
    env: dict[str, str] = {}
    argv = [exe]

    if kind == "chromium":
        argv.append(f"--user-data-dir={profile.data_path()}")
        argv.extend(CHROMIUM_BASE_ARGS)
        if port:
            argv.append(f"--proxy-server={proxy}")
        else:
            argv.append("--no-proxy-server")
        argv.extend(profile.args)
        if profile.url:
            argv.append(profile.url)
    else:
        argv.extend(profile.args)
        if profile.url:
            argv.append(profile.url)
        if port:
            for key in ENV_PROXY_KEYS:
                env[key] = proxy
        else:
            for key in ENV_PROXY_KEYS:
                env[key] = ""

    return LaunchPlan(argv=argv, env=env, cwd=str(profile.exe_path().parent),
                      proxy=proxy, kind=kind)


# --------------------------------------------------------------------------- 存储
class ProfileStore:
    """profiles.json：应用档案表。与线路表分开存，互不干扰。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else settings.profiles_path()
        self.profiles: list[AppProfile] = []

    def load(self) -> "ProfileStore":
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
            for item in raw.get("profiles") or []:
                try:
                    self.profiles.append(AppProfile.from_dict(item))
                except (TypeError, ValueError):
                    continue
        return self

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"version": 1, "profiles": [p.to_dict() for p in self.profiles]},
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
        self.profiles = [p for p in self.profiles if p.id != pid]
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

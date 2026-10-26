# -*- coding: utf-8 -*-
"""路径与默认值解析。

设计约束（踩过的坑）：
    * 全部函数式解析，不做模块级常量缓存 —— 便于测试用 monkeypatch 换根目录；
    * 只用标准库，不依赖第三方；
    * 默认根目录在 D 盘，不写 C 盘用户目录。
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "APPPROXY_HOME"
# 运行数据（注册表 / 配置 / 日志）统一放 D 盘独立目录，和代码分开 ——
# 订阅地址带 token（访问令牌），不能跟着代码一起被复制、打包、提交。
DEFAULT_HOME = Path(r"D:\AppProxy")

# xray 常见安装位置（按优先级）。装了别处的用 APPPROXY_XRAY 环境变量覆盖，
# 或者把路径写进注册表的 defaults.xray。
XRAY_CANDIDATES = (
    r"C:\Program Files\v2rayN\bin\xray\xray.exe",
    r"C:\Program Files\v2rayN\bin\Xray\xray.exe",
    r"D:\v2rayN\bin\xray\xray.exe",
    r"C:\Program Files\v2rayN\bin\xray.exe",
)

DEFAULT_HEALTH_URL = "https://www.gstatic.com/generate_204"


def home() -> Path:
    p = os.environ.get(ENV_HOME)
    return Path(p) if p else DEFAULT_HOME


def code_root() -> Path:
    """源码根目录（专利文件夹里的这一份）。"""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return home() / "data"


def runtime_dir() -> Path:
    """xray 运行配置与 pid/状态文件。"""
    return data_dir() / "runtime"


def logs_dir() -> Path:
    return home() / "logs"


def registry_path() -> Path:
    return data_dir() / "lines.json"


# ---------------------------------------------------------------- 应用档案（步骤 2）
def apps_dir() -> Path:
    """每个应用档案的独立数据目录都放这里（浏览器的 user-data-dir）。"""
    return home() / "apps"


def icons_dir() -> Path:
    """为每个档案生成的图标（同软件多图标靠它区分）。"""
    return home() / "icons"


def shortcuts_dir() -> Path:
    """生成的快捷方式母版；桌面上再放副本。"""
    return home() / "shortcuts"


def profiles_path() -> Path:
    return data_dir() / "profiles.json"


def desktop_dirs() -> list[Path]:
    """本机真实可见的桌面。

    踩过的坑：本机 `C:\\Users\\<user>\\Desktop` 和 `D:\\OneDrive\\Desktop` 同时存在，
    用户实际看到的是 OneDrive 那个，所以两处都放一份（去重）。
    """
    seen: list[Path] = []
    for c in (Path(os.environ.get("OneDrive", r"D:\OneDrive")) / "Desktop",
              Path.home() / "Desktop"):
        if c.is_dir() and c not in seen:
            seen.append(c)
    return seen


def ensure_dirs() -> None:
    for d in (data_dir(), runtime_dir(), logs_dir(), apps_dir(), icons_dir(), shortcuts_dir()):
        d.mkdir(parents=True, exist_ok=True)


def find_xray(explicit: str | None = None) -> Path | None:
    """显式路径 > 环境变量 APPPROXY_XRAY > 已知候选 > PATH 查找。"""
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
    env = os.environ.get("APPPROXY_XRAY")
    if env and Path(env).is_file():
        return Path(env)
    for c in XRAY_CANDIDATES:
        p = Path(c)
        if p.is_file():
            return p
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        p = Path(d) / "xray.exe"
        if p.is_file():
            return p
    return None


def third_party_dir() -> Path:
    return home() / "third_party"


def find_singbox(explicit: str | None = None) -> Path | None:
    """sing-box 是我们直接带过来的开源内核，优先用 third_party 里那份。"""
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
    env = os.environ.get("APPPROXY_SINGBOX")
    if env and Path(env).is_file():
        return Path(env)
    for base in (third_party_dir() / "sing-box", code_root() / "third_party" / "sing-box"):
        if not base.is_dir():
            continue
        for p in sorted(base.glob("*/sing-box.exe"), reverse=True):
            if p.is_file():
                return p
        direct = base / "sing-box.exe"
        if direct.is_file():
            return direct
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        p = Path(d) / "sing-box.exe"
        if p.is_file():
            return p
    return None


def find_engine(engine: str = "singbox", explicit: str | None = None) -> Path | None:
    if (engine or "").lower().startswith("sing"):
        return find_singbox(explicit)
    return find_xray(explicit)

# -*- coding: utf-8 -*-
"""Windows 系统代理开关（注册表 + 立即生效广播）。

为什么要它：
    有些程序既不吃 `--proxy-server` 参数，也不读 `HTTP_PROXY` 环境变量
    （大量国产客户端）。对这种程序，唯一可行的「引导」就是临时把系统代理指向
    某条线路的本地端口。

设计原则：
    * 只动当前用户（HKCU），不需要管理员权限；
    * 开启前**先备份**原来的设置，关闭时**还原**，不把别人的代理配置弄丢；
    * 三项注册表值都要改，且必须发 InternetSetOption 广播，否则要等程序重启才生效。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import settings

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
BACKUP_NAME = "sysproxy-backup.json"

# 接管系统代理时必须跳过本机地址。
# 不写这条，连本产品自己的界面（127.0.0.1 上的本地服务）都会被送进代理，
# 代理端口一时没起来就会白屏 —— 自己把自己锁在门外。
BYPASS_LOCAL = "localhost;127.*;10.*;172.16.*;192.168.*;<local>"


# --------------------------------------------------------------------------- 纯计算
def server_for(port: int) -> str:
    """一条线路的本地端口 -> 系统代理串。单端口对 http/https 都适用。"""
    return f"127.0.0.1:{int(port)}"


def describe(state: dict[str, Any]) -> str:
    """给人看的一句话。"""
    if state.get("enable") and state.get("server"):
        return f"已开启 → {state['server']}"
    return "已关闭"


# --------------------------------------------------------------------------- 注册表
def _open(access: int):
    import winreg
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, access)


def read() -> dict[str, Any]:
    """读当前系统代理设置。读不到就返回关闭态。"""
    out: dict[str, Any] = {"enable": False, "server": "", "override": ""}
    try:
        import winreg
        with _open(winreg.KEY_READ) as k:
            for name, key in (("ProxyEnable", "enable"), ("ProxyServer", "server"),
                              ("ProxyOverride", "override")):
                try:
                    val, _ = winreg.QueryValueEx(k, name)
                    out[key] = bool(val) if key == "enable" else str(val or "")
                except FileNotFoundError:
                    continue
    except OSError:
        pass
    return out


def _write(enable: bool, server: str | None = None, override: str | None = None) -> None:
    import winreg
    with _open(winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
        if server is not None:
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
        if override is not None:
            winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, override)


def notify_system() -> None:
    """广播设置变更，让已经在跑的程序也立刻用上新代理。"""
    try:
        import ctypes
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, 39, 0, 0)   # INTERNET_OPTION_SETTINGS_CHANGED
        wininet.InternetSetOptionW(0, 37, 0, 0)   # INTERNET_OPTION_REFRESH
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- 备份
def _backup_path() -> Path:
    return settings.data_dir() / BACKUP_NAME


def save_backup(state: dict[str, Any] | None = None) -> Path:
    """把「接管前」的状态存下来（只在还没有备份时写入，避免覆盖原始值）。"""
    path = _backup_path()
    if path.exists():
        return path
    data = state if state is not None else read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_backup() -> dict[str, Any] | None:
    path = _backup_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def drop_backup() -> None:
    try:
        _backup_path().unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- 开关
def enable(port: int) -> dict[str, Any]:
    """把系统代理指向某条线路的本地端口。"""
    save_backup(read())
    server = server_for(port)
    _write(True, server, BYPASS_LOCAL)
    notify_system()
    return read()


def disable(restore: bool = True) -> dict[str, Any]:
    """关闭系统代理；有备份就还原成接管前的样子（含忽略列表）。"""
    backup = load_backup() if restore else None
    if backup:
        _write(bool(backup.get("enable", False)), backup.get("server") or "",
               backup.get("override", ""))
        drop_backup()
    else:
        _write(False)
    notify_system()
    return read()


def toggle(port: int) -> dict[str, Any]:
    state = read()
    want = server_for(port)
    if state.get("enable") and state.get("server") == want:
        return disable()
    return enable(port)


def status() -> dict[str, Any]:
    state = read()
    state["text"] = describe(state)
    state["backup"] = bool(load_backup())
    return state


# --------------------------------------------------------------------------- 校验
def parse_server(server: str) -> list[str]:
    """ProxyServer 可能是 `127.0.0.1:10801`，也可能是 `http=…;https=…;socks=…`。

    返回其中出现的所有 `host:port`（小写、去空格），便于和某条线路比对。
    """
    out: list[str] = []
    for part in str(server or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            part = part.split("=", 1)[1].strip()
        if part and part not in out:
            out.append(part.lower())
    return out


def matches_port(state: dict[str, Any], port: int) -> bool:
    """当前系统代理是不是正好指向这条线路（兼容 http=…;https=… 写法）。"""
    if not state.get("enable"):
        return False
    want = server_for(port).lower()
    return want in parse_server(state.get("server", ""))

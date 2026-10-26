# -*- coding: utf-8 -*-
"""xray 内核进程管理：起、停、查。

收尸原则：**只杀命令行里带「我们这条 config 路径」的 xray**，
所以不会误伤 v2rayN / 其他软件起的 xray。
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from . import settings
from .models import Line

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 我们只管这两个内核；其他软件起的同名进程不碰
EXE_NAMES = ("xray.exe", "sing-box.exe")


class CoreError(RuntimeError):
    pass


# --------------------------------------------------------------------------- 端口
def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout):
            return True
    except OSError:
        return False


def free_port(near: int = 0) -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", int(near) or 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- 进程
def pids_by_config(cfg: Path) -> list[int]:
    target = str(cfg).replace("/", "\\").lower()
    found: list[int] = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() not in EXE_NAMES:
                continue
            cmd = " ".join(p.info["cmdline"] or []).replace("/", "\\").lower()
            if target in cmd:
                found.append(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def kill_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


@dataclass
class Handle:
    pid: int
    cfg: Path
    log: Path


# --------------------------------------------------------------------------- 校验
def validate(cfg: Path, binary: Path, engine: str = "xray",
             timeout: float = 20.0) -> tuple[bool, str]:
    """只验配置，不监听端口：xray 用 `run -test`，sing-box 用 `check`。"""
    args = [str(binary)]
    if (engine or "").lower().startswith("sing"):
        args += ["check", "-c", str(cfg)]
    else:
        args += ["run", "-test", "-c", str(cfg)]
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "配置校验超时"
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode == 0, out[-600:]


# --------------------------------------------------------------------------- 启停
def start(xray: Path, cfg: Path, http_port: int, *, log: Path | None = None,
          wait: float = 25.0) -> Handle:
    if not Path(xray).is_file():
        raise CoreError(f"找不到 xray：{xray}")
    if not Path(cfg).is_file():
        raise CoreError(f"找不到配置文件：{cfg}")

    existing = pids_by_config(cfg)
    if existing and port_open(http_port):
        return Handle(existing[0], Path(cfg), log or Path(""))

    kill_pids(existing)
    for _ in range(20):
        if not port_open(http_port):
            break
        time.sleep(0.15)

    log = log or (settings.logs_dir() / f"{Path(xray).stem}-{Path(cfg).stem}.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as lf:
        proc = subprocess.Popen([str(xray), "run", "-c", str(cfg)],
                                cwd=str(Path(xray).parent),
                                stdout=lf, stderr=lf,
                                creationflags=CREATE_NO_WINDOW)

    deadline = time.time() + wait
    while time.time() < deadline:
        if proc.poll() is not None:
            raise CoreError(f"内核启动即退出（退出码 {proc.returncode}），看日志 {log}")
        if port_open(http_port):
            (settings.runtime_dir() / f"{Path(cfg).stem}.pid").write_text(str(proc.pid),
                                                                         encoding="utf-8")
            return Handle(proc.pid, Path(cfg), log)
        time.sleep(0.25)
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    raise CoreError(f"{wait:.0f} 秒内端口 {http_port} 未就绪，看日志 {log}")


def stop(cfg: Path) -> int:
    """按 config 路径收尸，返回结束的进程数。"""
    pids = pids_by_config(cfg)
    for pid in pids:
        try:
            psutil.Process(pid).terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    deadline = time.time() + 5
    while time.time() < deadline:
        if not pids_by_config(cfg):
            break
        time.sleep(0.2)
    kill_pids(pids_by_config(cfg))
    pid_file = settings.runtime_dir() / f"{Path(cfg).stem}.pid"
    if pid_file.exists():
        try:
            pid_file.unlink()
        except OSError:
            pass
    return len(pids)


def status(line: Line) -> dict:
    cfg = line.config_path()
    pids = pids_by_config(cfg)
    return {
        "id": line.id,
        "name": line.name,
        "mode": line.mode,
        "running": bool(pids),
        "pids": pids,
        "http_port": line.http_port,
        "port_up": port_open(line.http_port),
        "socks_port": line.socks_port,
        "socks_up": port_open(line.socks_port) if line.socks_port else None,
        "config": str(cfg),
        "config_exists": cfg.exists(),
    }


# --------------------------------------------------------------------------- 探活
def probe_via_proxy(port: int, url: str, timeout: float = 8.0) -> tuple[bool, int, int]:
    """通过本地 http 代理请求 url，返回 (是否成功, 状态码, 毫秒)。"""
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler(
        {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AppProxy"})
    t0 = time.time()
    try:
        with opener.open(req, timeout=timeout) as r:
            r.read(64)
            ms = int((time.time() - t0) * 1000)
            return r.status in (200, 204), r.status, ms
    except Exception:  # noqa: BLE001
        return False, 0, int((time.time() - t0) * 1000)

# -*- coding: utf-8 -*-
"""TUN 总控：把「档案 → 线路」翻译成一份 TUN 配置，并负责起停与系统代理交接。

三条纪律
    1. **不越权**：只有建过档案、且绑了线路的软件才走代理；其余一律直连。
    2. **不和系统代理打架**：开 TUN 前把系统代理关掉（并记住原状态），
       关 TUN 时原样还原 —— 两个同时开会让本机流量绕圈，甚至把自己锁在门外。
    3. **配置固定路径**：提权任务里写死了配置文件路径，所以永远重建 `runtime/tun.json`
       这一份，不换文件名，任务就永远有效。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import coreproc, elevate, pool, profiles, settings, sysproxy, tunconf
from .registry import Registry

STATE_NAME = "tun-state.json"
CONFIG_NAME = "tun.json"


class TunError(RuntimeError):
    pass


def config_path() -> Path:
    return settings.runtime_dir() / CONFIG_NAME


def state_path() -> Path:
    return settings.runtime_dir() / STATE_NAME


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(data: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- 收集
def collect() -> tuple[dict[str, list], dict[str, str]]:
    """返回 (groups, app_rules)。

    app_rules 的键是**进程名**（chrome.exe），值是 `auto-<线路 id>`。
    同一个程序有多条档案时，靠后的覆盖前面的 —— 和界面上的顺序一致，
    用户新加的那条就是他想用的那条。
    """
    reg = Registry().load()
    store = profiles.ProfileStore().load()
    groups: dict[str, list] = {}
    for line in reg.lines:
        nodes, _ = reg.load_nodes(line)
        if nodes:
            groups[line.id] = nodes
    if not groups:
        raise TunError("还没有任何线路/节点，先添加代理并更新一次订阅")

    rules: dict[str, str] = {}
    skipped: list[str] = []
    for prof in store.profiles:
        if not prof.line_id:
            continue                       # 绑「直连」的档案不参与
        if prof.line_id not in groups:
            skipped.append(f"{prof.name}（线路 {prof.line_id} 没有节点）")
            continue
        exe = Path(prof.exe).name
        if not exe:
            continue
        rules[exe] = f"auto-{prof.line_id}"
    if skipped:
        # 不抛错：少一个程序走代理，好过整个 TUN 起不来
        pass
    return groups, rules


def build() -> dict[str, Any]:
    reg = Registry().load()
    groups, rules = collect()
    return tunconf.build(groups, rules, health_url=_health(reg))


def _health(reg: Registry) -> str:
    return reg.defaults.get("health_url") or settings.DEFAULT_HEALTH_URL


# --------------------------------------------------------------------------- 起停
def start() -> str:
    reg = Registry().load()
    binary = reg.binary("singbox")
    if binary is None:
        raise TunError("找不到 sing-box，TUN 模式需要它")

    cfg = build()
    path = config_path()
    tunconf_write(cfg, path)
    ok, out = coreproc.validate(path, binary, "singbox")
    if not ok:
        raise TunError(f"TUN 配置没通过校验：{out}")

    st = elevate.status()
    prev = _read_state().get("task") or {}
    sig = {"bin": str(binary), "cfg": str(path)}
    need_install = (not st["installed"]) or prev.get("bin") != sig["bin"] \
        or prev.get("cfg") != sig["cfg"]
    if need_install:
        # 只有这一步需要管理员：第一次装，或内核/配置路径变了才重装。
        # install_with_uac 会自己弹一次 UAC —— 界面是 Chrome --app 窗口，
        # 用户没地方「右键以管理员身份运行」，不能让他去找。
        elevate.install_with_uac(binary, path)

    # 系统代理必须让路：两者同时开会绕圈，本地页面也容易被送进代理
    saved = sysproxy.read()
    _write_state({"on": False, "sysproxy": saved if saved.get("enable") else {},
                  "since": int(time.time()), "task": sig})
    if saved.get("enable"):
        sysproxy.disable()

    msg = elevate.start()
    _write_state({"on": True, "sysproxy": saved if saved.get("enable") else {},
                  "since": int(time.time()), "task": sig,
                  "detail": tunconf.describe(cfg)})
    return f"{msg}；{tunconf.describe(cfg)}"


def stop(restore_sysproxy: bool = True) -> str:
    st = _read_state()
    msg = elevate.end()
    if restore_sysproxy and st.get("sysproxy"):
        # 还原接管前的系统代理（用户可能本来就开着别的代理）
        try:
            port = int(sysproxy.parse_server(st["sysproxy"].get("server", ""))[0]
                       .rsplit(":", 1)[-1])
            sysproxy.enable(port)
            msg += "；系统代理已还原"
        except (ValueError, IndexError, OSError):
            pass
    _write_state({"on": False, "sysproxy": {}, "since": int(time.time())})
    return msg


def status() -> dict[str, Any]:
    st = _read_state()
    info = elevate.status()
    try:
        _, rules = collect()
    except TunError:
        rules = {}
    return {
        "on": bool(info["running"]),
        "installed": info["installed"],
        "claimed_on": bool(st.get("on")),
        "procs": sorted(rules),
        "count": len(rules),
        "config": str(config_path()),
    }


def tunconf_write(cfg: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path


def rebuild_if_on() -> str:
    """档案改了（加软件/换线路）时，TUN 正在跑就重启一次让它生效。"""
    st = status()
    if not st["on"]:
        return "TUN 没在跑，跳过重建"
    stop(restore_sysproxy=False)
    return start()

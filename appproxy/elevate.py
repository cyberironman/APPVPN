# -*- coding: utf-8 -*-
"""提权通道：让 TUN 内核以管理员身份跑，而**只弹一次** UAC。

为什么不是「每次点都 UAC」
    TUN 要建虚拟网卡，必须管理员令牌。常见做法是每次启动弹 UAC（用户账户控制），
    点一次还容易点错；或者把整个托盘提权（等于全程高权限，风险更大）。
    这里走第三条路：装一个**没有触发器**的计划任务，用最高权限运行 sing-box。
        安装任务 → 需要一次管理员（UAC 弹一次，只在第一次）
        以后启动 → `schtasks /Run` 普通权限即可，任务自己带高权限令牌

任务名固定，方便用户自己去「任务计划程序」里删掉；也方便我们查状态。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

from . import selfrun, settings

TASK = "AppProxyTun"

_status_cache: tuple[float, dict] | None = None

CREATE_HINT = ("需要一次管理员授权才能装提权任务：右键托盘图标 →「以管理员身份运行」，"
               "或者从「开始菜单」搜到本程序右键选管理员。装好之后就不用再点了。")

# ShellExecuteW 的返回码：<= 32 都是失败，5 最常见 —— 用户在 UAC 弹窗里点了「否」
_SHELL_OK = 32
_SHELL_CANCEL = 5
UAC_TIMEOUT = 120.0


class ElevateError(RuntimeError):
    pass


def _schtasks(*args: str, timeout: float = 20.0) -> tuple[int, str]:
    """调 schtasks，永远不弹窗（CREATE_NO_WINDOW），返回 (返回码, 输出)。"""
    try:
        r = subprocess.run(["schtasks", *args], capture_output=True, text=True,
                           timeout=timeout, creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"{type(e).__name__}: {e}"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _current_user() -> str:
    dom = os.environ.get("USERDOMAIN") or ""
    name = os.environ.get("USERNAME") or ""
    return f"{dom}\\{name}" if dom and name else (name or ".")


def task_xml(singbox: Path, cfg: Path) -> str:
    """没有触发器的任务：只能手动 / 由本程序点起来。"""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>分应用代理管家 · TUN 内核（虚拟网卡接管）。删掉本任务即取消提权通道。</Description>
  </RegistrationInfo>
  <Triggers />
  <Principals>
    <Principal id="Author">
      <UserId>{escape(_current_user())}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(singbox))}</Command>
      <Arguments>run -c "{escape(str(cfg))}"</Arguments>
      <WorkingDirectory>{escape(str(Path(singbox).parent))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def write_xml(singbox: Path, cfg: Path, path: Path | None = None) -> Path:
    """schtasks /XML 只吃 UTF-16，写错编码会报「无法解析 XML」。"""
    path = path or (Path(tempfile.gettempdir()) / f"{TASK}.xml")
    path.write_text(task_xml(singbox, cfg), encoding="utf-16")
    return path


def installed() -> bool:
    rc, _ = _schtasks("/Query", "/TN", TASK)
    return rc == 0


def install(singbox: Path, cfg: Path) -> str:
    """装（或重装）提权任务。cfg 换路径时也要重装一次。"""
    xml = write_xml(singbox, cfg)
    rc, out = _schtasks("/Create", "/TN", TASK, "/XML", str(xml), "/F")
    if rc != 0:
        low = out.lower()
        if "拒绝访问" in out or "access is denied" in low or rc == 1:
            raise ElevateError(f"装提权任务失败（{out.strip()[:200]}）。{CREATE_HINT}")
        raise ElevateError(f"装提权任务失败：{out.strip()[:300]}")
    cache_clear()
    return "提权任务已装好（以后启动不再弹 UAC）"


def _log(msg: str) -> None:
    """提权子进程没有标准输出（走 pythonw），只能写文件。"""
    try:
        d = settings.logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "elevate.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _query_installed() -> bool:
    """绕过 3 秒缓存去问一次 —— 提权子进程装完要立刻看到。"""
    rc, _ = _schtasks("/Query", "/TN", TASK)
    return rc == 0


def _shell_runas(exe: Path, args: str, cwd: Path) -> int:
    """用 runas 拉一个提权子进程，返回 ShellExecuteW 的返回码（5 = 用户点了「否」）。"""
    import ctypes

    return int(ctypes.windll.shell32.ShellExecuteW(
        None, "runas", str(exe), args, str(cwd), 1))   # 1 = SW_SHOWNORMAL


def _launch_elevated_install(binary: Path, cfg: Path) -> int:
    """把自己以 `elevate install` 子命令再拉起一次（打包后是 AppProxy.exe）。"""
    exe = selfrun.self_exe()
    args = selfrun.self_args(["elevate", "install",
                              "--binary", str(binary), "--config", str(cfg)])
    return _shell_runas(exe, args, selfrun.self_cwd())


def install_with_uac(binary: Path, cfg: Path, timeout: float = UAC_TIMEOUT) -> str:
    """装提权任务。已经是管理员就一次成；否则**自己弹一次 UAC**。

    为什么不用「提示用户右键以管理员身份运行」
        本产品的图形界面是一个 Chrome --app 窗口，用户根本没有地方右键提权。
        让程序自己 ShellExecuteW("runas") 拉起提权子进程，用户只需要在 UAC
        弹窗上点一次「是」—— 这也是他唯一需要点的地方。
    """
    try:
        return install(binary, cfg)          # 已经是管理员：静默装完，不弹窗
    except ElevateError as first:
        _log(f"直接装失败，改走 UAC：{first}")

    # 路径里带空格（例如 D:\My Projects\appproxy）→ 参数里必须自己加引号，
    # 因为 ShellExecuteW 的 lpParameters 是**一整条命令行**，不做引号补全。
    rc = _launch_elevated_install(binary, cfg)
    if rc == _SHELL_CANCEL:
        raise ElevateError(
            "你取消了管理员授权，TUN 没有开启。\n"
            "想用的话再点一次「开启 TUN」，在弹窗里选「是」即可（只需这一次）。")
    if rc <= _SHELL_OK:
        raise ElevateError(
            f"没能弹出管理员授权窗口（错误码 {rc}）。"
            "如果系统提示已经被别处拦下，试着从开始菜单搜索本程序 → 右键 → 以管理员身份运行。")

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _query_installed():
            cache_clear()
            return "提权任务已装好（以后启动不再弹 UAC）"
        time.sleep(0.4)
    raise ElevateError(
        f"等了 {timeout:.0f} 秒还没装好提权任务。"
        "请去「任务计划程序」里看有没有 AppProxyTun，或看日志 logs\\elevate.log。")


def start() -> str:
    rc, out = _schtasks("/Run", "/TN", TASK)
    if rc != 0:
        raise ElevateError(f"启动 TUN 内核失败：{out.strip()[:300]}")
    return "TUN 内核已启动"


def end() -> str:
    rc, out = _schtasks("/End", "/TN", TASK)
    if rc != 0:
        # 已经在跑之外的情况：任务不存在 / 已经停了，都不算错误
        return "TUN 内核已停止" if installed() else "没有在运行的 TUN 任务"
    return "TUN 内核已停止"


def delete() -> str:
    rc, out = _schtasks("/Delete", "/TN", TASK, "/F")
    cache_clear()
    return "提权任务已删除" if rc == 0 else f"删除失败：{out.strip()[:200]}"


def status() -> dict:
    """{'installed': bool, 'running': bool, 'raw': '...'}

    界面每 6 秒会问一次，而 schtasks 启动一次要几十毫秒 —— 缓存 3 秒，
    免得把轮询变成小卡顿。装/删任务后调用 `status.cache_clear()` 即可。
    """
    global _status_cache
    now = time.time()
    if _status_cache and now - _status_cache[0] < 3.0:
        return _status_cache[1]
    res = _status_now()
    _status_cache = (now, res)
    return res


def _status_now() -> dict:
    if not installed():
        return {"installed": False, "running": False, "raw": ""}
    rc, out = _schtasks("/Query", "/TN", TASK, "/FO", "LIST", "/V")
    running = rc == 0 and ("Running" in out or "正在运行" in out)
    return {"installed": True, "running": running, "raw": out}


def cache_clear() -> None:
    global _status_cache
    _status_cache = None


# --------------------------------------------------------------------------- 提权子进程入口
def main(argv: list[str] | None = None) -> int:
    """`python -m appproxy.elevate install --binary X --config Y`

    这个入口**只在提权后运行**（由 install_with_uac 用 runas 拉起），
    而且必须无窗口（pythonw），因为没有用户在看它的输出。
    """
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "install":
        _log(f"未知指令：{argv}")
        return 2
    try:
        binary = Path(argv[argv.index("--binary") + 1])
        cfg = Path(argv[argv.index("--config") + 1])
    except (ValueError, IndexError):
        _log(f"参数不全：{argv}")
        return 2
    try:
        msg = install(binary, cfg)
    except ElevateError as e:
        _log(f"提权装任务失败：{e}")
        return 1
    _log(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

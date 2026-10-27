# -*- coding: utf-8 -*-
"""运行环境体检 —— 把「装不上 / 跑不起来」翻译成人话，并给一键修复。

为什么需要它
    软件打包成 exe 以后用户机器上没有 Python，也不会有人去读日志。缺一个
    系统组件，用户看到的就是「双击没反应」或者一句「找不到 xxx.dll」。
    与其让人猜，不如启动时自己查一遍，缺什么直接说，并且给一个按钮点到
    官方安装包 —— 用户只需要点两下，不需要知道什么叫运行库。

两类问题
    block  真的跑不起来，必须修（例如系统太老、内核文件丢了、数据目录写不进去）
    warn   能跑，但某个功能会用不了（例如缺 wintun 驱动，TUN 模式起不来）

修复动作都是「能自动就自动」，不能自动的至少把用户送到正确的页面。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import settings

# 微软官方的 VC++ 2015-2022 运行库（x64），点开就是一个安装程序
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
# Wintun 官方下载页（TUN 虚拟网卡在 Windows 上靠它）
WINTUN_PAGE = "https://www.wintun.net/"
WINTUN_ZIP = "https://www.wintun.net/builds/wintun-0.14.1.zip"
WINTUN_DLL = "wintun.dll"

# UCRT（通用 C 运行库）里最基础的那个，Win10 自带，Win7/8 需要打补丁
UCRT_DLL = "api-ms-win-crt-runtime-l1-1-0.dll"


@dataclass
class Issue:
    key: str            # 稳定标识，前端按钮认它
    level: str          # block / warn
    title: str
    detail: str
    fix: str = ""       # 修复动作：vc_runtime / wintun / engine / data / os / ""
    fix_label: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- 单项探测
def os_release() -> tuple[int, int, int]:
    """(major, minor, build)。非 Windows 返回 (0, 0, 0)。"""
    if os.name != "nt":
        return (0, 0, 0)
    try:
        ver = sys.getwindowsversion()      # type: ignore[attr-defined]
        return (ver.major, ver.minor, ver.build)
    except Exception:                      # noqa: BLE001
        return (0, 0, 0)


def engine_dir() -> Path | None:
    """内核所在目录（sing-box.exe 的父目录）—— wintun.dll 要放在这儿。"""
    exe = settings.find_singbox()
    return exe.parent if exe else None


def has_wintun() -> bool:
    d = engine_dir()
    if d is None:
        return False
    if (d / WINTUN_DLL).is_file():
        return True
    # 也可能被放到系统目录里（旧版手动装的）
    sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return (sys32 / WINTUN_DLL).is_file()


def _can_load(dll: str) -> bool:
    """能不能真的加载这个 DLL。比查文件是否存在准 —— 位数不对、依赖缺失都会失败。"""
    if os.name != "nt":
        return True
    try:
        ctypes = __import__("ctypes")
        ctypes.WinDLL(dll)
        return True
    except OSError:
        return False
    except Exception:                      # noqa: BLE001
        return False


def ucrt_available() -> bool:
    """UCRT 在不在 —— Win10/11 一定在；Win7/8 不打补丁就缺。"""
    return _can_load(UCRT_DLL)


def vc_runtime_installed() -> bool:
    """系统级 VC++ 2015-2022 运行库装没装。

    注意：这不影响我们自己的启动（PyInstaller 把 vcruntime140.dll 一起打进
    包里了），但很多别的软件要它，装一次省掉后面一堆「缺 dll」的排查。
    所以这一项是 warn 不是 block。
    """
    if os.name != "nt":
        return True
    try:
        import winreg
    except ImportError:
        return True
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in (r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
                    r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"):
            try:
                with winreg.OpenKey(root, sub) as k:
                    val, _ = winreg.QueryValueEx(k, "Installed")
                    if int(val) == 1:
                        return True
            except (OSError, ValueError):
                continue
    return False


def data_writable() -> bool:
    """数据目录能不能写。

    用「建目录 + os.access」判断，**不写探针文件再删** —— 删动作会在
    带 safe-delete 拦截的环境里直接中断进程（踩过）。
    """
    try:
        d = settings.data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return os.access(str(d), os.W_OK)
    except OSError:
        return False


def free_space_mb(path: Path | None = None) -> int:
    try:
        target = path or settings.home()
        target.mkdir(parents=True, exist_ok=True)
        return int(shutil.disk_usage(str(target)).free / 1048576)
    except OSError:
        return -1


# --------------------------------------------------------------------------- 汇总
def report() -> list[dict]:
    """体检报告。给界面和 doctor 命令共用，顺序按严重程度。"""
    out: list[Issue] = []

    major, minor, build = os_release()
    if major and major < 10:
        out.append(Issue(
            "os", "block", "系统版本太旧",
            "本软件需要 Windows 10 或 11（当前 %d.%d，内部版本 %d）。"
            "旧系统缺少必要的网络与安全接口，装上也跑不稳。" % (major, minor, build),
            fix="os", fix_label="看系统要求"))

    if not ucrt_available():
        out.append(Issue(
            "ucrt", "block", "缺少系统运行库（UCRT）",
            "当前系统缺少「通用 C 运行库」，程序无法启动。"
            "Win7 / Win8 用户装一次微软的运行库即可，装完重新打开软件。",
            fix="vc_runtime", fix_label="下载并安装运行库"))

    sb = settings.find_singbox()
    if sb is None:
        out.append(Issue(
            "engine", "block", "找不到内核",
            "代理内核 sing-box.exe 不在程序目录里。多半是解压不完整，"
            "或者杀毒软件把 third_party 文件夹删掉了。重新解压一次安装包即可。",
            fix="engine", fix_label="查看解决办法"))
    elif not has_wintun():
        out.append(Issue(
            "wintun", "warn", "TUN 驱动缺失（不影响普通用法）",
            "虚拟网卡模式需要 wintun.dll。现在没有它，TUN 打开会失败；"
            "按程序分流、系统代理这些功能不受影响。",
            fix="wintun", fix_label="自动下载补上"))

    if not data_writable():
        out.append(Issue(
            "data", "block", "数据目录写不进去",
            "程序要往 %s 写配置，但当前账户没有权限。"
            "把软件换个位置（比如放到「文档」下），或者用安装版装到用户目录。"
            % settings.home(),
            fix="data", fix_label="怎么处理"))

    free = free_space_mb()
    if 0 <= free < 300:
        out.append(Issue(
            "disk", "warn", "磁盘空间不足",
            "数据所在磁盘只剩 %d MB。空间不足时内核和日志会写入失败。" % free,
            fix="", fix_label=""))

    if not vc_runtime_installed():
        out.append(Issue(
            "vc", "warn", "建议装一次 VC++ 运行库",
            "本软件自带所需库、自己不受影响，但系统里没有 VC++ 2015-2022 运行库，"
            "别的软件可能会报「找不到 dll」。装一次一劳永逸（约 25 MB）。",
            fix="vc_runtime", fix_label="下载并安装"))

    return [i.as_dict() for i in out]


def blocking() -> list[dict]:
    return [i for i in report() if i["level"] == "block"]


def is_healthy() -> bool:
    return not blocking()


# --------------------------------------------------------------------------- 修复
def _download(url: str, dest: Path, timeout: int = 180) -> Path:
    """裸下载（显式绕开系统代理 —— 用户可能正开着代理，而代理本身是坏的）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with opener.open(url, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def install_vc_runtime() -> dict:
    """下载微软官方运行库安装包并启动它（安装程序自己会弹 UAC）。"""
    try:
        tmp = Path(tempfile.gettempdir()) / "vc_redist.x64.exe"
        _download(VC_REDIST_URL, tmp)
        os.startfile(str(tmp))            # noqa: S606  官方签名安装包
        return {"ok": True,
                "message": "已下载运行库安装包，正在打开安装向导。装完点「重新体检」。"}
    except Exception as e:                 # noqa: BLE001
        return {"ok": False,
                "message": "自动下载失败（%s）。请手动打开 %s 下载安装。"
                           % (e, VC_REDIST_URL),
                "url": VC_REDIST_URL}


def install_wintun() -> dict:
    """把 wintun.dll 补进内核目录。"""
    d = engine_dir()
    if d is None:
        return {"ok": False, "message": "找不到内核目录，先修好「找不到内核」这一项。"}
    try:
        tmp = Path(tempfile.gettempdir()) / "wintun-dl.zip"
        _download(WINTUN_ZIP, tmp)
        with zipfile.ZipFile(tmp) as zf:
            for name in zf.namelist():
                if name.lower().endswith("amd64/wintun.dll"):
                    (d / WINTUN_DLL).write_bytes(zf.read(name))
                    return {"ok": True,
                            "message": "已补上 wintun.dll（%s），TUN 现在可以用了。" % d}
        return {"ok": False, "message": "下载包里没找到 wintun.dll，请手动到 %s 下载。" % WINTUN_PAGE}
    except Exception as e:                 # noqa: BLE001
        return {"ok": False,
                "message": "下载失败（%s）。可以手动从 %s 下载 wintun.dll 放进 %s。"
                           % (e, WINTUN_PAGE, d),
                "url": WINTUN_PAGE}


def fix(key: str) -> dict:
    """执行一项修复。前端按钮和 doctor 命令都走这里。"""
    if key == "vc_runtime":
        return install_vc_runtime()
    if key == "wintun":
        return install_wintun()
    if key == "engine":
        return {"ok": False,
                "message": "请重新解压安装包覆盖到当前目录（数据在 %s，不会被覆盖）。"
                           "如果用安装版，重新运行 setup.exe 选「修复」即可。"
                           % settings.home()}
    if key == "data":
        return {"ok": False,
                "message": "把整个软件文件夹移到有写权限的位置（比如 D:\\AppProxy 或「文档」下），"
                           "绿色版的数据会跟着文件夹走；安装版请在安装时选用户目录。"}
    if key == "os":
        return {"ok": False,
                "message": "请在 Windows 10 / 11 上使用（设置 → 系统 → 关于 可以看版本）。"}
    if key == "disk":
        return {"ok": False, "message": "清理一下磁盘，或者把数据目录换到空间更大的盘。"}
    return {"ok": False, "message": "不认识的处理项：%s" % key}


# --------------------------------------------------------------------------- 启动拦截
def show_blocking_dialog(issues: list[dict]) -> None:
    """打包后双击启动、发现跑不起来时弹的原生提示框。

    为什么用 MessageBox 而不是界面：界面是靠 Python 服务起的，环境都坏了
    的时候它自己也起不来，这时候只有系统对话框靠得住。
    """
    if os.name != "nt":
        for i in issues:
            print("[!] %s：%s" % (i["title"], i["detail"]))
        return
    try:
        import ctypes
        text = "\n\n".join("%s\n%s" % (i["title"], i["detail"]) for i in issues)
        text += "\n\n点「确定」打开日志目录，方便进一步排查。"
        ctypes.windll.user32.MessageBoxW(       # type: ignore[attr-defined]
            None, text, "分应用代理管家 · 启动检查", 0x00000040 | 0x00000000)
    except Exception:                           # noqa: BLE001
        pass

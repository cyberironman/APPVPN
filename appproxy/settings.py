# -*- coding: utf-8 -*-
"""路径与默认值解析。

设计约束（踩过的坑）：
    * 全部函数式解析，不做模块级常量缓存 —— 便于测试用 monkeypatch 换根目录；
    * 只用标准库，不依赖第三方；
    * 数据目录跟着「安装形态」走，不写死任何一个盘符。

两种发行形态的数据落点
    * **绿色版**：程序目录里带 `portable.txt`，数据写 `程序目录\\AppProxyData\\`。
      整个文件夹拷到 U 盘、换盘符、换机器，数据跟着走，删文件夹即卸载。
    * **安装版**：安装程序把安装目录和数据目录写进注册表
      `HKCU\\Software\\AppProxy`，程序照着读。装在 Program Files 也不会因为
      没有写权限而失败。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_HOME = "APPPROXY_HOME"
# 没有便携标记、也没有安装信息时（比如直接跑源码）的默认根目录。
# 本机一直用这个位置，保留它是为了升级不丢已有订阅。
DEFAULT_HOME = Path(r"D:\AppProxy")

# 便携模式的标记文件名（放在 exe 同目录）
PORTABLE_FLAG = "portable.txt"
# 便携模式下的数据文件夹名（放在 exe 同目录）
PORTABLE_DIR = "AppProxyData"

# 安装程序写、程序读的注册表位置
REG_SUBKEY = r"Software\AppProxy"

# xray 常见安装位置（按优先级）。装了别处的用 APPPROXY_XRAY 环境变量覆盖，
# 或者把路径写进注册表的 defaults.xray。
XRAY_CANDIDATES = (
    r"C:\Program Files\v2rayN\bin\xray\xray.exe",
    r"C:\Program Files\v2rayN\bin\Xray\xray.exe",
    r"D:\v2rayN\bin\xray\xray.exe",
    r"C:\Program Files\v2rayN\bin\xray.exe",
)

DEFAULT_HEALTH_URL = "https://www.gstatic.com/generate_204"


def exe_dir() -> Path:
    """程序自己所在的目录 —— 「放哪都能用」的判断基准。

    打包后 = AppProxy.exe 所在目录（用户解压/安装到哪就是哪）；
    源码运行 = 仓库根目录。

    注意不要用 code_root()：打包后那是 `_internal\\`，是程序的**内容**目录，
    不是用户放软件的地方。便携标记和数据目录都在 exe 旁边才符合直觉。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return code_root()


def portable_flag() -> Path:
    return exe_dir() / PORTABLE_FLAG


def is_portable() -> bool:
    """是不是绿色版 —— **只看 exe 旁边的 portable.txt**。

    不把「AppProxyData 目录存在」也算进来：安装版第一次运行也会建这个目录，
    那样安装版会被误判成绿色版；更糟的是用户把数据改到别的盘之后，
    老目录还在，改过的设置就被无视了。
    """
    return portable_flag().is_file()


def _reg_value(name: str) -> str:
    """读 HKCU\\Software\\AppProxy 下的一个字符串值，读不到返回空串。"""
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_SUBKEY) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return str(val) if val else ""
    except OSError:
        return ""


def _reg_set(name: str, value: str) -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_SUBKEY) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        return True
    except OSError:
        return False


def data_dir_pref() -> Path | None:
    """用户在软件里（或在安装程序里）指定过的数据目录。

    存在 HKCU 而不是存进数据目录本身 —— 否则「数据在哪」这件事就没地方记了。
    """
    val = _reg_value("DataDir")
    return Path(val) if val else None


def set_data_dir(path: str | Path) -> bool:
    """记住用户选的数据目录。**不搬数据**，只记位置，下次启动生效。"""
    return _reg_set("DataDir", str(path))


def clear_data_dir() -> bool:
    """忘掉用户设的位置，回到默认（程序旁边的 AppProxyData）。"""
    return _reg_set("DataDir", "")


def local_app_data_home() -> Path:
    """兜底：当前用户的应用数据目录，任何 Windows 账户都可写。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "AppProxy"
    return Path.home() / ".appproxy"


def dir_writable(path: Path) -> bool:
    """目录能不能写。

    用 CreateFileW 直接以「写」方式打开目录，不建任何探针文件 ——
    在带 safe-delete 拦截的环境里，创建再删除文件会直接中断进程（踩过）。
    非 Windows 退回 os.access。
    """
    if os.name != "nt":
        return os.access(str(path), os.W_OK)
    try:
        import ctypes
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_ALL = 0x00000007
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000      # 允许打开目录
        k32 = ctypes.windll.kernel32                  # type: ignore[attr-defined]
        k32.CreateFileW.restype = ctypes.c_void_p
        h = k32.CreateFileW(str(path), GENERIC_WRITE, FILE_SHARE_ALL, None,
                            OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if h in (None, 0xFFFFFFFF, -1):
            return False
        k32.CloseHandle(ctypes.c_void_p(h))
        return True
    except Exception:                                 # noqa: BLE001
        return False


def home() -> Path:
    """运行数据根目录。从「用户明说」到「绝对能写」，五档。

    1. `APPPROXY_HOME` 环境变量 —— 测试/排障显式指定，最高优先。
    2. **绿色版**（exe 旁边有 `portable.txt`）—— 一律用旁边的 `AppProxyData\\`，
       不看注册表。绿色版的定义就是「不往系统里写东西」，数据跟着文件夹走；
       想换位置就把整个文件夹挪过去。
    3. 注册表里记的用户选择 —— 安装版默认就是 `{安装目录}\\AppProxyData`，
       用户也可以在「设置 → 数据目录」里改成别的盘。
    4. 程序旁边的 `AppProxyData\\` —— 安装版在没注册表信息时的默认值。
       装在哪个目录，数据就在哪个目录，整个文件夹搬走数据跟着走。
    5. 程序目录不可写（比如装在 Program Files 又没用管理员授权过）时，
       退到 `%LOCALAPPDATA%\\AppProxy`，保证一定能跑起来。
    6. 源码运行（没有打包）没有「程序旁边的数据目录」这个概念，
       沿用 `D:\\AppProxy`（有 D 盘）/ `%LOCALAPPDATA%\\AppProxy`。

    这里只做判断、不建目录（ensure_dirs 负责建），所以可以随便调。
    也不做模块级缓存 —— 测试要用 monkeypatch 换根目录。
    """
    env = os.environ.get(ENV_HOME)
    if env:
        return Path(env)

    if is_portable():
        return exe_dir() / PORTABLE_DIR

    if not getattr(sys, "frozen", False):
        # 源码模式：开发机的老位置优先，行为不变。
        # 也不读注册表 —— 那是「装过的软件」才有的东西，跑源码不该被它带偏。
        if Path(DEFAULT_HOME.anchor).is_dir():
            return DEFAULT_HOME
        return local_app_data_home()

    pinned = data_dir_pref()
    if pinned is not None:
        return pinned

    beside = exe_dir() / PORTABLE_DIR
    if beside.is_dir() or dir_writable(exe_dir()):
        return beside
    return local_app_data_home()


def code_root() -> Path:
    """程序内容根目录（放 appproxy 包、third_party 内核的那一层）。

    打包后是 `_internal\\`，源码模式是仓库根。找内核、找界面文件用它，
    找「用户放软件的地方」用 exe_dir()。
    """
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

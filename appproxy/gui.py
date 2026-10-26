# -*- coding: utf-8 -*-
"""主界面：本地 HTTP 服务 + Chromium --app 窗口。

三条边界
    1. 关窗口 ≠ 关代理。关掉界面只停本地 HTTP 服务，内核和已经开着的软件照常。
       内核该不该退场，由「还有没有软件在用」决定（pool.gc）。
    2. 界面不是常驻必需品。托盘图标才是常驻入口，界面是可随时开关的控制台。
    3. 托盘和界面是两个进程，只能靠 HTTP 对话：
       托盘点「设置…」→ 找已经在跑的界面 → 发一条「把视图切到设置」，
       顺手把那个窗口提到最前；找不到窗口才新开一个。

端口约定
    优先固定 1443（好记、托盘不用猜），被别的程序占了就退到随机端口，
    实际端口写进 `gui.port` 文件 —— 托盘读这个文件定位界面。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from appproxy import pool, settings  # noqa: E402

TITLE = "分应用代理管家"
GC_INTERVAL = 5.0           # 秒；回收检查频率
STARTUP_TIMEOUT = 12.0      # 等本地服务起来的最大秒数
GUI_LOCK_PORT = 10897       # 单实例锁（和托盘的 10896 分开）
GUI_PORT = 1443             # 界面服务首选端口（固定，方便托盘定位）
APP_TAG = "appproxy"

# 窗口外壳：优先用 Chromium 系的 --app 模式开独立窗口。
# 理由：这台机器上 WebView2 环境创建失败（BrowserProcessId 为 None），
# pywebview 会静默退成一片白；而 Chrome/Edge 的 --app 窗口没有地址栏、没有标签页，
# 外观就是独立桌面应用，还能强制 --no-proxy-server 保证本地页面一定打得开。
BROWSER_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def find_browser() -> Path | None:
    for c in BROWSER_CANDIDATES:
        p = Path(c)
        if p.is_file():
            return p
    return None


def log(msg: str) -> None:
    try:
        settings.logs_dir().mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(settings.logs_dir() / "gui.log", "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")
    except OSError:
        pass
    print(msg)


def _acquire_lock():
    """占住固定端口当单实例锁。占用失败说明已经开着一个窗口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", GUI_LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def _shout(msg: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, TITLE, 0x40)
    except Exception:  # noqa: BLE001
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_open(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- 进程间对话
def port_file() -> Path:
    """界面把「我实际用哪个端口」写在这里，供托盘/命令行定位。"""
    return settings.home() / "gui.port"


def _get(port: int, path: str, timeout: float = 0.6) -> dict:
    """直连本机，刻意不用 urllib —— 它默认读系统代理，
    用户正开着代理时访问 127.0.0.1 会被送去代理，直接 502。"""
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        res = conn.getresponse()
        return json.loads(res.read().decode("utf-8", "replace") or "{}")
    finally:
        conn.close()


def _post(port: int, path: str, payload: dict, timeout: float = 0.8) -> dict:
    import http.client

    body = json.dumps(payload).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("POST", path, body=body,
                     headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        return json.loads(res.read().decode("utf-8", "replace") or "{}")
    finally:
        conn.close()


def _is_ours(port: int) -> bool:
    try:
        return _get(port, "/api/ping").get("app") == APP_TAG
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def running_port() -> int | None:
    """已经开着的界面在哪个端口？没有就返回 None。

    先信 port 文件（可能是随机端口），再探固定端口兜底。
    """
    ports: list[int] = []
    try:
        f = port_file()
        if f.is_file():
            ports.append(int(f.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        pass
    if GUI_PORT not in ports:
        ports.append(GUI_PORT)
    for p in ports:
        if _port_open(p, timeout=0.2) and _is_ours(p):
            return p
    return None


def _find_window(title: str = TITLE) -> int:
    """按标题找我们的界面窗口，找不到返回 0。

    --app 窗口的标题就是页面 <title>，正好是我们自己起的名字。
    """
    if os.name != "nt":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            text = buf.value
            if text == title or text.startswith(title):
                found.append(int(hwnd))
            return True

        user32.EnumWindows(cb, 0)
        return found[0] if found else 0
    except Exception:  # noqa: BLE001
        return 0


def flash_window(title: str = TITLE, times: int = 5) -> bool:
    """让界面窗口在任务栏闪几下（不抢焦点，也不会被系统拦）。

    为什么需要它：Windows 不许后台进程随便把窗口提到最前，
    AttachThreadInput 那招也不是每次都能成。提不上来时至少闪一下，
    否则用户点了托盘的「设置」会觉得程序没反应。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = _find_window(title)
        if not hwnd:
            return False

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]

        FLASHW_ALL = 0x00000003          # 标题栏 + 任务栏按钮一起闪
        FLASHW_TIMERNOFG = 0x0000000C    # 一直闪到窗口回到前台
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd,
                          FLASHW_ALL | FLASHW_TIMERNOFG, times, 0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        # 注意：FlashWindowEx 的返回值是「调用前窗口是否已处于活动状态」，
        # 不是成功/失败 —— 拿它判断成败会误报，这里只要句柄在、调用没抛异常就算做了。
        return True
    except Exception:  # noqa: BLE001
        return False


def focus_window(title: str = TITLE) -> bool:
    """把已经开着的界面窗口提到最前（含最小化还原）。

    跨进程抢前台会被系统拦，所以走 AttachThreadInput 把自己挂到
    当前前台线程上再 SetForegroundWindow —— 这是 Windows 上最稳的老办法。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = _find_window(title)
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_me = kernel32.GetCurrentThreadId()
        attached = bool(tid_fg) and tid_fg != tid_me
        if attached:
            user32.AttachThreadInput(tid_fg, tid_me, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(tid_fg, tid_me, False)
        return True
    except Exception:  # noqa: BLE001  提不到前台也不算错，下面还有新开窗口兜底
        return False


VIEW_HASH = {"profile": "", "settings": "settings", "apps": "apps"}


def _view_url(port: int, view: str) -> str:
    frag = VIEW_HASH.get(view, "")
    return f"http://127.0.0.1:{port}/" + (f"#{frag}" if frag else "")


def _spawn_gui(view: str = "") -> bool:
    """另起一个界面进程（服务 + 窗口都在它里面）。"""
    import subprocess

    script = settings.code_root() / "appproxy" / "gui.py"
    if not script.is_file():
        log(f"[!!] 找不到界面程序 {script}")
        return False
    args = [sys.executable, str(script)]
    if view:
        args += ["--view", view]
    try:
        subprocess.Popen(args, cwd=str(settings.code_root()),
                         creationflags=0x00000008)      # DETACHED_PROCESS
    except OSError as e:
        log(f"[!!] 起界面进程失败：{e}")
        return False
    return True


def open_view(view: str = "profile") -> str:
    """统一入口：托盘「打开主界面」和「设置…」都走这里。

    有窗口 → 让它切视图、提到前台，并**在任务栏闪几下**；
             提到前台常被系统拦，闪一下用户才知道点的是哪个窗口。
    没窗口 → 直接开到指定视图。
    返回一句人话，方便调用方决定要不要提示。
    """
    settings.ensure_dirs()
    port = running_port()
    if port:
        try:
            _post(port, "/api/view", {"view": view})
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        # 不管提没提上来，都闪 —— 这是「点了有反应」最便宜的保证。
        flashed = flash_window()
        if focus_window():
            return "已切到已打开的窗口（任务栏闪烁提示）" if flashed else "已切到已打开的窗口"
        # 窗口提不上来（被别的虚拟桌面/显示器挡住等）→ 宁可多开一个，
        # 也不能让用户点了没反应。
        if launch_window(_view_url(port, view)):
            return "已新开一个窗口"
        return "已在原窗口切到该页（任务栏闪烁提示）" if flashed else "已请求切换页面"
    ok = _spawn_gui("" if view == "profile" else view)
    return "正在打开界面" if ok else "打开界面失败"


class ServerBox:
    """把 uvicorn 的 server 句柄带出来，好在窗口关闭时让它退出。"""

    def __init__(self) -> None:
        self.server = None
        self.port = 0


def serve_http(port: int, box: ServerBox) -> None:
    import uvicorn

    from appproxy.webui.api import app

    cfg = uvicorn.Config(app, host="127.0.0.1", port=port,
                         log_level="warning", access_log=False)
    server = uvicorn.Server(cfg)
    box.server = server
    server.run()


def gc_loop(stop: threading.Event) -> None:
    """后台回收：没有任何软件在用的内核自动退场。"""
    while not stop.is_set():
        try:
            done = pool.gc()
            for item in done:
                log(f"自动回收内核 {item['line']}（端口 {item['port']} 已释放）")
        except Exception as e:  # noqa: BLE001  回收失败绝不能让界面挂掉
            log(f"[!!] 回收出错：{type(e).__name__}: {e}")
        stop.wait(GC_INTERVAL)


def _window_args(url: str) -> list[str]:
    exe = find_browser()
    if exe is None:
        return []
    profile = settings.home() / "gui-profile"
    profile.mkdir(parents=True, exist_ok=True)
    log(f"用 {exe.name} 打开界面窗口")
    return [
        str(exe),
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        # 关键：界面自己绝不能走代理。
        # 用户很可能正开着系统代理，而本地 127.0.0.1 的页面一旦被送进代理，
        # 代理没起来就是整片白屏。
        "--no-proxy-server",
        "--window-size=1000,700",
        "--disable-features=Translate,MediaRouter",
        "--disable-background-networking",
    ]


def launch_window(url: str) -> bool:
    """开一个界面窗口就返回（不等它关），给「托盘要它出现在最前面」用。"""
    import subprocess

    args = _window_args(url)
    if not args:
        return False
    try:
        subprocess.Popen(args, cwd=str(Path(args[0]).parent),
                         creationflags=0x00000008)     # DETACHED_PROCESS
        return True
    except OSError as e:
        log(f"[!!] 开窗口失败：{e}")
        return False


def open_app_window(url: str) -> bool:
    """用 Chromium 的 --app 模式开一个「像桌面应用」的窗口，阻塞到窗口关闭。"""
    import subprocess

    args = _window_args(url)
    if not args:
        return False
    # 独立 user-data-dir 下，关掉窗口这个进程就会退出 —— 正好当退出信号
    proc = subprocess.Popen(args, cwd=str(Path(args[0]).parent),
                            creationflags=0x00000008)   # DETACHED_PROCESS
    proc.wait()
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    settings.ensure_dirs()

    # 关键：让 WebView2 直连，不走系统代理。
    # 用户很可能正开着系统代理（本产品的接管功能、或别的代理软件），
    # 而系统代理若没跳过本机地址，WebView2 会把 http://127.0.0.1:<端口> 也送进代理，
    # 代理一时没起来界面就整片白 —— 自己把自己锁在门外。
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--no-proxy-server")

    # 托盘点「设置…」时可能已经有窗口了：这时不再起第二个服务，
    # 直接把那个窗口切到目标页面并提到前台。
    want_view = ""
    if "--view" in argv:
        i = argv.index("--view")
        if i + 1 < len(argv):
            want_view = argv[i + 1]

    existing = running_port()
    if existing:
        log(f"界面已在 {existing} 运行，切到 {want_view or '主界面'}")
        try:
            _post(existing, "/api/view", {"view": want_view or "profile"})
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        if not focus_window():
            launch_window(_view_url(existing, want_view or "profile"))
        return 0

    lock = _acquire_lock()
    if lock is None:
        log("已经有一个主界面在运行，本次不重复打开")
        _shout("主界面已经打开了，看一下任务栏。")
        return 0

    try:
        import webview
    except ImportError:
        log("[!!] 没装 pywebview，改用浏览器打开")
        webview = None

    port = GUI_PORT if not _port_open(GUI_PORT, timeout=0.2) else _free_port()
    box = ServerBox()
    box.port = port

    t = threading.Thread(target=serve_http, args=(port, box), daemon=True, name="http")
    t.start()

    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if _port_open(port):
            break
        time.sleep(0.1)
    else:
        log(f"[!!] 本地服务 {STARTUP_TIMEOUT:.0f} 秒内没起来")
        return 1

    try:
        port_file().write_text(str(port), encoding="utf-8")
    except OSError as e:
        log(f"[!!] 端口文件写不了（托盘会退回探固定端口）：{e}")
    log(f"界面服务 http://127.0.0.1:{port}/")

    stop = threading.Event()
    threading.Thread(target=gc_loop, args=(stop,), daemon=True, name="gc").start()

    url = _view_url(port, want_view or "profile")
    try:
        want_native = "--native" in argv
        if want_native and webview is not None:
            _run_pywebview(webview, url, argv)
        elif open_app_window(url):
            pass                      # 窗口关闭后自然返回
        elif webview is not None:
            log("[--] 没找到 Chrome/Edge，退回 pywebview")
            _run_pywebview(webview, url, argv)
        else:
            import webbrowser
            webbrowser.open(url)
            log("已在浏览器打开界面。按 Ctrl+C 退出。")
            while True:
                time.sleep(3600)
    finally:
        stop.set()
        try:
            if box.server is not None:
                box.server.should_exit = True
        except Exception:  # noqa: BLE001
            pass
        try:
            lock.close()
        except OSError:
            pass
        try:                        # 端口文件是「界面还活着」的路标，退出就擦掉
            port_file().unlink()
        except OSError:
            pass
        log("界面已关闭")
    return 0


def _run_pywebview(webview, url: str, argv: list[str]) -> None:
    """原生窗口（WebView2 后端）。环境正常时可用，出问题用 --app 那条路。"""
    icon = settings.icons_dir() / "tray.ico"
    webview.create_window(TITLE, url, width=980, height=680,
                          min_size=(860, 560), text_select=False,
                          background_color="#F4F4F7")
    webview.start(icon=str(icon) if icon.is_file() else None,
                  debug="--debug" in argv)


if __name__ == "__main__":
    sys.exit(main())

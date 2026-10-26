# -*- coding: utf-8 -*-
"""托盘界面：右下角一个图标，只负责「日常那几下」，细活全在主界面。

菜单结构（刻意压到 6 项，三秒能看完）：

    分应用代理管家 · 全部运行中（3/3）      ← 双击图标同效：打开主界面
    ├─ 打开主界面
    ├─ 设置…                              ← 直达设置页，不用先进主界面再找
    │  ─────
    ├─ 启动所有软件                         ← 开关对象是「软件」，不是线路
    ├─ 关闭所有软件（并释放内核）
    │  ─────
    ├─ 系统代理 ▸  关闭 / ✓ alpha（10801）/ beta（10803）
    │  ─────
    └─ 退出

为什么砍掉了原来的「线路 / 应用 / 开机自启 / 打开目录」四层？
    这些是「配置一次」的事，不是「每天点」的事。它们全在主界面里（设置页有
    线路管理、开机自启、目录入口；左侧 ＋ 有程序库）。托盘堆十几项只会让人
    找不到东西 —— 现在「打开主界面」和「设置…」两个口子都通向同一个界面，
    主界面里再明显放一个齿轮按钮，三条路都能到设置。

单实例：靠占用本地端口 10896 实现，第二个实例会自己退出。
所有耗时动作（起内核、测速、扫描）都丢后台线程，托盘菜单不卡。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pythonw 下没有标准输出
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = sys.stdout

import pystray  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from appproxy import (coreproc, gui, launcher, pool, procwatch,  # noqa: E402
                      profiles, settings, shortcut, sysproxy)
from appproxy.registry import Registry  # noqa: E402

LOCK_PORT = 10896
TITLE = "分应用代理管家"

C_OK = (34, 160, 90)
C_WARN = (230, 160, 30)
C_BAD = (200, 60, 60)
C_IDLE = (128, 134, 144)
C_SYS = (47, 107, 255)


# --------------------------------------------------------------------------- 日志
def log(msg: str) -> None:
    try:
        settings.logs_dir().mkdir(parents=True, exist_ok=True)
        with open(settings.logs_dir() / "tray.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- 图标
def tray_image(color: tuple[int, int, int], size: int = 64) -> Image.Image:
    """一条线分成两路 = 分流。托盘只有 16px，形状必须极简。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((3, 3, size - 4, size - 4), radius=14, fill=color + (255,))
    w = 6
    d.line([(size // 2, size - 14), (size // 2, size // 2)], fill=(255, 255, 255), width=w)
    d.line([(size // 2, size // 2), (size // 2 - 13, 14)], fill=(255, 255, 255), width=w)
    d.line([(size // 2, size // 2), (size // 2 + 13, 14)], fill=(255, 255, 255), width=w)
    d.ellipse((size // 2 - 4, size - 18, size // 2 + 4, size - 10), fill=(255, 255, 255))
    return img


def save_tray_icon() -> Path:
    """把托盘图标存成 .ico，供桌面快捷方式与开机自启使用。"""
    settings.icons_dir().mkdir(parents=True, exist_ok=True)
    path = settings.icons_dir() / "tray.ico"
    tray_image(C_SYS).save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32),
                                                     (48, 48), (64, 64), (128, 128),
                                                     (256, 256)])
    return path


# --------------------------------------------------------------------------- App
class Tray:
    def __init__(self) -> None:
        self.reg = Registry().load()
        self.store = profiles.ProfileStore().load()
        self.icon: pystray.Icon | None = None
        self._lock: socket.socket | None = None
        self._busy = False
        self._color = C_IDLE
        # 界面在不在跑：菜单文案要跟着变（窗口开着时「设置…」改成闪烁提示），
        # 每次都探一遍端口太浪费 —— 缓存 4 秒，最多慢半拍，菜单不会卡。
        self._gui_open = False
        self._gui_checked = 0.0

    def gui_open(self) -> bool:
        """界面窗口是不是已经开着（带 4 秒缓存）。"""
        now = time.time()
        if now - self._gui_checked < 4.0:
            return self._gui_open
        try:
            self._gui_open = gui.running_port() is not None
        except Exception:  # noqa: BLE001  探不到就当没开，别让菜单崩
            self._gui_open = False
        self._gui_checked = now
        return self._gui_open

    # -- 单实例 + 控制通道 ---------------------------------------------------
    def acquire_single(self) -> bool:
        """占住本地端口当单实例锁，同时把它当作一个极简控制通道。

        有了它，命令行就能让正在跑的托盘优雅退出（`tray --quit`），
        而不是靠杀进程留下一个"幽灵图标"。
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", LOCK_PORT))
            s.listen(4)
            s.settimeout(1.0)
        except OSError:
            s.close()
            return False
        self._lock = s
        threading.Thread(target=self._serve, daemon=True).start()
        return True

    def _serve(self) -> None:
        while self._lock is not None:
            try:
                conn, _ = self._lock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                conn.settimeout(2.0)
                cmd = conn.recv(64).decode("utf-8", "replace").strip().lower()
                if cmd == "quit":
                    conn.sendall(b"bye")
                    conn.close()
                    self.act_quit()
                    return
                conn.sendall(b"pong" if cmd == "ping" else b"?")
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    # -- 数据 ---------------------------------------------------------------
    def reload(self) -> None:
        self.reg = Registry().load()
        self.store = profiles.ProfileStore().load()
        if self.icon is not None:
            self.icon.menu = self.build_menu()
            self.refresh()

    def line_up(self, line) -> bool:
        return coreproc.port_open(line.http_port, timeout=0.15)

    def state(self) -> tuple[tuple[int, int, int], str]:
        ups = [self.line_up(l) for l in self.reg.lines]
        sp = sysproxy.read()
        if sp.get("enable") and any(sysproxy.matches_port(sp, l.http_port) and up
                                     for l, up in zip(self.reg.lines, ups)):
            return C_SYS, f"系统代理已开启（{sp.get('server')}）"
        if not ups:
            return C_IDLE, "没有线路"
        good, total = sum(ups), len(ups)
        if good == total:
            return C_OK, f"全部运行中（{good}/{total}）"
        if good == 0:
            return C_BAD, f"全部停止（0/{total}）"
        return C_WARN, f"部分运行中（{good}/{total}）"

    # -- 刷新 ---------------------------------------------------------------
    def refresh(self) -> None:
        if self.icon is None:
            return
        color, text = self.state()
        try:
            if color != self._color:
                self._color = color
                self.icon.icon = tray_image(color)
            self.icon.title = f"{TITLE} — {text}"
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    # -- 后台执行 -----------------------------------------------------------
    def bg(self, fn, *a, **kw) -> None:
        if self._busy:
            self.notify("上一个操作还没结束")
            return

        def run() -> None:
            self._busy = True
            try:
                fn(*a, **kw)
            except Exception as e:  # noqa: BLE001
                log(f"[!!] {type(e).__name__}: {e}")
                self.notify(f"失败：{e}")
            finally:
                self._busy = False
                self.refresh()

        threading.Thread(target=run, daemon=True).start()

    def notify(self, msg: str, title: str = TITLE) -> None:
        try:
            if self.icon is not None:
                self.icon.notify(msg, title)
        except Exception:  # noqa: BLE001
            pass

    # -- 批量动作（托盘只留这一对：开关对象是「软件」，不是线路）----------------
    def act_all_profiles(self, start: bool) -> None:
        """对「所有软件档案」批量动作。

        这才是日常该用的那对按钮：
            启动 = 每个软件按需拉起它自己那条线路的内核（不用预热，约 1 秒）
            关闭 = 结束软件进程，没人在用的内核随之释放
        """

        def run() -> None:
            store = profiles.ProfileStore().load()
            reg = Registry().load()
            snap = procwatch.snapshot()
            done = 0
            for prof in store.profiles:
                try:
                    if start:
                        if snap.alive(prof):
                            continue
                        launcher.launch(prof, reg)
                    else:
                        pool.close_profile(reg, store, prof)
                    done += 1
                except Exception as e:  # noqa: BLE001
                    log(f"[!!] {prof.id}: {type(e).__name__}: {e}")
            msg = f"{done} 个软件已启动" if start else f"{done} 个软件已关闭"
            log(msg)
            self.notify(msg)
            self.refresh()

        self.bg(run)

    # -- 进界面（两条路都落到同一个页面，避免用户找不到设置）--------------------
    def _open_view(self, view: str) -> None:
        def run() -> None:
            try:
                msg = gui.open_view(view)
                log(f"打开界面 {view}：{msg}")
                # 界面本来开着的时候，点击的效果是「切页 + 任务栏闪烁」，
                # 不弹个气泡用户会以为没反应 —— 所以成功也提示。
                self.notify(msg)
            except Exception as e:  # noqa: BLE001
                log(f"[!!] 打开界面失败：{type(e).__name__}: {e}")
                self.notify(f"打开界面失败：{e}")
            finally:
                self._gui_checked = 0.0     # 立刻重新探一次，菜单文案要跟上
                self.refresh()

        self.bg(run)

    def act_open_gui(self) -> None:
        """打开主界面（已经开着就切过去并提到最前）。"""
        self._open_view("profile")

    def act_open_settings(self) -> None:
        """直接进设置页 —— 托盘点两下就到，不用先开界面再找齿轮。"""
        self._open_view("settings")

    # -- 系统代理 -----------------------------------------------------------
    def act_sysproxy(self, port: int | None) -> None:
        def run() -> None:
            if not port:
                st = sysproxy.disable()
                log(f"系统代理：{sysproxy.describe(st)}")
                self.notify("系统代理已关闭（已还原接管前的设置）")
                return
            st = sysproxy.enable(port)
            log(f"系统代理：{sysproxy.describe(st)}")
            self.notify(f"系统代理 → 127.0.0.1:{port}\n（关闭时会还原原来的设置）")
        self.bg(run)

    # -- 其它 ---------------------------------------------------------------
    # 开机自启、打开数据/日志目录这些「配置一次」的入口已挪到主界面设置页，
    # 托盘不再各留一份 —— 两条路都通向同一个地方，才不会让人猜哪个才是真的。
    def act_quit(self) -> None:
        """退出 = 收拾干净：停掉我们拉起来的内核，别留一堆没界面的后台进程。"""
        log("托盘退出，正在停止内核…")
        try:
            for line in Registry().load().lines:
                try:
                    coreproc.stop(line.config_path())
                except Exception as e:  # noqa: BLE001
                    log(f"[!!] 停止 {line.id} 失败：{type(e).__name__}: {e}")
        finally:
            log("托盘退出")
            if self.icon is not None:
                self.icon.stop()
            if self._lock is not None:
                try:
                    self._lock.close()
                except OSError:
                    pass
                self._lock = None

    # -- 菜单 ---------------------------------------------------------------
    def build_menu(self) -> pystray.Menu:
        """顶层 6 项封顶。

        配置类的东西（线路、程序库、开机自启、目录）全在主界面 ——
        它们在托盘的子菜单里翻三层才能找到，比在主界面点一下难得多。
        """
        _, summary = self.state()
        sp = sysproxy.read()

        sys_items = [
            pystray.MenuItem(lambda it, t=sysproxy.describe(sp): f"当前：{t}",
                             None, enabled=False),
            pystray.MenuItem("关闭系统代理（还原原设置）",
                             lambda icon, item: self.act_sysproxy(None)),
        ]
        for line in self.reg.lines:
            checked = sysproxy.matches_port(sp, line.http_port)
            sys_items.append(pystray.MenuItem(
                (lambda it, t=f"{line.name}（{line.http_port}）", c=checked:
                 ("✓ " if c else "　") + t),
                (lambda port: (lambda icon, item: self.act_sysproxy(port)))(line.http_port),
                radio=True, checked=(lambda it, c=checked: c)))
        if not self.reg.lines:
            sys_items.append(pystray.MenuItem("（先在主界面设置里添加线路）",
                                              None, enabled=False))
        sys_menu = pystray.Menu(*sys_items)

        # 先探一次界面在不在，两个动态文案后面直接读缓存，右键不卡
        self.gui_open()

        return pystray.Menu(
            # 点这一行 = 打开主界面（双击托盘图标同效），summary 顺便当状态栏
            pystray.MenuItem(f"{TITLE} · {summary}", self.act_open_gui, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                (lambda it: "显示主界面（已在后台）" if self.gui_open() else "打开主界面"),
                lambda icon, item: self.act_open_gui()),
            # 窗口开着时这一项不再「开新窗口」，而是切到设置页 + 让窗口在任务栏闪，
            # 文案直接写明白，省得用户以为点了没反应。
            pystray.MenuItem(
                (lambda it: "设置（切到界面并闪烁提示）" if self.gui_open() else "设置…"),
                lambda icon, item: self.act_open_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("启动所有软件", lambda icon, item: self.act_all_profiles(True)),
            pystray.MenuItem("关闭所有软件（并释放内核）",
                             lambda icon, item: self.act_all_profiles(False)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("系统代理", sys_menu),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda icon, item: self.act_quit()),
        )

    # -- 运行 ---------------------------------------------------------------
    def _auto_start(self) -> None:
        """开机自启时：把该起来的线路拉起来（不拦路，失败只记日志）。"""
        try:
            for line in Registry().load().lines:
                if coreproc.port_open(line.http_port):
                    continue
                try:
                    reg = Registry().load()
                    log(launcher.ensure_core(reg, reg.require(line.id)))
                except Exception as e:  # noqa: BLE001
                    log(f"[!!] 自启拉线路 {line.id} 失败：{type(e).__name__}: {e}")
        finally:
            self.refresh()

    def run(self, autostart_lines: bool = False) -> None:
        """托盘常驻。

        autostart_lines 默认 False：内核是**按需**的，开机就把所有线路拉起来
        等于退回全局代理软件那套常驻模型。要预热请用手动开关。
        """
        if not self.acquire_single():
            log("已经有一个托盘在运行，本次退出")
            return
        save_tray_icon()
        color, summary = self.state()
        self._color = color
        self.icon = pystray.Icon("appproxy", tray_image(color),
                                 f"{TITLE} — {summary}", self.build_menu())
        if autostart_lines:
            threading.Thread(target=self._auto_start, daemon=True).start()

        def ticker() -> None:
            while True:
                time.sleep(6)
                try:
                    for item in pool.gc():
                        log(f"自动回收内核 {item['line']}（端口 {item['port']} 已释放）")
                except Exception:  # noqa: BLE001  回收失败不能让托盘停摆
                    pass
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=ticker, daemon=True).start()
        self.icon.run()


def send_command(cmd: str, timeout: float = 3.0) -> str:
    """给正在跑的托盘发一条指令（quit / ping）。没在跑会抛 OSError。"""
    with socket.create_connection(("127.0.0.1", LOCK_PORT), timeout) as s:
        s.sendall(cmd.encode("utf-8"))
        return s.recv(64).decode("utf-8", "replace")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    settings.ensure_dirs()

    if "--quit" in argv:
        try:
            print(f"[OK] 已通知托盘退出（{send_command('quit')}）")
        except OSError:
            print("[--] 没有正在运行的托盘")
        return 0

    log(f"========== 托盘启动 pid={os.getpid()} ==========")
    icon_path = save_tray_icon()
    log(f"托盘图标 {icon_path}")

    if "--install" in argv:
        made = shortcut.install_tray()
        for m in made:
            log(f"快捷方式 {m}")
            print(f"[OK] {m}")

    # 默认不预热内核（--warm 才预热）。内核按需起，用户能接受那 1 秒。
    Tray().run(autostart_lines="--warm" in argv)
    log("========== 托盘退出 ==========")
    return 0


if __name__ == "__main__":
    sys.exit(main())

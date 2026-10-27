# -*- coding: utf-8 -*-
"""打包后的可执行入口（PyInstaller 用，源码运行时不用它）。

行为：
    双击（无参数）   → 先体检环境；有问题先弹提示框；没问题再按下面走
                       有新线路就出托盘；一条线路都没有就直接开接线界面
    带参数           → 原样转给 appproxy.cli，例如 AppProxy.exe gui / doctor

为什么开头要接管 stdout/stderr：
    打包成无控制台窗口的 exe 后，sys.stdout 是 None，任何一句 print 都会抛
    AttributeError 把程序打死。这里先把它们指向空设备，日志统一走 appproxy 的
    logs 目录。
"""
from __future__ import annotations

import os
import sys


def _fix_streams() -> None:
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except OSError:      # 极端情况下连空设备都打不开，给个哑对象
                class _Sink:
                    def write(self, *_a, **_k):
                        return 0

                    def flush(self):
                        return None

                setattr(sys, name, _Sink())


def _attach_console() -> bool:
    """打包后的 exe 是「无控制台窗口」的，从 cmd 里跑 `AppProxy.exe doctor`
    什么都看不到。父进程有控制台就挂上去，并临时把输出代码页切成 UTF-8，
    这样中文不会变成乱码。挂不上（双击启动）就返回 False，什么也不做。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32            # type: ignore[attr-defined]
        if not k32.AttachConsole(0xFFFFFFFF):   # ATTACH_PARENT_PROCESS
            return False
        k32.SetConsoleOutputCP(65001)
        handle = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stdout = handle
        sys.stderr = handle
        return True
    except Exception:                           # noqa: BLE001
        return False


def _guard() -> None:
    """双击启动时的环境拦截 —— 跑不起来就当场说清楚，别让人对着黑屏猜。

    只挡 block 级问题（系统太老、内核丢了、目录写不进去）。warn 级的
    （比如没装 VC++ 运行库）不拦人，进界面后会显示在设置页里。
    """
    try:
        from appproxy import preflight
        bad = preflight.blocking()
    except Exception:            # noqa: BLE001  体检自己出错绝不能拖死启动
        return
    if bad:
        preflight.show_blocking_dialog(bad)
        raise SystemExit(1)


def main() -> int:
    argv = sys.argv[1:]

    if argv:
        # 命令行用法：能挂上父控制台就把输出露出来，挂不上再静默
        if not _attach_console():
            _fix_streams()
        from appproxy import cli
        try:
            return cli.main(argv)
        finally:
            # 挂到 CONOUT$ 的流不是 Python 自己开的，退出前手动刷一次
            for s in (sys.stdout, sys.stderr):
                try:
                    s.flush()
                except Exception:        # noqa: BLE001
                    pass

    _fix_streams()
    _guard()

    # 双击：没接过线就先把接线页摆出来，否则安静地待在托盘里
    try:
        from appproxy import settings
        configured = settings.registry_path().is_file()
    except Exception:            # noqa: BLE001  判断失败按老用户处理，别拦着人用
        configured = True

    from appproxy import cli
    return cli.main(["tray"] if configured else ["gui"])


if __name__ == "__main__":
    sys.exit(main())

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

_STD_OUTPUT = -11
_STD_ERROR = -12
_FILE_TYPE_DISK = 1        # 输出被重定向到文件
_FILE_TYPE_PIPE = 3        # 输出被接进了管道

_console_cp: int | None = None      # 挂控制台前的原代码页，退出时要还原


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


def _redirected_handles():
    """父进程把输出接到了文件或管道时，返回 (out, err) 句柄；都不是就返回 None。

    为什么必须先判断：cmd 里跑 `AppProxy.exe doctor > doctor.txt` 时，标准输出
    已经被换成文件句柄了，而下面 `_attach_console()` 挂上控制台后再开 CONOUT$，
    等于把 stdout 换成「屏幕」—— 文件里一个字节都收不到。用户看到的结果就是
    「命令跑完了，日志是空的」。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        k32 = ctypes.windll.kernel32            # type: ignore[attr-defined]
        out = k32.GetStdHandle(_STD_OUTPUT)
        err = k32.GetStdHandle(_STD_ERROR)
        out_ok = k32.GetFileType(out) in (_FILE_TYPE_DISK, _FILE_TYPE_PIPE)
        err_ok = k32.GetFileType(err) in (_FILE_TYPE_DISK, _FILE_TYPE_PIPE)
        if out_ok or err_ok:
            return (out if out_ok else None, err if err_ok else None)
    except Exception:                           # noqa: BLE001
        return None
    return None


def _wrap_handle(handle):
    """把继承来的 Windows 句柄包成 Python 文本流（不改动句柄本身）。"""
    import msvcrt
    fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
    return open(fd, "w", encoding="utf-8", errors="replace", closefd=False)


def _prepare_std_streams() -> None:
    """命令行用法下，按「有没有重定向」选一条正确的输出通路。"""
    pair = _redirected_handles()
    if pair:
        out, err = pair
        out = out if out is not None else err
        err = err if err is not None else out
        try:
            sys.stdout = _wrap_handle(out)
            sys.stderr = _wrap_handle(err)
            return
        except Exception:                        # noqa: BLE001  包装失败退回控制台
            pass
    if not _attach_console():
        _fix_streams()


def _attach_console() -> bool:
    """打包后的 exe 是「无控制台窗口」的，从 cmd 里跑 `AppProxy.exe doctor`
    什么都看不到。父进程有控制台就挂上去，并临时把输出代码页切成 UTF-8，
    这样中文不会变成乱码。挂不上（双击启动）就返回 False，什么也不做。
    """
    global _console_cp
    if os.name != "nt":
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32            # type: ignore[attr-defined]
        if not k32.AttachConsole(0xFFFFFFFF):   # ATTACH_PARENT_PROCESS
            return False
        _console_cp = k32.GetConsoleOutputCP()
        k32.SetConsoleOutputCP(65001)
        handle = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stdout = handle
        sys.stderr = handle
        return True
    except Exception:                           # noqa: BLE001
        return False


def _restore_console_cp() -> None:
    """把控制台代码页还回去 —— 不然跑完这一条，用户自己的 cmd 里中文就花了。"""
    if _console_cp is None or os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(_console_cp)   # type: ignore[attr-defined]
    except Exception:                           # noqa: BLE001
        pass


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
        # 命令行用法：重定向就用文件句柄，否则挂父控制台，都拿不到再静默
        _prepare_std_streams()
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
            _restore_console_cp()

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

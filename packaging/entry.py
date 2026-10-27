# -*- coding: utf-8 -*-
"""打包后的可执行入口（PyInstaller 用，源码运行时不用它）。

行为：
    双击（无参数）   → 有新线路就出托盘；一条线路都没有就直接开接线界面
    带参数           → 原样转给 appproxy.cli，例如 AppProxy.exe gui / status

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


def main() -> int:
    _fix_streams()
    argv = sys.argv[1:]

    if argv:
        from appproxy import cli
        return cli.main(argv)

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

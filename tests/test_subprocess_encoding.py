# -*- coding: utf-8 -*-
"""子进程输出的编码。

真机症状：打包后的 exe 在中文 Windows 上跑 `add`，命令本身成功，
控制台却刷一屏 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xae` ——
出在 subprocess 的读输出线程里。原因是子进程（schtasks / netsh）输出的是
系统代码页（GBK），而打包版 Python 按 UTF-8 模式解码。

修法：凡是 `capture_output=True, text=True` 的调用必须显式给
`encoding="utf-8", errors="replace"`。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "appproxy"


def _py_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py"))


def test_no_text_capture_without_encoding():
    """抓所有 subprocess 调用：开了 text=True 就必须给 encoding。"""
    bad: list[str] = []
    for f in _py_files():
        text = f.read_text(encoding="utf-8")
        # 逐个找 subprocess.run / Popen 的调用块
        for m in re.finditer(r"subprocess\.(?:run|Popen)\(", text):
            start = m.start()
            # 粗略取到下一个右括号前 600 字符作为调用块
            block = text[start:start + 700]
            if "text=True" not in block:
                continue
            if "encoding=" not in block:
                line = text[:start].count("\n") + 1
                bad.append(f"{f.relative_to(SRC.parent)}:{line}")
    assert not bad, "这些 subprocess 调用开了 text=True 却没给 encoding：" + ", ".join(bad)


def test_elevate_uses_utf8():
    from appproxy import elevate
    src = inspect.getsource(elevate._schtasks)
    assert "encoding=" in src
    assert "errors=" in src


def test_registry_netsh_uses_utf8():
    from appproxy import registry
    src = inspect.getsource(registry._excluded_ranges)
    assert "encoding=" in src


@pytest.mark.parametrize("mod", ["elevate", "registry"])
def test_modules_import_clean(mod):
    __import__(f"appproxy.{mod}")

# -*- coding: utf-8 -*-
"""打包入口（packaging/entry.py）的输出通路回归测试。

背景：v0.6.1 的 exe 在 cmd 里跑 `AppProxy.exe doctor > doctor.txt` 时，
输出全进了控制台，文件 0 字节 —— 因为入口无条件挂 CONOUT$，把父进程
已经重定向好的文件句柄换掉了。这里把两种情形都钉住。
"""
from __future__ import annotations

import importlib.util
import msvcrt
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def entry():
    spec = importlib.util.spec_from_file_location(
        "packaging_entry", ROOT / "packaging" / "entry.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fake_sys(entry, monkeypatch):
    """换掉 entry 模块眼里的 sys，避免污染 pytest 自己的 stdout。"""
    fake = types.SimpleNamespace(stdout=None, stderr=None)
    monkeypatch.setattr(entry, "sys", fake)
    return fake


def test_wrap_handle_writes_into_redirected_file(entry, tmp_path):
    """句柄包装后写进去的内容要真的落到文件里，且中文不乱码。"""
    target = tmp_path / "doctor.txt"
    f = open(target, "w", encoding="utf-8")
    try:
        stream = entry._wrap_handle(msvcrt.get_osfhandle(f.fileno()))
        stream.write("运行环境体检：内核正常\n")
        stream.flush()
    finally:
        f.close()
    assert "运行环境体检" in target.read_text(encoding="utf-8")


def test_prepare_prefers_redirect_over_console(entry, fake_sys, monkeypatch):
    """有重定向时不能去挂控制台，否则目标文件收不到任何输出。"""
    called = {"attach": 0}

    def fake_attach():
        called["attach"] += 1
        return True

    monkeypatch.setattr(entry, "_attach_console", fake_attach)
    monkeypatch.setattr(entry, "_redirected_handles", lambda: (11, 12))
    monkeypatch.setattr(entry, "_wrap_handle", lambda _h: "wrapped")

    entry._prepare_std_streams()

    assert called["attach"] == 0
    assert fake_sys.stdout == "wrapped"
    assert fake_sys.stderr == "wrapped"


def test_prepare_falls_back_to_sink_when_nothing_available(entry, fake_sys, monkeypatch):
    """既没重定向也挂不上控制台（双击启动）时，stdout 不能是 None。"""
    monkeypatch.setattr(entry, "_redirected_handles", lambda: None)
    monkeypatch.setattr(entry, "_attach_console", lambda: False)

    entry._prepare_std_streams()

    assert fake_sys.stdout is not None
    assert fake_sys.stderr is not None
    fake_sys.stdout.write("不能抛异常")      # 哑对象只要求不炸


def test_console_codepage_restored(entry, monkeypatch):
    """挂控制台改了代码页，退出时必须还原，不然用户的 cmd 会花。"""
    seen = {}

    class _K32:
        @staticmethod
        def GetConsoleOutputCP():
            return 936

        @staticmethod
        def SetConsoleOutputCP(cp):
            seen["cp"] = cp

    fake_ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(kernel32=_K32))
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(entry, "_console_cp", 936)

    entry._restore_console_cp()

    assert seen["cp"] == 936

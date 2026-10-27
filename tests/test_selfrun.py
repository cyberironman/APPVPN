# -*- coding: utf-8 -*-
"""selfrun：源码形态与打包形态的「重新拉起自己」。

这三条最容易在打包后才炸：
    * 快捷方式指向的 exe
    * 提权子进程的落点
    * 托盘切界面时新起的进程
所以两种形态都要有测试兜着。
"""
from __future__ import annotations

import sys

from appproxy import selfrun


def test_source_mode_uses_module_entry(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert selfrun.is_frozen() is False

    argv = selfrun.self_argv(["gui"])
    assert argv[1:3] == ["-m", "appproxy.cli"]
    assert argv[-1] == "gui"
    assert argv[0].lower().endswith(".exe")
    # 源码模式优先 pythonw.exe：重新拉起的进程不该弹黑框
    assert "python" in argv[0].lower()


def test_frozen_mode_runs_exe_directly(monkeypatch, tmp_path):
    fake = tmp_path / "AppProxy.exe"
    fake.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake))

    assert selfrun.is_frozen() is True
    assert selfrun.self_exe() == fake
    # 打包后不能再带 -m appproxy.cli，否则 exe 认不出来
    assert selfrun.self_argv(["tray"]) == [str(fake), "tray"]


def test_frozen_args_quote_paths_with_spaces(monkeypatch, tmp_path):
    fake = tmp_path / "AppProxy.exe"
    fake.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake))

    args = selfrun.self_args(["elevate", "install", "--binary",
                              r"D:\My Apps\sing-box.exe"])
    assert '-m' not in args
    # 路径里有空格必须自己加引号 —— ShellExecuteW 不会替我们补
    assert '"D:\\My Apps\\sing-box.exe"' in args


def test_self_process_names_covers_frozen_exe(monkeypatch, tmp_path):
    fake = tmp_path / "AppProxy.exe"
    fake.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake))

    names = selfrun.self_process_names()
    assert "appproxy.exe" in names
    assert "python.exe" in names

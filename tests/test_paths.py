# -*- coding: utf-8 -*-
"""数据根目录解析 —— 「软件放在哪都能用」的底层保障。

规则（用户定的）：
    * 数据默认放在**用户选的安装目录**里（`<软件目录>\\AppProxyData`），
      装在哪个目录数据就在哪，整个文件夹搬走数据跟着走；
    * 软件里可以另外指定一个数据目录；
    * 安装时不额外问一遍数据放哪。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from appproxy import settings


@pytest.fixture
def frozen(monkeypatch):
    """模拟「打包后的 exe」，并把注册表置空、默认按安装版处理。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("APPPROXY_HOME", raising=False)
    monkeypatch.setattr(settings, "data_dir_pref", lambda: None)
    return monkeypatch


def test_env_variable_wins(frozen, tmp_path):
    frozen.setenv("APPPROXY_HOME", str(tmp_path / "custom"))
    assert settings.home() == tmp_path / "custom"


def test_user_choice_beats_default(frozen, tmp_path):
    """安装版里设过的数据目录优先级高于「程序旁边」。"""
    pinned = tmp_path / "我在E盘" / "数据"
    frozen.setattr(settings, "data_dir_pref", lambda: pinned)
    assert settings.home() == pinned


def test_portable_ignores_registry(frozen, tmp_path):
    """绿色版不读注册表 —— 否则先装过安装版的人，解压绿色版会看到别人的订阅。"""
    exe = tmp_path / "U盘" / "AppProxy"
    exe.mkdir(parents=True)
    (exe / settings.PORTABLE_FLAG).write_text("", encoding="utf-8")
    frozen.setattr(settings, "exe_dir", lambda: exe)
    frozen.setattr(settings, "data_dir_pref", lambda: tmp_path / "安装版数据")
    assert settings.home() == exe / settings.PORTABLE_DIR


def test_default_is_next_to_exe(frozen, tmp_path):
    """默认就是软件目录下的 AppProxyData —— 装哪就在哪。"""
    frozen.setattr(settings, "is_portable", lambda: False)
    exe = tmp_path / "我的软件"
    exe.mkdir()
    frozen.setattr(settings, "exe_dir", lambda: exe)
    frozen.setattr(settings, "dir_writable", lambda p: True)
    assert settings.home() == exe / settings.PORTABLE_DIR


def test_readonly_install_dir_falls_back(frozen, tmp_path, monkeypatch):
    """装在 Program Files 且没授权 → 退到 LocalAppData，保证能跑。"""
    frozen.setattr(settings, "is_portable", lambda: False)
    exe = tmp_path / "ProgramFiles" / "AppProxy"
    exe.mkdir(parents=True)
    frozen.setattr(settings, "exe_dir", lambda: exe)
    frozen.setattr(settings, "dir_writable", lambda p: False)
    frozen.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    assert settings.home() == tmp_path / "LocalAppData" / "AppProxy"


def test_source_mode_keeps_d_drive(monkeypatch):
    """源码运行（开发机）行为不变：有 D 盘就用 D:\\AppProxy。"""
    monkeypatch.delenv("APPPROXY_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(settings, "data_dir_pref", lambda: None)
    if not Path("D:/").is_dir():
        pytest.skip("本机没有 D 盘")
    monkeypatch.setattr(settings, "DEFAULT_HOME", Path(r"D:\AppProxy"))
    assert settings.home() == Path(r"D:\AppProxy")


def test_source_mode_without_d_drive(monkeypatch, tmp_path):
    monkeypatch.delenv("APPPROXY_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(settings, "data_dir_pref", lambda: None)
    monkeypatch.setattr(settings, "DEFAULT_HOME", Path("Q:/AppProxy"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert settings.home() == tmp_path / "AppProxy"


def test_local_app_data_fallback_without_env(frozen, tmp_path):
    frozen.setattr(settings, "exe_dir", lambda: tmp_path / "ro")
    frozen.setattr(settings, "dir_writable", lambda p: False)
    frozen.delenv("LOCALAPPDATA", raising=False)
    frozen.delenv("APPDATA", raising=False)
    assert settings.home() == Path.home() / ".appproxy"


def test_descendants_follow_home(monkeypatch, tmp_path):
    monkeypatch.setenv("APPPROXY_HOME", str(tmp_path / "h"))
    assert settings.data_dir() == tmp_path / "h" / "data"
    assert settings.logs_dir() == tmp_path / "h" / "logs"
    assert settings.registry_path() == tmp_path / "h" / "data" / "lines.json"


def test_exe_dir_matches_code_root_in_source_mode():
    assert settings.exe_dir() == settings.code_root()


def test_set_and_clear_data_dir(tmp_path, monkeypatch):
    """改数据目录只写一个注册表值，不碰任何文件。"""
    written: dict[str, str] = {}
    monkeypatch.setattr(settings, "_reg_set", lambda k, v: written.update({k: v}) or True)
    assert settings.set_data_dir(tmp_path / "x") is True
    assert written["DataDir"] == str(tmp_path / "x")
    assert settings.clear_data_dir() is True
    assert written["DataDir"] == ""


def test_dir_writable_true_for_temp(tmp_path):
    assert settings.dir_writable(tmp_path) is True

# -*- coding: utf-8 -*-
"""数据根目录解析。

绿色包要能搬到任何一台 Windows 上跑，所以「D 盘不存在」必须有不崩的退路 ——
不能因为没 D 盘就在第一次运行时报错。
"""
from __future__ import annotations

from pathlib import Path

from appproxy import settings


def test_env_variable_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("APPPROXY_HOME", str(tmp_path / "custom"))
    assert settings.home() == tmp_path / "custom"


def test_uses_d_drive_when_present(monkeypatch):
    monkeypatch.delenv("APPPROXY_HOME", raising=False)
    if not Path("D:/").is_dir():
        return                                   # 本机没有 D 盘，跳过
    monkeypatch.setattr(settings, "DEFAULT_HOME", Path(r"D:\AppProxy"))
    assert settings.home() == Path(r"D:\AppProxy")


def test_falls_back_when_d_drive_missing(monkeypatch):
    """盘符不存在时退到程序旁边的 AppProxyData，而不是抛异常。"""
    monkeypatch.delenv("APPPROXY_HOME", raising=False)
    monkeypatch.setattr(settings, "DEFAULT_HOME", Path("Q:/AppProxy"))
    h = settings.home()
    assert h == settings.code_root() / "AppProxyData"


def test_descendants_follow_home(monkeypatch, tmp_path):
    monkeypatch.setenv("APPPROXY_HOME", str(tmp_path / "h"))
    assert settings.data_dir() == tmp_path / "h" / "data"
    assert settings.logs_dir() == tmp_path / "h" / "logs"
    assert settings.registry_path() == tmp_path / "h" / "data" / "lines.json"

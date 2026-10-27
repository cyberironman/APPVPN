# -*- coding: utf-8 -*-
"""软件里改数据目录（设置页「数据目录 → 更改…」）。

用户要求：数据默认跟着安装目录，但要能在软件里再改。改的时候不能把已有配置弄丢，
更不能把旧目录删掉（用户反悔要能改回来）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from appproxy import settings
from appproxy.webui.api import DataDirBody, post_data_dir


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """把 home 指到一个假的老目录，注册表写入改为记录到字典。"""
    old = tmp_path / "老位置"
    (old / "data").mkdir(parents=True)
    (old / "data" / "lines.json").write_text('{"lines": []}', encoding="utf-8")
    (old / "logs").mkdir()
    (old / "logs" / "tray.log").write_text("日志不该被复制", encoding="utf-8")

    written: dict[str, str] = {}
    monkeypatch.setattr(settings, "home", lambda: old)
    monkeypatch.setattr(settings, "is_portable", lambda: False)
    monkeypatch.setattr(settings, "dir_writable", lambda p: True)
    monkeypatch.setattr(settings, "set_data_dir",
                        lambda p: written.update({"path": str(p)}) or True)
    monkeypatch.setattr(settings, "clear_data_dir",
                        lambda: written.update({"path": ""}) or True)
    monkeypatch.setattr(settings, "clear_data_dir",
                        lambda: written.update({"path": ""}) or True)
    return {"old": old, "written": written, "root": tmp_path}


def test_change_copies_config_but_not_logs(fake):
    new = fake["root"] / "新位置"
    r = post_data_dir(DataDirBody(path=str(new), copy_existing=True))
    assert r["ok"] is True and r["restart"] is True
    # 配置复制过去了
    assert (new / "data" / "lines.json").is_file()
    # 日志不复制（没价值，而且可能正在写）
    assert not (new / "logs" / "tray.log").exists()
    # 记住了新位置
    assert Path(fake["written"]["path"]) == new
    # 老目录还在（用户反悔能改回来）
    assert (fake["old"] / "data" / "lines.json").is_file()


def test_change_without_copy(fake):
    new = fake["root"] / "空的"
    r = post_data_dir(DataDirBody(path=str(new), copy_existing=False))
    assert r["ok"] is True
    assert not (new / "data" / "lines.json").exists()


def test_empty_path_restores_default(fake):
    r = post_data_dir(DataDirBody(path="", copy_existing=False))
    assert r["ok"] is True
    assert fake["written"]["path"] == ""


def test_nested_path_refused(fake):
    """目标在旧目录里面会无限递归，必须拒掉。"""
    inside = fake["old"] / "子目录"
    with pytest.raises(HTTPException):
        post_data_dir(DataDirBody(path=str(inside), copy_existing=True))


def test_same_path_refused(fake):
    with pytest.raises(HTTPException):
        post_data_dir(DataDirBody(path=str(fake["old"]), copy_existing=True))


def test_portable_refused(fake, monkeypatch):
    """绿色版的数据跟着文件夹走，改注册表没用，直接告诉用户怎么回事。"""
    monkeypatch.setattr(settings, "is_portable", lambda: True)
    with pytest.raises(HTTPException) as e:
        post_data_dir(DataDirBody(path=str(fake["root"] / "x"), copy_existing=True))
    assert "绿色版" in str(e.value.detail)


def test_unwritable_target_refused(fake, monkeypatch):
    monkeypatch.setattr(settings, "dir_writable", lambda p: False)
    with pytest.raises(HTTPException) as e:
        post_data_dir(DataDirBody(path=str(fake["root"] / "只读"), copy_existing=True))
    assert "权限" in str(e.value.detail)

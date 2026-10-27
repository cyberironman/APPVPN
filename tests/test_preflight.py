# -*- coding: utf-8 -*-
"""环境体检：报出来的东西必须能指导用户动手修。

这个模块是给「双击没反应」的用户准备的 —— 它说的话就是用户看到的全部信息，
所以测试重点不是内部实现，而是「每条问题都带可执行的处理方式」。
"""
from __future__ import annotations

from pathlib import Path

from appproxy import preflight


def test_report_returns_well_formed_items(tmp_home):
    items = preflight.report()
    assert isinstance(items, list)
    for i in items:
        assert set(i) >= {"key", "level", "title", "detail", "fix", "fix_label"}
        assert i["level"] in ("block", "warn")
        assert i["title"] and i["detail"]


def test_every_fixable_issue_has_a_label(tmp_home):
    """有问题就必须有按钮文字，不能出现「知道有问题但没告诉你怎么办」。"""
    for i in preflight.report():
        if i["fix"]:
            assert i["fix_label"], i


def test_blocking_is_subset_of_report(tmp_home):
    blocks = preflight.blocking()
    assert all(i["level"] == "block" for i in blocks)
    assert preflight.is_healthy() == (not blocks)


def test_os_release_is_triple():
    major, minor, build = preflight.os_release()
    assert all(isinstance(x, int) for x in (major, minor, build))
    assert major >= 0


def test_data_writable_in_temp(tmp_home):
    assert preflight.data_writable() is True


def test_free_space_is_positive(tmp_home):
    assert preflight.free_space_mb() > 0


def test_unknown_fix_key_is_refused():
    r = preflight.fix("nonexistent")
    assert r["ok"] is False
    assert "nonexistent" in r["message"]


def test_wintun_detected_next_to_engine(tmp_path, monkeypatch):
    """wintun.dll 放在内核旁边就算有 —— 这是随包分发的方式。"""
    engine = tmp_path / "sing-box"
    engine.mkdir()
    (engine / "sing-box.exe").write_bytes(b"")
    monkeypatch.setattr(preflight, "engine_dir", lambda: engine)
    assert preflight.has_wintun() is False
    (engine / preflight.WINTUN_DLL).write_bytes(b"")
    assert preflight.has_wintun() is True


def test_engine_fix_points_at_reinstall(tmp_home):
    r = preflight.fix("engine")
    assert r["ok"] is False
    assert "解压" in r["message"] or "安装" in r["message"]


def test_vc_runtime_check_is_boolean():
    assert isinstance(preflight.vc_runtime_installed(), bool)


def test_blocking_dialog_never_raises(tmp_home, monkeypatch):
    """弹窗失败也不能把启动流程打死（远程桌面、无桌面会话都可能失败）。"""
    def boom(*_a, **_k):
        raise RuntimeError("no desktop")
    monkeypatch.setattr("ctypes.windll.user32.MessageBoxW", boom, raising=False)
    preflight.show_blocking_dialog([{"title": "t", "detail": "d"}])

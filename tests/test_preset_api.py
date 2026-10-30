# -*- coding: utf-8 -*-
"""常用软件预置的接口测试。

钉住三件事：
  1. 设置页能拿到预置状态（几个在列表里、缺哪几个）；
  2. 删掉预置档案 -> 记录进「已删除」，以后不许自动长回来；
  3. 「补回来」能把删掉的重新建起来，且清掉删除记录。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from appproxy import appfind, icons, preset, shortcut
from appproxy.models import Line
from appproxy.profiles import ProfileStore
from appproxy.registry import Registry
from appproxy.webui import api


@pytest.fixture()
def env(tmp_home, tmp_path, monkeypatch):
    """本机只装了 Chrome，桌面版软件一律当作没装。"""
    chrome = tmp_path / "bin" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("", encoding="utf-8")
    table = {"chrome.exe": [str(chrome)]}

    monkeypatch.setattr(appfind, "load", lambda force=False: table)
    monkeypatch.setattr(appfind, "clear", lambda: None)
    monkeypatch.setattr(icons, "available", lambda: False)
    monkeypatch.setattr(shortcut, "install", lambda p, **kw: [])

    class E:
        pass

    e = E()
    e.client = TestClient(api.app)
    e.chrome = str(chrome)
    return e


def test_settings_reports_preset_state(env):
    st = env.client.get("/api/settings").json()
    ps = st["preset"]
    assert ps["total"] == len(preset.wanted())
    assert "missing" in ps


def test_preset_restore_creates_them(env):
    r = env.client.post("/api/preset/restore")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["made"]) == len(preset.wanted())

    store = ProfileStore().load()
    for item in preset.wanted():
        assert store.get(item["key"]) is not None


def test_delete_preset_records_removal(env):
    env.client.post("/api/preset/restore")
    store = ProfileStore().load()
    assert store.get("telegram") is not None

    r = env.client.delete("/api/profile/telegram")
    assert r.status_code == 200

    again = ProfileStore().load()
    assert again.get("telegram") is None
    assert "telegram" in again.preset_removed


def test_restore_after_delete_works_and_clears_record(env):
    env.client.post("/api/preset/restore")
    env.client.delete("/api/profile/telegram")
    assert "telegram" in ProfileStore().load().preset_removed

    r = env.client.post("/api/preset/restore")
    assert r.status_code == 200
    assert any(x["key"] == "telegram" for x in r.json()["made"])

    again = ProfileStore().load()
    assert again.get("telegram") is not None
    assert again.preset_removed == []


def test_set_line_accepts_default_marker(env):
    """`@default` 是内部值，接口不许拒。"""
    env.client.post("/api/preset/restore")
    r = env.client.post("/api/profile/telegram/line", json={"line_id": "@default"})
    assert r.status_code == 200
    assert ProfileStore().load().get("telegram").line_id == "@default"


def test_set_line_rejects_unknown_line(env):
    env.client.post("/api/preset/restore")
    r = env.client.post("/api/profile/telegram/line", json={"line_id": "ghost"})
    assert r.status_code == 400


def test_preset_profile_resolves_default_line_after_import(env):
    """导入线路后，预置档案自动能用上，不用挨个改。"""
    reg = Registry().load()
    reg.upsert(Line(id="only", name="唯一", http_port=17011, socks_port=17012))
    reg.save()

    env.client.post("/api/preset/restore")

    snap = api._reg()
    store = ProfileStore().load()
    line = snap.resolve(store.get("telegram").line_id)
    assert line is not None and line.id == "only"

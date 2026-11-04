# -*- coding: utf-8 -*-
"""「添加软件」这条链路的接口测试。

重点钉两件事：
  1. 本机装了 -> 一键建桌面版档案；只有网页版 -> 一键建专属浏览器档案；
  2. 没装且没网页版 -> 老老实实报错并给官网地址，**不能假装成功**。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from appproxy import appfind, icons, shortcut
from appproxy.profiles import ProfileStore
from appproxy.webui import api


@pytest.fixture()
def env(tmp_home, tmp_path, monkeypatch):
    """本机只装了 Telegram 桌面版和 Chrome，其余当作没装。"""
    tg = tmp_path / "bin" / "Telegram.exe"
    tg.parent.mkdir(parents=True)
    tg.write_text("", encoding="utf-8")
    chrome = tmp_path / "bin" / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    table = {"telegram.exe": [str(tg)], "chrome.exe": [str(chrome)]}

    monkeypatch.setattr(appfind, "load", lambda force=False: table)
    monkeypatch.setattr(appfind, "clear", lambda: None)
    # 测试环境当作没装任何商店版（注册表是机器真实状态，必须隔离）
    monkeypatch.setattr(appfind, "store_installed", lambda family: False)
    # 测试不写桌面、不画图标
    monkeypatch.setattr(icons, "available", lambda: False)
    monkeypatch.setattr(shortcut, "install", lambda p, **kw: [])

    class E:
        pass

    e = E()
    e.client = TestClient(api.app)
    e.tg = str(tg)
    e.chrome = str(chrome)
    return e


def _profiles() -> list:
    return ProfileStore().load().profiles


def test_apps_lists_catalog_with_stats(env):
    r = env.client.get("/api/apps")
    assert r.status_code == 200
    d = r.json()
    assert len(d["suggest"]) >= 60
    assert d["stats"]["found"] == 2          # telegram + chrome
    assert d["browser"] == env.chrome
    rows = {x["key"]: x for x in d["suggest"]}
    assert rows["telegram"]["kind"] == "exe"
    assert rows["facebook"]["kind"] == "web"


def test_add_installed_app_creates_desktop_profile(env):
    r = env.client.post("/api/apps/add", json={"key": "telegram"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "exe"
    profs = _profiles()
    assert len(profs) == 1
    assert Path(profs[0].exe) == Path(env.tg)
    assert profs[0].url == "https://web.telegram.org/"   # 桌面版也记着网页入口


def test_add_same_app_twice_does_not_duplicate(env):
    env.client.post("/api/apps/add", json={"key": "telegram"})
    r = env.client.post("/api/apps/add", json={"key": "telegram"})
    assert r.status_code == 200
    assert "已经加过" in r.json()["message"]
    assert len(_profiles()) == 1


def test_add_same_app_to_other_line_is_allowed(env):
    """同一个软件走两条线路，是这产品的核心用法，不能拦。"""
    env.client.post("/api/apps/add", json={"key": "telegram", "line_id": "alpha"})
    env.client.post("/api/apps/add", json={"key": "telegram", "line_id": "beta"})
    profs = _profiles()
    assert len(profs) == 2
    assert {p.line_id for p in profs} == {"alpha", "beta"}


def test_add_web_only_entry_uses_browser(env):
    r = env.client.post("/api/apps/add", json={"key": "facebook"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "web"
    p = _profiles()[0]
    assert p.exe == env.chrome
    assert p.url == "https://www.facebook.com/"
    assert p.resolved_kind == "chromium"


def test_web_profiles_do_not_block_real_browser_profile(env):
    """网页版档案的 exe 也是 chrome.exe，但不能因此把「加 Chrome」判成重复。"""
    env.client.post("/api/apps/add", json={"key": "facebook"})
    r = env.client.post("/api/apps/add", json={"key": "chrome"})
    assert r.status_code == 200
    assert "已经加过" not in r.json()["message"]
    assert len(_profiles()) == 2


def test_add_missing_desktop_app_is_refused_with_download(env):
    """WhatsApp 有桌面版但本机没装 —— 要报错并给官网，不能默默开网页。"""
    r = env.client.post("/api/apps/add", json={"key": "whatsapp"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "没找到 WhatsApp" in detail
    assert "https://www.whatsapp.com/download" in detail
    assert not _profiles()


def test_add_force_web_works_even_if_desktop_missing(env):
    r = env.client.post("/api/apps/add", json={"key": "whatsapp", "web": True})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "web"
    assert _profiles()[0].url == "https://web.whatsapp.com/"


def test_add_unknown_key_404(env):
    r = env.client.post("/api/apps/add", json={"key": "根本没有这个软件"})
    assert r.status_code == 404


def test_add_by_raw_exe_still_works(env):
    r = env.client.post("/api/apps/add", json={"exe": env.chrome, "name": "工作用 Chrome"})
    assert r.status_code == 200
    assert _profiles()[0].name == "工作用 Chrome"


def test_add_batch_reports_each_kind(env):
    r = env.client.post("/api/apps/add-batch",
                        json={"keys": ["telegram", "facebook", "whatsapp", "chrome"]})
    assert r.status_code == 200
    d = r.json()
    assert set(d["added"]) == {"telegram", "facebook", "chrome"}
    assert any(f["key"] == "whatsapp" for f in d["failed"])
    assert len(_profiles()) == 3


def test_open_url_rejects_non_http(env, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(api.os, "startfile", lambda u: called.append(u), raising=False)
    assert env.client.post("/api/open-url", json={"url": "file:///C:/windows"}).status_code == 400
    assert env.client.post("/api/open-url", json={"url": "calc.exe"}).status_code == 400
    r = env.client.post("/api/open-url", json={"url": "https://telegram.org/dl/desktop/win64"})
    assert r.status_code == 200
    assert called == ["https://telegram.org/dl/desktop/win64"]


def test_add_store_app_creates_store_profile(env, monkeypatch):
    """本机只装了商店版 WhatsApp —— 一键建档成 shell: 激活，不再报「去官网装」。"""
    monkeypatch.setattr(appfind, "store_installed", lambda family: (
        family == "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"))
    r = env.client.post("/api/apps/add", json={"key": "whatsapp"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "store"
    profs = _profiles()
    assert len(profs) == 1
    assert profs[0].exe == ("shell:AppsFolder\\"
                            "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App")
    assert profs[0].kind == "store"
    assert profs[0].watch == "whatsapp.root.exe"
    assert profs[0].url == "https://web.whatsapp.com/"   # 网页版入口保留


def test_add_store_app_dup_not_blocked_by_web_profile(env, monkeypatch):
    """同一线路已有网页版档案时，商店版仍能单独添加（按 exe 判重，不按网址）。"""
    r = env.client.post("/api/apps/add", json={"key": "whatsapp", "web": True})
    assert r.status_code == 200
    monkeypatch.setattr(appfind, "store_installed", lambda family: (
        family == "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"))
    r2 = env.client.post("/api/apps/add", json={"key": "whatsapp"})
    assert r2.status_code == 200
    assert r2.json()["kind"] == "store"
    assert len(_profiles()) == 2


def test_add_store_app_creates_store_profile(env, monkeypatch):
    """本机只装了商店版 WhatsApp —— 一键建档成 shell: 激活，不再报「去官网装」。"""
    monkeypatch.setattr(appfind, "store_installed", lambda family: (
        family == "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"))
    r = env.client.post("/api/apps/add", json={"key": "whatsapp"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "store"
    profs = _profiles()
    assert len(profs) == 1
    assert profs[0].exe == ("shell:AppsFolder\\"
                            "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App")
    assert profs[0].kind == "store"
    assert profs[0].watch == "whatsapp.root.exe"
    assert profs[0].url == "https://web.whatsapp.com/"   # 网页版入口保留


def test_add_store_app_dup_not_blocked_by_web_profile(env, monkeypatch):
    """同一线路已有网页版档案时，商店版仍能单独添加（按 exe 判重，不按网址）。"""
    r = env.client.post("/api/apps/add", json={"key": "whatsapp", "web": True})
    assert r.status_code == 200
    monkeypatch.setattr(appfind, "store_installed", lambda family: (
        family == "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"))
    r2 = env.client.post("/api/apps/add", json={"key": "whatsapp"})
    assert r2.status_code == 200
    assert r2.json()["kind"] == "store"
    assert len(_profiles()) == 2

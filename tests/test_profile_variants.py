# -*- coding: utf-8 -*-
"""同一个档案两种开法：启动网页版 / 启动客户端。

重点钉：
  1. 网页版档案（exe=浏览器 + url）plan variant=web 仍是浏览器命令；
  2. variant=client 会去找桌面客户端：没装就 LookupError（话里带官网地址），
     装了就换成客户端 exe、按客户端的写法做数据隔离；
  3. 桌面版档案两个 variant 都等于原样启动，不能被改坏；
  4. API 端点把 LookupError / ValueError 变成 400，前端 toast 能把话说全。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from appproxy import launcher, profiles
from appproxy.models import Line
from appproxy.profiles import AppProfile, ProfileStore, plan
from appproxy.webui import api


def _web_profile(tmp_path) -> AppProfile:
    return AppProfile(
        id="whatsapp", name="WhatsApp",
        exe=str(tmp_path / "bin" / "chrome.exe"),
        line_id="", url="https://web.whatsapp.com/",
    )


def _line(port: int = 15956) -> Line:
    return Line(id="wa", name="WhatsApp·固定快速", http_port=port)


# --------------------------------------------------------------------------- plan

def test_plan_web_variant_keeps_browser(tmp_path):
    p = plan(_web_profile(tmp_path), _line(), variant="web")
    assert p.argv[0].endswith("chrome.exe")
    assert "https://web.whatsapp.com/" in p.argv
    assert "--proxy-server=http://127.0.0.1:15956" in p.argv
    assert p.iso_mode == "user-data-dir"


def test_plan_web_variant_without_url_raises(tmp_path):
    prof = AppProfile(id="tg", name="Telegram",
                      exe=str(tmp_path / "bin" / "telegram.exe"))
    with pytest.raises(ValueError, match="网页版"):
        plan(prof, None, variant="web")


def test_plan_client_missing_raises_with_download(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "find_client_exe", lambda p, table=None: "")
    prof = _web_profile(tmp_path)
    with pytest.raises(LookupError) as ei:
        plan(prof, _line(), variant="client")
    msg = str(ei.value)
    assert "客户端" in msg
    assert "https://www.whatsapp.com/download" in msg   # 官网地址要说全


def test_plan_client_found_uses_client_exe_and_isolation(tmp_path, monkeypatch):
    wa = tmp_path / "bin" / "WhatsApp.exe"
    wa.parent.mkdir(parents=True)
    wa.write_text("", encoding="utf-8")
    monkeypatch.setattr(profiles, "find_client_exe", lambda p, table=None: str(wa))
    p = plan(_web_profile(tmp_path), _line(), variant="client")
    assert p.argv[0] == str(wa)
    assert "https://web.whatsapp.com/" not in p.argv     # 客户端不带网址
    assert p.kind == "generic"
    # WhatsApp 是 Electron：沿用 --user-data-dir 写法，隔离照做
    assert any(a.startswith("--user-data-dir=") for a in p.argv)
    assert p.env["HTTP_PROXY"] == "http://127.0.0.1:15956"


def test_plan_client_on_desktop_profile_is_noop(tmp_path):
    prof = AppProfile(id="tg", name="Telegram",
                      exe=str(tmp_path / "bin" / "telegram.exe"))
    a = plan(prof, _line(), variant="auto")
    b = plan(prof, _line(), variant="client")
    assert a.argv == b.argv


def test_plan_client_remembers_pinned_path(tmp_path):
    profiles.client_cache_clear()   # 全套跑时别吃别的测试留下的缓存
    wa = tmp_path / "bin" / "WhatsApp.exe"
    wa.parent.mkdir(parents=True)
    wa.write_text("", encoding="utf-8")
    prof = _web_profile(tmp_path)
    prof.client_exe = str(wa)
    monkey_found = profiles.find_client_exe(prof)
    assert monkey_found == str(wa)   # 档案里记下的路径优先，不用翻索引


# --------------------------------------------------------------------------- API

@pytest.fixture()
def env(tmp_home, tmp_path, monkeypatch):
    chrome = tmp_path / "bin" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("", encoding="utf-8")

    class E:
        pass

    e = E()
    e.chrome = str(chrome)
    e.client = TestClient(api.app)
    store = ProfileStore().load()
    store.upsert(AppProfile(id="whatsapp", name="WhatsApp",
                            exe=str(chrome), url="https://web.whatsapp.com/"))
    store.save()
    return e


def test_api_start_rejects_bad_variant(env):
    r = env.client.post("/api/profile/whatsapp/start?variant=fast")
    assert r.status_code == 400


def test_api_start_client_missing_returns_400_with_download(env, monkeypatch):
    monkeypatch.setattr(profiles, "find_client_exe", lambda p, table=None: "")
    monkeypatch.setattr(profiles, "_CLIENT_CACHE", {})  # 别吃别的测试的缓存
    r = env.client.post("/api/profile/whatsapp/start?variant=client")
    assert r.status_code == 400
    assert "whatsapp.com/download" in r.json()["detail"]


def test_api_start_web_launches(env, monkeypatch):
    calls: list[tuple] = []

    def fake_launch(prof, reg, *, dry_run=False, no_core=False, variant="auto"):
        calls.append((prof.id, variant))
        return 0

    monkeypatch.setattr(launcher, "launch", fake_launch)
    r = env.client.post("/api/profile/whatsapp/start?variant=web")
    assert r.status_code == 200
    assert calls == [("whatsapp", "web")]
    assert "网页版" in r.json()["message"]


def test_api_start_default_still_auto(env, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(launcher, "launch",
                        lambda prof, reg, **kw: calls.append((prof.id, kw.get("variant"))) or 0)
    r = env.client.post("/api/profile/whatsapp/start")
    assert r.status_code == 200
    assert calls == [("whatsapp", "auto")]

# -*- coding: utf-8 -*-
"""界面语言：/api/lang 端点 + 托盘侧 i18n.tr 的精确/模式翻译。"""
from __future__ import annotations

import json

import pytest

from appproxy import i18n, settings


@pytest.fixture()
def lang_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPPROXY_HOME", str(tmp_path))
    settings.set_ui_lang("zh")   # 落一个初始文件
    yield tmp_path


def test_settings_roundtrip(lang_home):
    assert settings.ui_lang() == "zh"
    settings.set_ui_lang("en")
    assert settings.ui_lang() == "en"
    ui = lang_home / "data" / "ui.json"
    assert json.loads(ui.read_text(encoding="utf-8"))["lang"] == "en"


def test_settings_rejects_unknown(lang_home):
    with pytest.raises(ValueError):
        settings.set_ui_lang("fr")


def test_tr_zh_is_identity(lang_home):
    assert i18n.tr("退出") == "退出"


def test_tr_en_exact(lang_home):
    settings.set_ui_lang("en")
    assert i18n.tr("退出") == "Quit"
    assert i18n.tr("分应用代理管家") == "AppProxy"


def test_tr_en_patterns_fill_tokens_in_order(lang_home):
    settings.set_ui_lang("en")
    assert i18n.tr("全部运行中（2/3）") == "All running (2/3)"
    assert i18n.tr("部分运行中（0/5）") == "Partly running (0/5)"
    assert i18n.tr("3 个软件已启动") == "3 apps started"
    assert i18n.tr("系统代理已开启（127.0.0.1:7890）") == "System proxy on (127.0.0.1:7890)"
    assert i18n.tr("删掉了吧") == "删掉了吧"            # 查不到 → 回退中文


def test_api_lang_roundtrip(lang_home):
    from fastapi.testclient import TestClient

    from appproxy.webui import api
    c = TestClient(api.app)
    assert c.get("/api/lang").json()["lang"] == "zh"
    r = c.post("/api/lang", json={"lang": "en"})
    assert r.status_code == 200 and r.json()["lang"] == "en"
    assert c.get("/api/lang").json()["lang"] == "en"
    r = c.post("/api/lang", json={"lang": "fr"})
    assert r.status_code == 400


def test_webui_carries_tr_text_helper(lang_home):
    """引擎必须在页面里：trText、EN_DICT、切换按钮、boot 绑定一个不能少。"""
    html = (lang_home.parent.parent / "nonexistent").exists() if False else True
    from pathlib import Path
    page = Path(__file__).resolve().parents[1] / "appproxy" / "webui" / "static" / "index.html"
    s = page.read_text(encoding="utf-8")
    for needle in ("function trText", "EN_DICT", 'id="btn-lang"',
                   "setLang(I18N_LANG === \"zh\" ? \"en\" : \"zh\")",
                   "trText(msg)", "/api/lang"):
        assert needle in s, needle

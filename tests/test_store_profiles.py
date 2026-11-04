# -*- coding: utf-8 -*-
"""微软商店版应用（kind=store）的建档 / 启动计划 / 探活。"""
from __future__ import annotations

import pytest

from appproxy import appfind
from appproxy.models import Line
from appproxy.procwatch import Snapshot
from appproxy.profiles import AppProfile, plan

SHELL_URI = "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"


def make_profile(**kw) -> AppProfile:
    base = dict(id="whatsapp", name="WhatsApp", exe=SHELL_URI,
                kind="store", watch="whatsapp.root.exe",
                url="https://web.whatsapp.com/", line_id="wa")
    base.update(kw)
    return AppProfile(**base)


def make_line(port: int = 15956) -> Line:
    return Line(id="wa", name="WhatsApp·固定快速", http_port=port,
                socks_port=port + 1, mode="single", node_key="x" * 12,
                engine="singbox")


# ------------------------------------------------------------------- 建模
def test_store_kind_accepted() -> None:
    p = make_profile()
    assert p.kind == "store"
    assert p.resolved_kind == "store"
    assert p.exists()            # shell: URI 不当文件路径检查


def test_store_kind_invalid_rejected() -> None:
    with pytest.raises(ValueError):
        AppProfile(id="x", name="X", exe="C:\\a\\b.exe", kind=" nonsense ")


# ------------------------------------------------------------------- 启动计划
def test_store_plan_uses_shell_activation() -> None:
    p = plan(make_profile(), make_line())
    assert p.argv == [SHELL_URI]          # 不带任何 --proxy-server
    assert p.kind == "store"
    assert p.env == {}                    # 商店版不靠环境变量传代理
    assert p.iso_mode == "none"


def test_store_plan_web_variant_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """商店版档案点「启动网页版」要能落回浏览器 + 代理参数。"""
    monkeypatch.setattr("appproxy.profiles.detect_kind", lambda exe: "chromium")
    prof = make_profile()
    prof.args = []
    p = plan(prof, make_line(), variant="web")
    assert p.argv[0].endswith("chrome.exe") or p.argv[0].endswith("msedge.exe")
    assert any(a.startswith("--proxy-server=") for a in p.argv)


def test_chromium_app_window_url_not_duplicated() -> None:
    """--app=<url> 已在参数里时，不要再追加一遍网址（会多开一个标签页）。"""
    prof = AppProfile(id="msgr", name="Messenger",
                      exe="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
                      kind="chromium", args=["--app=https://www.messenger.com/"],
                      url="https://www.messenger.com/", line_id="")
    p = plan(prof, None)
    assert p.argv.count("https://www.messenger.com/") == 0  # 只在 --app= 里出现
    assert "--app=https://www.messenger.com/" in p.argv


# ------------------------------------------------------------------- 探活
def test_procwatch_matches_by_watch_name() -> None:
    snap = Snapshot()
    snap.by_name["whatsapp.root.exe"].append(4321)
    prof = make_profile()
    assert snap.alive(prof)
    assert snap.pids(prof) == [4321]


def test_procwatch_watch_name_without_exe_suffix() -> None:
    snap = Snapshot()
    snap.by_name["whatsapp.root.exe"].append(7)
    prof = make_profile(watch="whatsapp.root")     # 忘写 .exe 也能匹配
    assert snap.pids(prof) == [7]


def test_procwatch_no_match_returns_empty() -> None:
    snap = Snapshot()
    assert snap.pids(make_profile()) == []


# ------------------------------------------------------------------- 商店包检测
def test_store_installed_false_for_unknown_family() -> None:
    assert appfind.store_installed("") is False
    assert appfind.store_installed("NoSuch.Package_abc123") is False

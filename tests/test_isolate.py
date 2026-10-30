# -*- coding: utf-8 -*-
"""同软件多开 = 每个档案一份独立用户数据。

这块最容易「看着对、实际没隔离」：参数拼错一个字符，程序照常启动，
只是悄悄用了系统那份配置 —— 用户会以为多开成功了，其实两个窗口是同一份数据。
所以每条都断言到具体参数。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from appproxy import profiles, settings
from appproxy.models import Line

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
TELEGRAM = r"C:\Users\me\AppData\Roaming\Telegram Desktop\Telegram.exe"


@pytest.fixture()
def line(tmp_home):
    settings.ensure_dirs()
    return Line(id="alpha", name="alpha", http_port=17111, socks_port=17112)


def _prof(pid="a", exe=CHROME, **kw) -> profiles.AppProfile:
    return profiles.AppProfile(id=pid, name=kw.pop("name", pid), exe=exe, **kw)


# --------------------------------------------------------------------------- 能力判定
def test_chromium_family_is_always_isolatable():
    for exe in ("chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe"):
        iso = profiles.isolation(exe)
        assert iso["can"] is True, exe
        assert iso["mode"] == "user-data-dir"


def test_known_native_programs_use_their_own_flag():
    tg = profiles.isolation(TELEGRAM)
    assert tg["can"] is True and tg["mode"] == "native"
    assert tg["tpl"][0] == "-workdir"

    ff = profiles.isolation(r"C:\Program Files\Mozilla Firefox\firefox.exe")
    # 少了 -no-remote，第二个实例只会把第一个窗口顶到前面，等于没多开
    assert "-no-remote" in ff["tpl"]

    dc = profiles.isolation(r"C:\Users\me\AppData\Local\Discord\app-1.0\Discord.exe")
    assert dc["mode"] == "native"


def test_unknown_program_is_honest_about_it():
    iso = profiles.isolation(r"C:\Program Files (x86)\Steam\steam.exe")
    assert iso["can"] is False
    assert iso["mode"] == "none"
    assert "共用" in iso["note"]


# --------------------------------------------------------------------------- 启动命令
def test_chromium_gets_exactly_one_user_data_dir(line):
    p = profiles.plan(_prof(), line)
    hits = [a for a in p.argv if a.startswith("--user-data-dir=")]
    assert len(hits) == 1, f"重复传会互相覆盖：{hits}"
    assert hits[0].endswith("a\\data") or hits[0].endswith("a/data")
    assert p.iso_mode == "user-data-dir"


def test_native_program_gets_workdir(line):
    p = profiles.plan(_prof("tg", TELEGRAM), line)
    assert "-workdir" in p.argv
    idx = p.argv.index("-workdir")
    assert p.argv[idx + 1].endswith("tg") or "tg" in p.argv[idx + 1]
    assert p.iso_mode == "native"
    # 顺便确认代理还是照旧给到环境变量
    assert p.env["HTTPS_PROXY"] == "http://127.0.0.1:17111"


def test_unsupported_program_gets_no_made_up_args(line):
    """表里没有的程序，绝不能瞎塞参数 —— 起不来比不隔离更糟。"""
    p = profiles.plan(_prof("steam", r"C:\Steam\steam.exe"), line)
    assert not any("user-data-dir" in a or "-workdir" in a for a in p.argv)
    assert p.iso_mode == "none"


def test_isolate_off_disables_chromium_isolation(line):
    p = profiles.plan(_prof(isolate="off"), line)
    assert not any(a.startswith("--user-data-dir=") for a in p.argv)
    assert p.iso_mode == "none"
    assert "关闭" in p.iso_note


def test_isolate_full_falls_back_to_env_for_stubborn_programs(line):
    """程序不认参数时，强行隔离只能掰 APPDATA —— 而且不能藏着不说。"""
    p = profiles.plan(_prof("steam", r"C:\Steam\steam.exe", isolate="full"), line)
    assert p.iso_mode == "env"
    assert p.env["APPDATA"].endswith("Roaming")
    assert p.env["LOCALAPPDATA"].endswith("Local")
    assert "不保证" in p.iso_note


def test_two_profiles_of_same_app_get_different_data(line):
    """这就是「多占磁盘换全面隔离」的落点：两个档案两份数据。"""
    a = profiles.plan(_prof("chrome-a"), line)
    b = profiles.plan(_prof("chrome-b"), line)
    dir_a = next(x for x in a.argv if x.startswith("--user-data-dir="))
    dir_b = next(x for x in b.argv if x.startswith("--user-data-dir="))
    assert dir_a != dir_b


def test_custom_data_dir_wins(tmp_home):
    custom = tmp_home / "my-own-data"
    p = profiles.plan(_prof(data_dir=str(custom)))
    assert f"--user-data-dir={custom}" in p.argv


# --------------------------------------------------------------------------- 兼容
def test_old_profiles_json_without_isolate_still_loads(tmp_home):
    """v0.6.5 之前的档案没有 isolate 字段，不能因此读不出来。"""
    settings.ensure_dirs()
    path = settings.profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"version":1,"profiles":[{"id":"old","name":"旧档案","exe":"%s"}]}'
        % CHROME.replace("\\", "\\\\"), encoding="utf-8")
    store = profiles.ProfileStore().load()
    assert store.get("old") is not None
    assert store.get("old").isolate == "auto"


def test_bad_isolate_value_falls_back_to_auto():
    assert _prof(isolate="胡说").isolate == "auto"

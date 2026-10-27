# -*- coding: utf-8 -*-
"""步骤 2：应用档案（启动参数 / 存储 / 图标 / 快捷方式）的离线单测。

不启动任何真实程序，也不联网。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from appproxy import icons, profiles, shortcut, settings
from appproxy.models import Line

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


@pytest.fixture()
def store(tmp_home):
    settings.ensure_dirs()
    return profiles.ProfileStore().load()


def _line(port=10801, lid="alpha"):
    return Line(id=lid, name=lid, http_port=port, socks_port=port + 1, engine="singbox")


def _prof(**kw):
    base = dict(id="chrome-work", name="Chrome工作", exe=CHROME, line_id="alpha")
    base.update(kw)
    return profiles.AppProfile(**base)


# --------------------------------------------------------------------------- 类型识别
@pytest.mark.parametrize("exe,kind", [
    (CHROME, "chromium"),
    (r"C:\x\msedge.exe", "chromium"),
    (r"C:\x\Thorium.exe", "chromium"),
    (r"C:\x\notepad.exe", "generic"),
    (r"C:\x\Telegram.exe", "generic"),
])
def test_detect_kind(exe, kind):
    assert profiles.detect_kind(exe) == kind


def test_slug_sanitizes():
    assert profiles.slug("Chrome 工作/1") == "chrome-1"
    assert profiles.slug("___") == "profile"


def test_bad_kind_rejected():
    with pytest.raises(ValueError):
        _prof(kind="weird")


# --------------------------------------------------------------------------- 启动参数
def test_chromium_plan_has_isolated_dir_and_proxy():
    p = _prof()
    plan = profiles.plan(p, _line())
    joined = " ".join(plan.argv)
    assert f"--user-data-dir={p.data_path()}" in joined
    assert "--proxy-server=http://127.0.0.1:10801" in plan.argv
    assert "--no-first-run" in plan.argv
    assert plan.kind == "chromium" and plan.proxy.endswith("10801")


def test_chromium_direct_profile_forbids_proxy():
    p = _prof(line_id="")
    plan = profiles.plan(p, None)
    assert "--no-proxy-server" in plan.argv
    assert not any(a.startswith("--proxy-server") for a in plan.argv)
    assert p.is_direct and p.short_code() == "D"


def test_chromium_url_appended_last_and_extra_args_kept():
    p = _prof(url="https://example.com", args=["--start-maximized"])
    argv = profiles.plan(p, _line()).argv
    assert argv[-1] == "https://example.com"
    assert "--start-maximized" in argv


def test_generic_plan_uses_env_proxy():
    p = _prof(exe=r"C:\x\Telegram.exe")
    plan = profiles.plan(p, _line(10803))
    assert plan.kind == "generic"
    assert plan.env["HTTPS_PROXY"] == "http://127.0.0.1:10803"
    assert plan.env["ALL_PROXY"] == "http://127.0.0.1:10803"
    assert not any(a.startswith("--proxy-server") for a in plan.argv)


def test_generic_direct_clears_env():
    p = _prof(exe=r"C:\x\Telegram.exe", line_id="")
    plan = profiles.plan(p, None)
    assert plan.env["HTTP_PROXY"] == ""
    assert plan.proxy == ""


def test_two_profiles_same_exe_get_different_args():
    """同一个 exe 两个档案：数据目录不同、端口不同 —— 才能各走各的代理。"""
    a = profiles.plan(_prof(id="c-alpha", line_id="alpha"), _line(10801, "alpha")).argv
    b = profiles.plan(_prof(id="c-beta", line_id="beta"), _line(10803, "beta")).argv
    assert a != b
    assert "--proxy-server=http://127.0.0.1:10803" in b
    assert any("c-alpha" in x for x in a) and any("c-beta" in x for x in b)


def test_custom_data_dir_respected(tmp_home):
    p = _prof(data_dir=r"D:\MyChrome")
    assert p.data_path() == Path(r"D:\MyChrome")


def test_default_data_dir_under_home(tmp_home):
    p = _prof(id="c1")
    assert p.data_path() == tmp_home / "apps" / "c1" / "data"


# --------------------------------------------------------------------------- 存储
def test_store_roundtrip(store):
    store.upsert(_prof())
    store.save()
    again = profiles.ProfileStore().load()
    got = again.require("chrome-work")
    assert got.name == "Chrome工作" and got.line_id == "alpha"
    assert got.args == [] and got.created > 0


def test_store_unique_id(store):
    store.upsert(_prof(id="chrome"))
    assert store.unique_id("chrome") == "chrome-2"
    store.upsert(_prof(id="chrome-2"))
    assert store.unique_id("chrome") == "chrome-3"


def test_store_remove_and_by_line(store):
    store.upsert(_prof(id="a", line_id="alpha"))
    store.upsert(_prof(id="b", line_id="alpha"))
    store.upsert(_prof(id="c", line_id="beta"))
    assert [p.id for p in store.by_line("alpha")] == ["a", "b"]
    assert store.remove("a") and store.get("a") is None


def test_store_tolerates_broken_json(tmp_home):
    path = settings.profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert profiles.ProfileStore().load().profiles == []


def test_store_json_is_utf8(store, tmp_home):
    store.upsert(_prof())
    store.save()
    raw = json.loads(settings.profiles_path().read_text(encoding="utf-8"))
    assert raw["profiles"][0]["name"] == "Chrome工作"


# --------------------------------------------------------------------------- 图标
needs_pil = pytest.mark.skipif(not icons.available(), reason="没装 Pillow")


@needs_pil
def test_icon_render_creates_multi_size_ico(tmp_home):
    from PIL import Image
    p = _prof()
    path = icons.render(p)
    assert path.is_file() and path.suffix == ".ico"
    with Image.open(path) as im:
        sizes = im.info.get("sizes") or set()
    assert (256, 256) in sizes and (16, 16) in sizes


@needs_pil
def test_icon_badge_uses_line_color(tmp_path, tmp_home):
    from PIL import Image
    p = _prof(id="same", line_id="alpha")
    a = icons.render(p, tmp_path / "alpha.ico")
    b = icons.render(_prof(id="same", line_id="beta"), tmp_path / "beta.ico")
    with Image.open(a) as im:      # 256 那一档
        badge = im.convert("RGB").getpixel((194, 148))   # 角标内、文字上方一点
    want = icons._hex(icons._pick("alpha", skip=icons._pick(CHROME.lower())))
    assert badge == want
    # 换一条线路 -> 图标必须变（角标颜色或短码至少变一样）
    assert b.read_bytes() != a.read_bytes()


@needs_pil
def test_icon_glyph_prefers_cjk_name():
    assert icons._glyph(_prof(name="工作号")) == "工"
    assert icons._glyph(_prof(name="Chrome", exe=CHROME)) == "C"


# --------------------------------------------------------------------------- 快捷方式
def test_shortcut_install_and_remove(tmp_path, tmp_home, monkeypatch):
    desk = tmp_path / "Desktop"
    desk.mkdir()
    monkeypatch.setattr(settings, "desktop_dirs", lambda: [desk])
    p = _prof()
    made = shortcut.install(p)
    assert (settings.shortcuts_dir() / "Chrome工作.lnk").is_file()
    assert (desk / "Chrome工作.lnk").is_file()
    assert p.shortcut and Path(p.shortcut).is_file()
    assert len(made) == 2
    assert shortcut.remove(p) >= 2
    assert not (desk / "Chrome工作.lnk").exists()


def test_launcher_arguments_point_to_launcher(tmp_home):
    """快捷方式不再指向 launcher.py 这个脚本文件，而是走 `profile launch` 子命令。

    原因：打包成 AppProxy.exe 之后没有 Python 解释器、也没有 .py 文件可跑，
    写死脚本路径的快捷方式会全部点不开。两种形态由 selfrun 统一。
    """
    args = shortcut.launcher_arguments("chrome-work")
    assert "profile" in args and "launch" in args and "chrome-work" in args
    # 源码模式下必须显式带模块入口，否则 exe 以外的方式拉不起来
    assert "-m" in args and "appproxy.cli" in args
    assert shortcut.launcher_script().name == "launcher.py"
    assert shortcut.python_exe().name.lower().startswith("python")

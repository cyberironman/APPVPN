# -*- coding: utf-8 -*-
"""步骤 3：托盘 / 系统代理 / 开机自启 / 程序扫描 的离线单测。

不真改系统代理、不写真实启动目录、不起托盘。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from appproxy import appscan, autostart, settings, sysproxy, tray
from appproxy.models import Line
from appproxy.profiles import AppProfile, ProfileStore


@pytest.fixture()
def home(tmp_home):
    settings.ensure_dirs()
    return tmp_home


# --------------------------------------------------------------------------- 系统代理
def test_server_for_port():
    assert sysproxy.server_for(10801) == "127.0.0.1:10801"


def test_matches_port():
    st = {"enable": True, "server": "127.0.0.1:10801"}
    assert sysproxy.matches_port(st, 10801)
    assert not sysproxy.matches_port(st, 10803)
    assert not sysproxy.matches_port({"enable": False, "server": "127.0.0.1:10801"}, 10801)


def test_parse_server_both_formats():
    assert sysproxy.parse_server("127.0.0.1:10801") == ["127.0.0.1:10801"]
    assert sysproxy.parse_server("http=127.0.0.1:10888;https=127.0.0.1:10888") == \
        ["127.0.0.1:10888"]
    assert sysproxy.parse_server("http=1.2.3.4:80;socks=5.6.7.8:1080") == \
        ["1.2.3.4:80", "5.6.7.8:1080"]
    assert sysproxy.parse_server("") == []


def test_matches_port_against_per_protocol_form():
    """v2rayN 那种 http=…;https=… 写法也要能认出来。"""
    st = {"enable": True, "server": "http=127.0.0.1:10801;https=127.0.0.1:10801"}
    assert sysproxy.matches_port(st, 10801)


def test_describe():
    assert "10801" in sysproxy.describe({"enable": True, "server": "127.0.0.1:10801"})
    assert sysproxy.describe({"enable": False, "server": ""}) == "已关闭"


def test_backup_roundtrip(home):
    assert sysproxy.load_backup() is None
    sysproxy.save_backup({"enable": True, "server": "10.0.0.1:8888", "override": ""})
    again = sysproxy.load_backup()
    assert again["server"] == "10.0.0.1:8888"
    # 已有备份时不覆盖（避免把"原始设置"冲掉）
    sysproxy.save_backup({"enable": False, "server": ""})
    assert sysproxy.load_backup()["server"] == "10.0.0.1:8888"
    sysproxy.drop_backup()
    assert sysproxy.load_backup() is None


# --------------------------------------------------------------------------- 开机自启
def test_autostart_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert autostart.startup_dir() == tmp_path / "Microsoft/Windows/Start Menu/Programs/Startup"
    assert autostart.target_lnk().name == autostart.LNK_NAME
    assert autostart.tray_script().name == "tray.py"


def test_autostart_toggle(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    autostart.startup_dir().mkdir(parents=True, exist_ok=True)
    assert not autostart.enabled()
    assert autostart.enable().is_file()
    assert autostart.enabled()
    assert autostart.toggle() is False          # 再切一次 -> 关闭
    assert not autostart.enabled()
    assert autostart.status()["enabled"] is False


# --------------------------------------------------------------------------- 程序扫描
def _fake_dirs(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    return [tmp_path]


def test_scan_filters_and_dedupes(tmp_path):
    lnks = {"Chrome.lnk": r"C:\x\chrome.exe", "Chrome副本.lnk": r"C:\x\chrome.exe",
            "卸载 Chrome.lnk": r"C:\x\unins000.exe",
            "Telegram.lnk": r"C:\y\Telegram.exe",
            "帮助文档.lnk": r"C:\y\readme.exe",
            "坏链接.lnk": None}
    for name in lnks:
        (tmp_path / name).write_bytes(b"")
    items = appscan.scan(resolver=lambda p: lnks.get(p.name), dirs=[tmp_path])
    names = [i["name"] for i in items]
    assert "Chrome" in names and "Chrome副本" not in names
    assert "卸载 Chrome" not in names and "帮助文档" not in names and "坏链接" not in names
    assert {i["group"] for i in items} <= set(appscan.GROUP_ORDER)


@pytest.mark.parametrize("name,exe,group", [
    ("Google Chrome", r"C:\x\chrome.exe", "浏览器"),
    ("微信", r"C:\x\WeChat.exe", "通讯"),
    ("Visual Studio Code", r"C:\x\Code.exe", "开发"),
    ("WPS Office", r"C:\x\wps.exe", "办公"),
    ("随便什么", r"C:\x\foo.exe", "工具"),
])
def test_guess_group(name, exe, group):
    assert appscan.guess_group(name, exe) == group


def test_grouped_limits_per_group():
    items = [{"name": f"n{i}", "exe": r"C:\x\chrome.exe", "group": "浏览器"}
             for i in range(20)]
    items += [{"name": "t", "exe": r"C:\y\t.exe", "group": "通讯"}]
    g = appscan.grouped(items, per_group=5)
    assert len(g["浏览器"]) == 5 and len(g["通讯"]) == 1


def test_cache_roundtrip(home):
    assert appscan.load_cache() == []
    appscan.save_cache([{"name": "A", "exe": r"C:\a.exe", "group": "工具"}])
    assert appscan.load_cache()[0]["name"] == "A"
    assert appscan.cached_or_scan() == appscan.load_cache()   # 有缓存就不重扫


# --------------------------------------------------------------------------- 托盘
def test_tray_image_size():
    img = tray.tray_image(tray.C_OK)
    assert img.size == (64, 64) and img.mode == "RGBA"


def test_save_tray_icon(home):
    from PIL import Image
    path = tray.save_tray_icon()
    assert path.is_file()
    with Image.open(path) as im:
        assert (64, 64) in (im.info.get("sizes") or set())


def test_menu_is_short_with_two_doors_to_settings(home, monkeypatch):
    """托盘菜单要「简洁 + 双渠道进设置」：顶层不超 7 项，界面与设置各一个入口。"""
    monkeypatch.setattr(tray.gui, "running_port", lambda: None)   # 假设界面没开
    store = ProfileStore().load()
    store.upsert(AppProfile(id="c1", name="Chrome·示例A",
                            exe=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                            line_id="alpha"))
    store.save()
    from appproxy.registry import Registry
    r = Registry().load()
    r.upsert(Line(id="alpha", name="alpha", http_port=10801))
    r.save()

    t = tray.Tray()
    t.line_up = lambda line: True           # 不真连端口
    menu = t.build_menu()
    import pystray
    labels = [i.text for i in menu.items if i is not pystray.Menu.SEPARATOR and i.text]

    assert len(labels) <= 7, f"托盘顶层太长了：{labels}"
    # 双渠道：一个进主界面，一个直达设置页
    assert "打开主界面" in labels
    assert any(x.startswith("设置") for x in labels)
    # 开关对象是「软件」，不是线路
    assert any("启动所有软件" in x for x in labels)
    assert any("关闭所有软件" in x for x in labels)
    assert "系统代理" in labels
    # 这些已经挪进主界面，不该再占托盘
    for gone in ("线路", "应用", "打开", "开机自启"):
        assert gone not in labels, f"{gone} 还留在托盘里"


def test_menu_without_data(home, monkeypatch):
    monkeypatch.setattr(tray.gui, "running_port", lambda: None)
    t = tray.Tray()
    menu = t.build_menu()
    assert menu is not None
    labels = [i.text for i in menu.items]
    assert any("添加线路" in (s.text or "") for i in menu.items if i.submenu
               for s in i.submenu.items)


def test_settings_item_flips_to_flash_hint_when_gui_open(home, monkeypatch):
    """界面开着时，「设置…」要自己变成「切到界面并闪烁提示」——
    这一项不再开新窗口，点它的结果是让已有窗口在任务栏闪。"""
    import pystray
    monkeypatch.setattr(tray.gui, "running_port", lambda: None)

    t = tray.Tray()
    menu = t.build_menu()
    labels = [i.text for i in menu.items if i is not pystray.Menu.SEPARATOR]
    assert "设置…" in labels
    assert "打开主界面" in labels

    # 界面开起来了：缓存作废后重新构建菜单，文案要跟着变
    monkeypatch.setattr(tray.gui, "running_port", lambda: 1443)
    t._gui_checked = 0.0
    menu2 = t.build_menu()
    labels2 = [i.text for i in menu2.items if i is not pystray.Menu.SEPARATOR]
    assert any("闪烁" in x for x in labels2), labels2
    assert "显示主界面（已在后台）" in labels2


def test_flash_window_without_window_returns_false():
    """找不到窗口就老实返回 False，绝不抛异常（托盘线程不能被这种事打断）。"""
    assert tray.gui.flash_window("这个标题不可能存在的窗口") is False

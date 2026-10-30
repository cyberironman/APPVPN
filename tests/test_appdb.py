# -*- coding: utf-8 -*-
"""常用软件清单与本机定位 —— 全离线。

这两个模块决定「点一下能不能加上」，错一个用户就看到「本机没找到」，
所以边界都要钉住：商店版要排除、网页版要能兜底、判重要看性质而不是 exe。
"""
from __future__ import annotations

from pathlib import Path

from appproxy import appdb, appfind


# --------------------------------------------------------------------------- 清单本身
def test_catalog_is_well_formed():
    items = appdb.entries()
    assert len(items) >= 60, "常用软件太少了，用户翻不到想加的那个"
    keys = [i.get("key") for i in items]
    assert all(keys), "每条都要有稳定标识 key"
    assert len(keys) == len(set(keys)), f"key 有重复：{len(keys) - len(set(keys))} 个"
    for it in items:
        assert it.get("name"), it
        assert it.get("group") in appdb.groups(), f"{it.get('name')} 的分组没在 groups 里声明"
        assert it.get("level") in ("must", "often"), it
        # 至少有一个入口：桌面版进程名，或者网页版地址
        assert it.get("exe") or it.get("web"), f"{it.get('name')} 既没有 exe 也没有 web"


def test_catalog_covers_the_usual_suspects():
    """用户点名要的那些必须在。少一个就是「怎么没有 Telegram」。"""
    keys = {i.get("key") for i in appdb.entries()}
    for must in ("telegram", "whatsapp", "messenger", "chrome", "facebook",
                 "discord", "youtube", "instagram", "x", "netflix", "spotify",
                 "chatgpt", "notion", "steam"):
        assert must in keys, f"清单缺 {must}"


def test_catalog_entries_have_official_download():
    """引导用户去装，就得给得出官网地址，不能让人自己搜。"""
    no_dl = [i["name"] for i in appdb.entries()
             if i.get("exe") and not i.get("download")]
    assert not no_dl, f"这些有桌面版却没写 download：{no_dl}"


def test_get_by_key_and_by_name():
    assert appdb.get("telegram")["name"] == "Telegram"
    assert appdb.get("TELEGRAM")["name"] == "Telegram"      # 大小写不敏感
    assert appdb.get("Telegram")["key"] == "telegram"       # 名字也能查
    assert appdb.get("根本没有这个") is None
    assert appdb.get("") is None


# --------------------------------------------------------------------------- 本机定位
def _tree(root: Path, files: list[str]) -> None:
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")


def test_scan_roots_finds_exe_by_name(tmp_path):
    _tree(tmp_path, [r"Telegram Desktop\Telegram.exe",
                     r"Google\Chrome\Application\chrome.exe",
                     r"SomeApp\readme.txt"])
    table = appfind.scan_roots([tmp_path])
    assert set(table) == {"telegram.exe", "chrome.exe"}


def test_scan_roots_skips_windowsapps(tmp_path):
    """商店版目录不枚举 —— 那目录本来也读不了，进去只会抛异常。"""
    _tree(tmp_path, [r"WindowsApps\Foo\foo.exe", r"Normal\bar.exe"])
    table = appfind.scan_roots([tmp_path])
    assert "foo.exe" not in table
    assert "bar.exe" in table


def test_usable_rejects_store_paths(tmp_path):
    store = tmp_path / "WindowsApps" / "Mozilla.Firefox_1.0_x64" / "firefox.exe"
    store.parent.mkdir(parents=True)
    store.write_text("", encoding="utf-8")
    normal = tmp_path / "Firefox" / "firefox.exe"
    normal.parent.mkdir(parents=True)
    normal.write_text("", encoding="utf-8")

    assert appfind.usable(str(store)) is False, "商店版不能用 CreateProcess 带参数启动"
    assert appfind.usable(str(normal)) is True
    assert appfind.is_store(str(store)) is True
    assert appfind.is_store(str(normal)) is False


def test_locate_uses_table_and_skips_store(tmp_path):
    normal = tmp_path / "Telegram Desktop" / "Telegram.exe"
    normal.parent.mkdir(parents=True)
    normal.write_text("", encoding="utf-8")
    store = tmp_path / "WindowsApps" / "T" / "telegram.exe"
    store.parent.mkdir(parents=True)
    store.write_text("", encoding="utf-8")

    # 索引里商店版排前面，但 locate 要跳过它拿到可用的那个
    table = {"telegram.exe": [str(store), str(normal)]}
    assert appfind.locate("telegram.exe", table) == str(normal)
    # 只有商店版时宁可返回 None，也不要给一个起不来的路径
    assert appfind.locate("telegram.exe", {"telegram.exe": [str(store)]}) is None
    # 直接给完整路径也走同一套过滤
    assert appfind.locate(str(normal), table) == str(normal)
    assert appfind.locate(str(store), table) is None


def test_locate_any_follows_order(tmp_path):
    a = tmp_path / "a" / "one.exe"
    b = tmp_path / "b" / "two.exe"
    for p in (a, b):
        p.parent.mkdir(parents=True)
        p.write_text("", encoding="utf-8")
    table = {"one.exe": [str(a)], "two.exe": [str(b)]}
    assert appfind.locate_any(["one.exe", "two.exe"], table) == str(a)
    assert appfind.locate_any(["nope.exe", "two.exe"], table) == str(b)
    assert appfind.locate_any([], table) is None


def test_store_only_reports_store_copy(tmp_path):
    store = tmp_path / "WindowsApps" / "W" / "whatsapp.exe"
    store.parent.mkdir(parents=True)
    store.write_text("", encoding="utf-8")
    assert appfind.store_only(["whatsapp.exe"], {"whatsapp.exe": [str(store)]}) == str(store)
    assert appfind.store_only(["whatsapp.exe"], {"whatsapp.exe": []}) is None


def test_browser_prefers_chrome(tmp_path):
    for name in ("msedge.exe", "chrome.exe", "firefox.exe"):
        p = tmp_path / name
        p.write_text("", encoding="utf-8")
    table = {n: [str(tmp_path / n)] for n in ("msedge.exe", "chrome.exe", "firefox.exe")}
    assert Path(appfind.browser(table)).name == "chrome.exe"


def test_index_roundtrip(tmp_home):
    """索引要能落盘再读回来（不然每次进界面都白扫一遍）。"""
    d = tmp_home / "appdata"
    _tree(d, [r"Foo\foo.exe"])
    appfind.build([d])
    assert appfind.cache_path().is_file()
    table = appfind.load()
    assert "foo.exe" in table
    assert appfind.stale() is False


# --------------------------------------------------------------------------- 推荐列表
def _table(**kv) -> dict[str, list[str]]:
    return {k: list(v) for k, v in kv.items()}


def test_suggest_marks_installed_as_ready(tmp_home, tmp_path):
    exe = tmp_path / "Telegram.exe"
    exe.write_text("", encoding="utf-8")
    rows = {r["key"]: r for r in appdb.suggest(table=_table(**{"telegram.exe": [str(exe)]}))}
    assert rows["telegram"]["found"] is True
    assert rows["telegram"]["kind"] == "exe"
    assert rows["telegram"]["exe"] == str(exe)


def test_suggest_web_only_entry_is_clickable(tmp_home, tmp_path):
    """Facebook 这种只有网页版的，也要能一键加（专属浏览器图标）。"""
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    rows = {r["key"]: r for r in appdb.suggest(table=_table(**{"chrome.exe": [str(chrome)]}))}
    fb = rows["facebook"]
    assert fb["kind"] == "web" and fb["can_web"] is True
    assert fb["web"].startswith("https://")
    assert rows["youtube"]["kind"] == "web"


def test_suggest_desktop_missing_does_not_silently_become_web(tmp_home, tmp_path):
    """有桌面版的软件没装时，kind 必须是空的 —— 界面才会显示「去官网安装」。

    如果这里返回 'web'，用户会以为装好了，其实只是开了个网页。
    """
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    rows = {r["key"]: r for r in appdb.suggest(table=_table(**{"chrome.exe": [str(chrome)]}))}
    tel = rows["telegram"]
    assert tel["found"] is False
    assert tel["kind"] == ""
    assert tel["can_web"] is True          # 但还是可以用网页版兜底
    assert tel["download"].startswith("https://telegram.org")


def test_suggest_no_browser_means_no_web(tmp_home):
    rows = {r["key"]: r for r in appdb.suggest(table={})}
    assert rows["facebook"]["kind"] == ""
    assert rows["facebook"]["can_web"] is False


def test_suggest_marks_already_added(tmp_home, tmp_path):
    exe = tmp_path / "Telegram.exe"
    exe.write_text("", encoding="utf-8")
    table = _table(**{"telegram.exe": [str(exe)], "chrome.exe": [str(tmp_path / "c.exe")]})
    rows = {r["key"]: r for r in appdb.suggest(
        have_exes={str(exe).lower()}, table=table,
        have_urls={"https://www.facebook.com"})}
    assert rows["telegram"]["have"] is True
    assert rows["facebook"]["have"] is True      # 末尾斜杠不影响判定
    assert rows["discord"]["have"] is False


def test_suggest_grouped_together(tmp_home, tmp_path):
    """同一个分组必须连续出现，否则界面会冒出好几个同名小标题。"""
    rows = appdb.suggest(table={})
    seen: list[str] = []
    for r in rows:
        if not seen or seen[-1] != r["group"]:
            seen.append(r["group"])
    assert len(seen) == len(set(seen)), f"分组被打散：{seen}"


def test_suggest_stats_add_up(tmp_home, tmp_path):
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    rows = appdb.suggest(table=_table(**{"chrome.exe": [str(chrome)]}))
    st = appdb.suggest_stats(rows)
    assert st["total"] == len(rows)
    assert st["ready"] + st["need_install"] == len(rows) - sum(1 for r in rows if r["have"])
    assert st["installed_web"] > 0

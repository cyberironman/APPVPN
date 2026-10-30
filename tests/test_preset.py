# -*- coding: utf-8 -*-
"""首次启动就摆好常用软件（左侧那批预置档案）。

这三件事是这个功能的全部意义：
  1. 打开软件左侧就有 Telegram / WhatsApp / Messenger，不用自己找；
  2. 不带软件本体 —— 装了桌面版就指向它，没装就给网页版；
  3. 用户删掉的绝不自作主张长回来。
"""
import json
from pathlib import Path

from appproxy import appdb, preset, profiles
from appproxy.models import Line
from appproxy.registry import Registry


# --------------------------------------------------------------------------- 清单
def test_preset_list_has_the_expected_head_order():
    """用户点名要的就得排在最前，顺序不能乱。"""
    keys = [e["key"] for e in preset.wanted()]
    assert keys[:4] == ["telegram", "whatsapp", "messenger", "facebook"]
    assert len(keys) >= 7
    # 序号必须连续且唯一，否则左侧列表的排序会出现并列
    ranks = [int(e["preset"]) for e in preset.wanted()]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


def test_preset_entries_have_a_way_in():
    """每条预置都要么有 exe（桌面版）要么有 web（网页版），否则建出来是死的。"""
    for item in preset.wanted():
        assert item.get("exe") or item.get("web"), f"{item['key']} 两条路都没有"


# --------------------------------------------------------------------------- 建档
def _table(exe: str, browser: str = "") -> dict:
    """模拟 appfind 的索引：key 是文件名（小写），value 是完整路径。"""
    t = {}
    if exe:
        t[Path(exe).name.lower()] = [exe]
    if browser:
        t[Path(browser).name.lower()] = [browser]
    return t


def test_ensure_creates_profiles(tmp_home, tmp_path):
    exe = tmp_path / "Telegram.exe"
    exe.write_text("x", encoding="utf-8")
    br = tmp_path / "chrome.exe"
    br.write_text("x", encoding="utf-8")

    store = profiles.ProfileStore().load()
    r = preset.ensure(store, table=_table(str(exe), str(br)))

    made = {x["key"]: x for x in r["created"]}
    assert "telegram" in made
    assert made["telegram"]["kind"] == "exe"
    assert made["telegram"]["exe"] == str(exe)
    # 没装桌面版但有浏览器 -> 网页版入口
    assert made["whatsapp"]["kind"] == "web"
    assert made["whatsapp"]["url"].startswith("http")

    # 绑的是「跟着默认线路走」，不是某条具体线路
    assert store.get("telegram").line_id == preset.DEFAULT_LINE


def test_ensure_is_idempotent(tmp_home, tmp_path):
    exe = tmp_path / "Telegram.exe"
    exe.write_text("x", encoding="utf-8")
    br = tmp_path / "chrome.exe"
    br.write_text("x", encoding="utf-8")

    store = profiles.ProfileStore().load()
    t = _table(str(exe), str(br))
    preset.ensure(store, table=t)
    n1 = len(store.profiles)
    r2 = preset.ensure(store, table=t)
    assert r2["ran"] is False          # 版本号到了就不再动
    assert len(store.profiles) == n1


def test_ensure_skips_when_no_browser_and_no_exe(tmp_home):
    """既没装桌面版、又找不到浏览器 —— 建出来点了就是报错，不如不建。"""
    store = profiles.ProfileStore().load()
    r = preset.ensure(store, table={})
    assert r["created"] == []
    assert any("浏览器" in s["why"] for s in r["skipped"])


# --------------------------------------------------------------------------- 删除不许复活
def test_removed_preset_does_not_come_back(tmp_home, tmp_path):
    exe = tmp_path / "Telegram.exe"
    exe.write_text("x", encoding="utf-8")
    br = tmp_path / "chrome.exe"
    br.write_text("x", encoding="utf-8")
    t = _table(str(exe), str(br))

    store = profiles.ProfileStore().load()
    preset.ensure(store, table=t)
    assert store.get("telegram") is not None

    store.remove("telegram")
    store.save()
    assert store.get("telegram") is None
    assert "telegram" in store.preset_removed

    # 升级预置清单（版本号 +1）也不能把它长回来
    r = preset.ensure(store, table=t, version=preset.PRESET_VERSION + 1)
    assert store.get("telegram") is None
    assert any(x["key"] == "telegram" for x in r["skipped"])


def test_restore_brings_back_the_removed_one(tmp_home, tmp_path):
    exe = tmp_path / "Telegram.exe"
    exe.write_text("x", encoding="utf-8")
    br = tmp_path / "chrome.exe"
    br.write_text("x", encoding="utf-8")
    t = _table(str(exe), str(br))

    store = profiles.ProfileStore().load()
    preset.ensure(store, table=t)
    store.remove("telegram")
    store.save()

    r = preset.ensure(store, table=t, force=True)
    assert any(x["key"] == "telegram" for x in r["created"])
    assert store.get("telegram") is not None
    assert store.preset_removed == []


def test_removed_list_survives_reload(tmp_home):
    store = profiles.ProfileStore().load()
    store.preset_removed = ["telegram", "whatsapp"]
    store.save()

    again = profiles.ProfileStore().load()
    assert again.preset_removed == ["telegram", "whatsapp"]


def test_existing_user_profile_is_not_overwritten(tmp_home, tmp_path):
    """用户自己建的同名档案，预置不许覆盖。"""
    exe = tmp_path / "Telegram.exe"
    exe.write_text("x", encoding="utf-8")
    br = tmp_path / "chrome.exe"
    br.write_text("x", encoding="utf-8")

    store = profiles.ProfileStore().load()
    store.upsert(profiles.AppProfile(id="telegram", name="我的电报",
                                      exe=str(exe), line_id=""))
    store.save()

    r = preset.ensure(store, table=_table(str(exe), str(br)))
    assert store.get("telegram").name == "我的电报"
    assert any(x["key"] == "telegram" and "已有" in x["why"] for x in r["skipped"])


# --------------------------------------------------------------------------- 信息接口
def test_info_reports_missing(tmp_home, tmp_path):
    exe = tmp_path / "Telegram.exe"
    exe.write_text("x", encoding="utf-8")
    br = tmp_path / "chrome.exe"
    br.write_text("x", encoding="utf-8")

    store = profiles.ProfileStore().load()
    preset.ensure(store, table=_table(str(exe), str(br)))
    info = preset.info(store)
    assert info["total"] == len(preset.wanted())
    assert info["in_list"] == info["total"]
    assert info["missing"] == []


# --------------------------------------------------------------------------- 左侧排序
def test_snapshot_lists_presets_first(tmp_home, tmp_path):
    """预置的必须排在用户自己建的前面 —— 这是「默认就放在左边」的关键。"""
    from appproxy import pool

    exe = tmp_path / "Telegram.exe"
    exe.write_text("x", encoding="utf-8")
    br = tmp_path / "chrome.exe"
    br.write_text("x", encoding="utf-8")

    reg = Registry().load()
    reg.upsert(Line(id="mine", name="我的线", http_port=17001, socks_port=17002))
    reg.save()

    store = profiles.ProfileStore().load()
    preset.ensure(store, table=_table(str(exe), str(br)))
    store.upsert(profiles.AppProfile(id="zzz-mine", name="我自己的",
                                      exe=str(br), line_id="mine"))
    store.save()

    pool.reload_preset_rank()
    snap = pool.state(reg, store)
    ids = [p["id"] for p in snap["profiles"]]
    assert ids[0] == "telegram"           # 预置排最前
    assert ids[-1] == "zzz-mine"           # 用户自己建的排最后
    # 预置之间按清单序号排
    want = [e["key"] for e in preset.wanted()]
    preset_ids = [i for i in ids if i in want]
    assert preset_ids == sorted(preset_ids, key=want.index)


def test_preset_flag_visible_in_snapshot(tmp_home, tmp_path):
    from appproxy import pool

    exe = tmp_path / "Telegram.exe"
    exe.write_text("x", encoding="utf-8")
    br = tmp_path / "chrome.exe"
    br.write_text("x", encoding="utf-8")

    reg = Registry().load()
    store = profiles.ProfileStore().load()
    preset.ensure(store, table=_table(str(exe), str(br)))
    pool.reload_preset_rank()
    snap = pool.state(reg, store)
    tg = next(p for p in snap["profiles"] if p["id"] == "telegram")
    assert tg["preset"] is True
    assert tg["preset_rank"] > 0


# --------------------------------------------------------------------------- 默认线路
def test_preset_follows_default_line(tmp_home):
    """没设默认线路、但只有一条线时，预置档案应该能用那一条。"""
    reg = Registry().load()
    reg.upsert(Line(id="only", name="唯一一条", http_port=17003, socks_port=17004))
    reg.save()
    assert reg.resolve(preset.DEFAULT_LINE).id == "only"


def test_preset_line_stays_following_default_after_import(tmp_home):
    """导入订阅后，预置档案不用挨个改，自动跟着默认线路走。"""
    reg = Registry().load()
    reg.upsert(Line(id="a", name="甲", http_port=17005, socks_port=17006))
    reg.upsert(Line(id="b", name="乙", http_port=17007, socks_port=17008))
    reg.defaults["default_line"] = "b"
    reg.save()
    assert reg.resolve(preset.DEFAULT_LINE).id == "b"


# --------------------------------------------------------------------------- 清单完整性
def test_every_preset_key_unique():
    keys = [e["key"] for e in preset.wanted()]
    assert len(keys) == len(set(keys))


def test_preset_keys_are_in_appdb():
    db_keys = {e["key"] for e in appdb.entries()}
    for e in preset.wanted():
        assert e["key"] in db_keys


def test_preset_file_is_valid_json():
    p = preset.__file__.replace("preset.py", "")
    with open(p + "data/apps_need_proxy.json", encoding="utf-8") as f:
        d = json.load(f)
    assert d["apps"]

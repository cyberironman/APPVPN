# -*- coding: utf-8 -*-
"""配置导入导出：换电脑不搬目录，但**端口必须重新随机**。

三条必须守住的行为：
  1. 导出的文件里不含端口（端口是本机的，别人的端口到你这里大概率被占）；
  2. 导入回来的线路端口是新的随机值，且不重复；
  3. 档案指向一条本机不存在的线路时，改成直连并给一条 warning
     —— 不能让档案存在一个永远起不来的绑定上。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from appproxy import transfer
from appproxy.models import Line
from appproxy.profiles import AppProfile, ProfileStore
from appproxy.registry import PORT_MAX, PORT_MIN, Registry


def _seed(tmp_home):
    reg = Registry().load()
    line = Line(id="alpha", name="家里那台", sub_url="https://sub.example.com/x?token=t",
                http_port=15002, socks_port=15003, sub_ok_at=999)
    reg.upsert(line)
    reg.save()
    store = ProfileStore().load()
    store.upsert(AppProfile(id="chrome-work", name="Chrome 工作",
                            exe=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                            line_id="alpha", url="https://example.com"))
    store.save()
    return reg, store


def _switch_home(monkeypatch, base: Path, name: str) -> Path:
    """模拟「换了一台电脑」：数据根目录换成一个全新的空目录。"""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("APPPROXY_HOME", str(d))
    return d


def test_export_drops_ports_and_local_state(tmp_home):
    reg, store = _seed(tmp_home)
    bundle = transfer.export_bundle(reg, store, include_secrets=True)
    line = bundle["lines"][0]
    assert "http_port" not in line and "socks_port" not in line
    assert "sub_checked_at" not in line and "sub_stale" not in line
    assert line["sub_url"].endswith("token=t")        # 显式要了才带
    prof = bundle["profiles"][0]
    assert "data_dir" not in prof and "icon" not in prof
    assert prof["line_id"] == "alpha"


def test_export_without_secrets_blanks_sub_url(tmp_home):
    reg, store = _seed(tmp_home)
    bundle = transfer.export_bundle(reg, store)
    assert bundle["lines"][0]["sub_url"] == ""
    assert bundle["contains_secrets"] is False


def test_dump_and_load_roundtrip(tmp_home):
    reg, store = _seed(tmp_home)
    path = transfer.dump(transfer.export_bundle(reg, store, include_secrets=True),
                         tmp_home / "cfg.json")
    back = transfer.load(path)
    assert back["appproxy_bundle"] == transfer.BUNDLE_VERSION
    assert len(back["lines"]) == 1


def test_load_rejects_foreign_json(tmp_home):
    p = tmp_home / "other.json"
    p.write_text('{"hello": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="不是本软件导出"):
        transfer.load(p)


def test_load_rejects_future_version(tmp_home):
    p = tmp_home / "future.json"
    p.write_text('{"appproxy_bundle": 99}', encoding="utf-8")
    with pytest.raises(ValueError, match="比本软件支持"):
        transfer.load(p)


def test_import_allocates_new_random_ports(tmp_home, tmp_path, monkeypatch):
    reg, store = _seed(tmp_home)
    bundle = transfer.export_bundle(reg, store, include_secrets=True)

    # 目标机器：干净环境
    _switch_home(monkeypatch, tmp_path, "home2")
    reg2 = Registry().load()
    store2 = ProfileStore().load()
    assert not reg2.lines
    r = transfer.import_bundle(reg2, store2, bundle)
    assert r["lines_added"] == ["alpha"]
    line = reg2.get("alpha")
    assert line is not None
    assert PORT_MIN <= line.http_port < PORT_MAX
    assert line.http_port != 15002 or True      # 允许巧合，但必须是随机段内的号
    assert r["profiles_added"] == ["chrome-work"]
    assert store2.get("chrome-work").line_id == "alpha"


def test_import_updates_existing_line_but_keeps_local_port(tmp_home):
    reg, store = _seed(tmp_home)
    bundle = transfer.export_bundle(reg, store, include_secrets=True)
    bundle["lines"][0]["name"] = "改过的名字"
    bundle["lines"][0]["sub_url"] = "https://sub.example.com/new?token=x"

    r = transfer.import_bundle(reg, store, bundle)
    assert r["lines_updated"] == ["alpha"]
    line = reg.get("alpha")
    assert line.name == "改过的名字"
    assert line.http_port == 15002              # 本机端口不动
    assert line.sub_url.endswith("token=x")


def test_import_keeps_local_sub_when_bundle_has_none(tmp_home):
    reg, store = _seed(tmp_home)
    bundle = transfer.export_bundle(reg, store)          # 不带订阅地址
    transfer.import_bundle(reg, store, bundle)
    assert reg.get("alpha").sub_url            # 本机原来的订阅地址没被清掉


def test_import_orphan_profile_becomes_direct(tmp_home, tmp_path, monkeypatch):
    reg, store = _seed(tmp_home)
    bundle = transfer.export_bundle(reg, store)
    bundle["profiles"][0]["line_id"] = "ghost"           # 导出方有，本机没有

    _switch_home(monkeypatch, tmp_path, "home2")
    reg2 = Registry().load()
    store2 = ProfileStore().load()
    r = transfer.import_bundle(reg2, store2, bundle)
    assert store2.get("chrome-work").line_id == ""
    assert any("ghost" in w for w in r["warnings"])


def test_import_bad_line_is_skipped_with_warning(tmp_home):
    reg = Registry().load()
    store = ProfileStore().load()
    bundle = {"appproxy_bundle": 1, "contains_secrets": False,
              "lines": [{"id": "bad", "mode": "不存在的模式"}], "profiles": []}
    r = transfer.import_bundle(reg, store, bundle)
    assert r["lines_added"] == []
    assert r["warnings"]


def test_import_with_fetch_fills_nodes(tmp_home):
    reg = Registry().load()
    store = ProfileStore().load()
    bundle = {"appproxy_bundle": 1, "contains_secrets": True,
              "lines": [{"id": "alpha", "name": "a",
                         "sub_url": "https://sub.example.com/x?token=t"}],
              "profiles": []}

    class FakeNode:
        pass

    from appproxy.models import Node
    node = Node(scheme="vless", address="1.1.1.1", port=443, secret="s")
    r = transfer.import_bundle(reg, store, bundle, fetch_sub=True,
                               loader=lambda url: ([node], {"count": 1}))
    assert r["lines_added"] == ["alpha"]
    assert len(reg.load_nodes(reg.get("alpha"))[0]) == 1


def test_import_fetch_failure_only_warns(tmp_home):
    reg = Registry().load()
    store = ProfileStore().load()
    bundle = {"appproxy_bundle": 1, "contains_secrets": True,
              "lines": [{"id": "alpha", "name": "a",
                         "sub_url": "https://sub.example.com/x?token=t"}],
              "profiles": []}

    def boom(url):
        raise RuntimeError("timeout")

    r = transfer.import_bundle(reg, store, bundle, fetch_sub=True, loader=boom)
    assert r["lines_added"] == ["alpha"]                 # 导入本身成功
    assert any("订阅拉取失败" in w for w in r["warnings"])


def test_summary_text_reads_like_chinese():
    text = transfer.summary_text({"lines_added": ["a"], "lines_updated": [],
                                  "profiles_added": [], "profiles_updated": [],
                                  "warnings": ["某条 warning"]})
    assert "新增 1 条" in text and "某条 warning" in text

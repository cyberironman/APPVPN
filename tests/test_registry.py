# -*- coding: utf-8 -*-
from __future__ import annotations

import socket

from appproxy import registry as registry_mod
from appproxy import settings
from appproxy.models import Line, Node
from appproxy.registry import PORT_MAX, PORT_MIN, Registry


def _node(i: int = 1) -> Node:
    return Node(scheme="vless", address=f"1.2.3.{i}", port=443, secret="uuid-" + str(i),
                remark=f"US-{i:02d}")


def test_home_follows_env(tmp_home):
    assert settings.home() == tmp_home
    settings.ensure_dirs()
    assert settings.registry_path().parent == tmp_home / "data"
    assert settings.logs_dir().is_dir()


def test_registry_roundtrip(tmp_home):
    settings.ensure_dirs()
    reg = Registry()
    reg.upsert(Line(id="alpha", name="alpha", sub_url="https://example.com/sub", http_port=10910,
                    socks_port=10911))
    reg.upsert(Line(id="lan", name="lan", http_port=10912, socks_port=10913))
    reg.save()

    again = Registry().load()
    assert [l.id for l in again.lines] == ["alpha", "lan"]
    assert again.require("alpha").socks_port == 10911

    again.upsert(Line(id="alpha", name="改过", http_port=10910))
    again.save()
    assert Registry().load().require("alpha").name == "改过"

    assert again.remove("lan") is True
    assert again.get("lan") is None


def test_node_cache(tmp_home):
    settings.ensure_dirs()
    reg = Registry().load()
    line = Line(id="alpha", name="alpha", http_port=10910)
    nodes = [_node(1), _node(2)]
    reg.save_nodes(line, nodes, {"count": 2})

    loaded, meta = reg.load_nodes(line)
    assert len(loaded) == 2 and loaded[0].uid == nodes[0].uid
    assert meta["count"] == 2


def test_alloc_ports_skips_used(tmp_home):
    """确定性模式：跳过被其它线路占用的那一对。"""
    settings.ensure_dirs()
    reg = Registry().load()
    reg.upsert(Line(id="a", name="a", http_port=20000, socks_port=20001))
    http, socks = reg.alloc_ports(20000)
    assert (http, socks) != (20000, 20001)
    assert socks == http + 1


def test_alloc_ports_is_random_and_spread(tmp_home):
    """连开 12 条线：端口落在 10000–29998、成对、互不重复、不碰代理工具祖传段。

    这条是「每台机器都拿 10840/10841 起」那个老问题的回归测试 ——
    一旦有人把随机改回固定递增，12 条线会全挤在一起。
    """
    settings.ensure_dirs()
    reg = Registry().load()
    seen: set[int] = set()
    for _ in range(12):
        http, socks = reg.alloc_ports()
        assert socks == http + 1, "http / socks 必须是相邻的一对"
        assert PORT_MIN <= http < PORT_MAX, f"端口 {http} 越界"
        assert http % 2 == 0, "起点取偶数，保证 socks 落在 http+1"
        assert not (10800 <= http <= 10999), f"{http} 落在代理祖传段"
        seen.add(http)
        reg.upsert(Line(id=f"l{http}", name="x", http_port=http, socks_port=socks))
    assert len(seen) == 12, "12 次分配不该出现重复端口"


def test_alloc_ports_retries_when_range_is_reserved(tmp_home, monkeypatch):
    """系统保留段要跳过，不能把 WSAEACCES 的端口发给用户。"""
    settings.ensure_dirs()
    reg = Registry().load()
    monkeypatch.setattr(registry_mod, "_excluded_ranges", lambda: [(20000, 20002)])

    class _Rng:
        def __init__(self):
            self.calls = 0

        def randrange(self, _a, _b, _step=1):
            self.calls += 1
            return 20000 if self.calls == 1 else 26000

    rng = _Rng()
    http, socks = reg.alloc_ports(rng=rng)
    assert (http, socks) == (26000, 26001)
    assert rng.calls == 2


def test_alloc_ports_skips_port_already_listening(tmp_home):
    """此刻真有人在这个端口上听着，也不能发出去。"""
    settings.ensure_dirs()
    reg = Registry().load()
    s = socket.socket()
    s.bind(("127.0.0.1", 25000))
    s.listen(1)
    try:
        http, socks = reg.alloc_ports(25000)
        assert http != 25000
        assert socks == http + 1
    finally:
        s.close()

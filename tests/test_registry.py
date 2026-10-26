# -*- coding: utf-8 -*-
from __future__ import annotations

from appproxy import settings
from appproxy.models import Line, Node
from appproxy.registry import Registry


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
    settings.ensure_dirs()
    reg = Registry().load()
    reg.upsert(Line(id="a", name="a", http_port=10840, socks_port=10841))
    http, socks = reg.alloc_ports(10840)
    assert (http, socks) != (10840, 10841)
    assert socks == http + 1

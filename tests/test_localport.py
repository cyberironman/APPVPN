# -*- coding: utf-8 -*-
"""本机自用端口（单实例锁 / 控制口 / 界面服务）的随机分配。

这几个端口以前是写死的 10896 / 10897 / 1443，被别的程序占掉就表现成
「双击没反应」或「窗口打不开」，且日志里看不出是端口冲突。这里把
「一定随机、避开祖传段、重启沿用、绝不撞自己的其它槽位」钉死。
"""
from __future__ import annotations

import socket

import pytest

from appproxy import localport, settings


@pytest.fixture()
def ready(tmp_home):
    settings.ensure_dirs()
    return tmp_home


def test_alloc_lands_in_own_range(ready):
    """分出来的端口必须落在 AppProxy 自己的段里 —— 不能又跑到 108xx 去。"""
    p = localport.alloc("gui_http")
    assert localport.PORT_MIN <= p < localport.PORT_MAX
    assert p % 2 == 0, "成对分配的端口必须是偶数，+1 才是 socks"


def test_alloc_avoids_legacy_segments(ready):
    """代理工具祖传段（1080/7890/108xx/18488）一个都不许碰。"""
    for slot in localport.SLOTS:
        p = localport.alloc(slot)
        for lo, hi in localport.AVOID_RANGES:
            assert not (lo <= p <= hi), f"{slot}={p} 落进了祖传段 {lo}-{hi}"


def test_alloc_is_persisted_across_restarts(ready):
    """同一个槽位重读必须拿到同一个号 —— 端口一跳，托盘就找不到窗口了。"""
    first = localport.alloc("gui_http")
    assert localport.get("gui_http") == first
    assert localport.alloc("gui_http") == first


def test_slots_never_share_a_port(ready):
    """三个槽位互不重复，否则界面服务和锁会互相踢。"""
    got = {slot: localport.alloc(slot) for slot in localport.SLOTS}
    assert len(set(got.values())) == len(localport.SLOTS), got


def test_occupied_port_is_replaced(ready):
    """持久化的号被别人占了（bind 不上）时必须换，而不是硬撞。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    taken = s.getsockname()[1]
    try:
        localport.save_state({"gui_http": taken})
        assert localport.alloc("gui_http") != taken
    finally:
        s.close()


def test_bind_returns_listening_socket(ready):
    """bind() 给的 socket 必须真的在听，并且端口被记下来了。"""
    s = localport.bind("ctl")
    assert s is not None
    port = s.getsockname()[1]
    try:
        assert localport.PORT_MIN <= port < localport.PORT_MAX
        assert localport.get("ctl") == port
        # 真的能连上
        c = socket.create_connection(("127.0.0.1", port), 1.0)
        c.close()
    finally:
        s.close()


def test_bind_reuses_port_after_restart(ready):
    """进程重启后沿用同一个控制口 —— 否则 `--quit` 找不到正在跑的托盘。"""
    s1 = localport.bind("ctl")
    assert s1 is not None
    port = s1.getsockname()[1]
    s1.close()

    s2 = localport.bind("ctl")
    assert s2 is not None
    try:
        assert s2.getsockname()[1] == port
    finally:
        s2.close()


def test_out_of_range_persisted_port_is_discarded(ready):
    """老版本写进文件的 10896 这类值，一律不认 —— 保证一次切换干净。"""
    localport.save_state({"ctl": 10896})
    s = localport.bind("ctl")
    assert s is not None
    try:
        assert s.getsockname()[1] != 10896
        assert localport.PORT_MIN <= s.getsockname()[1] < localport.PORT_MAX
    finally:
        s.close()


def test_forget_clears_slot(ready):
    localport.alloc("gui_http")
    localport.forget("gui_http")
    assert localport.get("gui_http") == 0

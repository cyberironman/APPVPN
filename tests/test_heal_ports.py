# -*- coding: utf-8 -*-
"""端口体检（`Registry.heal_ports`）—— 存量线路在启动时被自动换到随机端口。

v0.6.3 只保证「新加线路随机」，老线路里那批 108xx / 109xx 一直留着，
正好落在三个代理程序共用的祖传段。这里把「什么时候换、什么时候绝不能换」
钉死：**自家内核正在听的端口一个都不许动**，否则体检等于把用户网络踢断。
"""
from __future__ import annotations

import pytest

from appproxy import registry, settings
from appproxy.models import Line
from appproxy.registry import PORT_MAX, PORT_MIN, Registry


@pytest.fixture()
def ready(tmp_home):
    settings.ensure_dirs()
    return tmp_home


@pytest.fixture()
def free_ports(monkeypatch):
    """默认所有端口都空闲；返回一个可以「占住」若干端口的句柄。"""
    occupied: set[int] = set()

    def fake_free(port: int) -> bool:
        return int(port) not in occupied

    def fake_open(port: int) -> bool:
        return int(port) in occupied

    monkeypatch.setattr(registry, "_port_free", fake_free)
    return occupied


def _line(lid: str, http: int, socks: int | None = None) -> Line:
    return Line(id=lid, name=lid, http_port=http,
                socks_port=http + 1 if socks is None else socks)


def _in_range(p: int) -> bool:
    return PORT_MIN <= p <= PORT_MAX - 1


def test_legacy_segment_is_moved(ready, free_ports):
    """108xx 祖传段：即使系统空闲也要换掉（否则跟 v2rayN 谁先绑谁活）。"""
    reg = Registry()
    reg.lines = [_line("alpha", 10801)]
    changes = reg.heal_ports(save=False)

    assert len(changes) == 1, changes
    lid, old, new = changes[0]
    assert (lid, old) == ("alpha", 10801)
    assert _in_range(new)
    assert reg.lines[0].socks_port == new + 1


def test_out_of_range_is_moved(ready, free_ports):
    """越过区间上限的老值（v0.6.3 早期留下的 3xxxx）也要换。"""
    reg = Registry()
    reg.lines = [_line("alpha", 39999)]
    assert reg.heal_ports(save=False)
    assert _in_range(reg.lines[0].http_port)


def test_occupied_port_is_moved(ready, free_ports):
    """端口被别的程序占着（bind 不起来）→ 换。"""
    free_ports.update({15000, 15001})
    reg = Registry()
    reg.lines = [_line("alpha", 15000)]
    changes = reg.heal_ports(save=False)

    assert changes and changes[0][1] == 15000
    assert reg.lines[0].http_port != 15000
    assert _in_range(reg.lines[0].http_port)


def test_duplicate_ports_are_split(ready, free_ports):
    """两条线路撞同一个端口 → 后一条换，换完两条仍不重复。"""
    reg = Registry()
    reg.lines = [_line("a", 15100), _line("b", 15100)]
    changes = reg.heal_ports(save=False)

    assert [c[0] for c in changes] == ["b"]
    a, b = reg.lines
    assert a.http_port != b.http_port
    assert {a.http_port, a.socks_port}.isdisjoint({b.http_port, b.socks_port})


def test_healthy_line_is_left_alone(ready, free_ports):
    """端口本来就在区间内、没被占用、也没重复 —— 一个字都不改。"""
    reg = Registry()
    reg.lines = [_line("alpha", 16000)]
    assert reg.heal_ports(save=False) == []
    assert (reg.lines[0].http_port, reg.lines[0].socks_port) == (16000, 16001)


def test_running_own_core_is_never_killed(ready, free_ports, monkeypatch):
    """自家内核正听着这个端口时，**绝不能**把它换掉 —— 那等于把用户网络踢断。

    这正是 10-29 那次事故的反面教材：端口「不通」和「内核死了」是两件事。
    """
    free_ports.update({16200, 16201})                 # 端口被占（其实是自己）
    monkeypatch.setattr(Registry, "_core_running_on", lambda self, line, p: True)

    reg = Registry()
    reg.lines = [_line("alpha", 16200)]
    assert reg.heal_ports(save=False) == [], "自家内核在跑，不该动"
    assert reg.lines[0].http_port == 16200


def test_change_is_audited(ready, free_ports):
    """每次换端口写一行 logs/ports.log，出事能倒查哪天换了什么。"""
    reg = Registry()
    reg.lines = [_line("alpha", 10803)]
    reg.heal_ports(save=False)

    log_file = settings.logs_dir() / "ports.log"
    assert log_file.is_file()
    text = log_file.read_text(encoding="utf-8")
    assert "alpha" in text and "10803" in text


def test_heal_persists_when_save(ready, free_ports):
    """save=True（`load()` 的默认路径）时结果要落盘，不能只改内存。"""
    reg = Registry()
    reg.lines = [_line("alpha", 10805)]
    reg.save()

    reloaded = Registry().load()          # load() 内部会跑体检
    assert _in_range(reloaded.lines[0].http_port)
    assert Registry().load().lines[0].http_port == reloaded.lines[0].http_port


def test_change_drops_stale_runtime_config(ready, free_ports):
    """换端口之后，`runtime/<id>.json` 必须作废。

    这是真机上踩到的坑：v0.6.4 只改了 lines.json 的端口，配置里 inbounds 还是
    10930。`ensure_core` 见配置已存在就直接拿它起内核 → 内核去听旧端口 →
    界面报「25 秒内端口未就绪」，而 `sing-box-<id>.log` 是空的，极难查。
    """
    reg = Registry()
    reg.lines = [_line("alpha", 10930)]
    reg.save()
    cfg = reg.lines[0].config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('{"inbounds":[{"listen_port":10930}]}', encoding="utf-8")

    reg.heal_ports(save=False)
    assert reg.lines[0].http_port != 10930
    assert not cfg.exists(), "端口换了，旧配置留着就会去听旧端口"


def test_no_change_keeps_config(ready, free_ports):
    """端口本来就正常时，配置不许被删（删了虽然能重建，但没必要让用户多等）。"""
    reg = Registry()
    reg.lines = [_line("alpha", 17002)]
    reg.save()
    cfg = reg.lines[0].config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('{"inbounds":[{"listen_port":17002}]}', encoding="utf-8")

    assert reg.heal_ports(save=False) == []
    assert cfg.exists()


def test_change_drops_stale_runtime_config(ready, free_ports):
    """换端口之后，`runtime/<id>.json` 必须作废。

    这是真机上踩到的坑：v0.6.4 只改了 lines.json 的端口，配置里 inbounds 还是
    10930。`ensure_core` 见配置已存在就直接拿它起内核 → 内核去听旧端口 →
    界面报「25 秒内端口未就绪」，而 `sing-box-<id>.log` 是空的，极难查。
    """
    reg = Registry()
    reg.lines = [_line("alpha", 10930)]
    reg.save()
    cfg = reg.lines[0].config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('{"inbounds":[{"listen_port":10930}]}', encoding="utf-8")

    reg.heal_ports(save=False)
    assert reg.lines[0].http_port != 10930
    assert not cfg.exists(), "端口换了，旧配置留着就会去听旧端口"


def test_no_change_keeps_config(ready, free_ports):
    """端口本来就正常时，配置不许被删（删了虽然能重建，但没必要让用户多等）。"""
    reg = Registry()
    reg.lines = [_line("alpha", 17002)]
    reg.save()
    cfg = reg.lines[0].config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('{"inbounds":[{"listen_port":17002}]}', encoding="utf-8")

    assert reg.heal_ports(save=False) == []
    assert cfg.exists()

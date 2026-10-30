# -*- coding: utf-8 -*-
"""订阅自动更新：到点没有、退避算得对不对、拉到新节点后会不会乱动内核。

这里全部用假 loader，不碰网络 —— 自动更新是后台常驻逻辑，必须能在
「订阅站 502」「节点一模一样」这些情况下都保持在可预测状态。
"""
from __future__ import annotations

import time

from appproxy import autosub
from appproxy.models import Line, Node
from appproxy.registry import Registry


def _line(**kw) -> Line:
    base = dict(id="alpha", name="alpha", sub_url="https://sub.example.com/x?token=t")
    base.update(kw)
    return Line(**base)


def _node(i: int = 1) -> Node:
    return Node(scheme="vless", address=f"1.1.1.{i}", port=443, secret=f"sid-{i}",
                remark=f"US-{i}")


# --------------------------------------------------------------------------- 到期判断
def test_never_fetched_is_due():
    line = _line()
    assert autosub.due(line, now=1_000_000) is True


def test_recent_success_not_due():
    now = 1_000_000
    line = _line(sub_ok_at=now - 3600, sub_checked_at=now - 3600)   # 1 小时前
    assert autosub.due(line, now=now) is False


def test_after_interval_is_due():
    now = 1_000_000
    line = _line(sub_ok_at=now - 13 * 3600, sub_checked_at=now - 13 * 3600)
    assert autosub.due(line, now=now) is True


def test_no_sub_url_never_due():
    line = _line(sub_url="")
    assert autosub.due(line, now=1_000_000) is False


def test_interval_zero_disables():
    line = _line(sub_interval_h=0)
    assert autosub.interval_of(line) == 0
    assert autosub.due(line, now=1_000_000) is False
    assert "关闭" in autosub.describe(line, now=1_000_000)


def test_per_line_interval_overrides_global():
    line = _line(sub_interval_h=3)
    assert autosub.interval_of(line, default=12) == 3
    assert autosub.interval_of(_line(), default=6) == 6      # -1 → 跟随全局


def test_global_zero_is_not_eaten_by_default():
    """`defaults['sub_interval_h'] = 0` 是「全关」，不能被 `or 12` 吃掉。"""
    reg = Registry(path=__import__("pathlib").Path("/nonexistent/lines.json"))
    reg.defaults["sub_interval_h"] = 0
    assert autosub.global_interval(reg) == 0
    reg.defaults["sub_interval_h"] = 6
    assert autosub.global_interval(reg) == 6


# --------------------------------------------------------------------------- 退避
def test_failure_backoff_grows_and_caps():
    now = 1_000_000
    line = _line(sub_fail=1, sub_checked_at=now - 400)      # 失败后过了 400 秒
    assert autosub.due(line, now=now) is True               # 第一档退避 300 秒
    line.sub_checked_at = now - 100
    assert autosub.due(line, now=now) is False

    line.sub_fail = 5                                        # 已经到上限 6 小时
    line.sub_checked_at = now - 6 * 3600 - 1
    assert autosub.due(line, now=now) is True
    line.sub_checked_at = now - 6 * 3600 + 60
    assert autosub.due(line, now=now) is False


def test_describe_reports_failure_reason():
    now = 1_000_000
    line = _line(sub_fail=2, sub_error="HTTP 502", sub_checked_at=now - 60)
    text = autosub.describe(line, now=now)
    assert "HTTP 502" in text and "连续 2 次" in text


# --------------------------------------------------------------------------- 拉取
def test_refresh_ok_updates_state(tmp_home):
    reg = Registry().load()
    line = _line()
    reg.upsert(line)
    reg.save()
    nodes = [_node(1), _node(2)]
    r = autosub.refresh(reg, line, loader=lambda url: (nodes, {"count": 2}), now=1000)
    assert r["ok"] and r["count"] == 2
    assert line.sub_fail == 0 and line.sub_ok_at == 1000
    assert len(reg.load_nodes(line)[0]) == 2


def test_refresh_failure_records_error(tmp_home):
    reg = Registry().load()
    line = _line()
    reg.upsert(line)
    reg.save()

    def boom(url):
        raise RuntimeError("拉取订阅失败：timeout")

    r = autosub.refresh(reg, line, loader=boom, now=2000)
    assert r["ok"] is False
    assert line.sub_fail == 1
    assert "timeout" in line.sub_error
    assert line.sub_checked_at == 2000


def test_refresh_marks_stale_only_when_nodes_changed(tmp_home, monkeypatch):
    reg = Registry().load()
    line = _line(http_port=15002)
    reg.upsert(line)
    reg.save()
    autosub.refresh(reg, line, loader=lambda url: ([_node(1)], {}), now=1000)

    # 节点没变 → 不该标记 stale
    autosub.refresh(reg, line, loader=lambda url: ([_node(1)], {}), now=2000)
    assert line.sub_stale is False

    # 内核在跑 + 节点变了 → 标记 stale，但**不重启**
    monkeypatch.setattr(autosub.coreproc, "port_open", lambda p: True)
    autosub.refresh(reg, line, loader=lambda url: ([_node(9)], {}), now=3000)
    assert line.sub_stale is True


def test_refresh_drops_config_when_core_stopped(tmp_home, monkeypatch):
    reg = Registry().load()
    line = _line()
    reg.upsert(line)
    reg.save()
    autosub.refresh(reg, line, loader=lambda url: ([_node(1)], {}), now=1000)
    cfg = line.config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(autosub.coreproc, "port_open", lambda p: False)
    autosub.refresh(reg, line, loader=lambda url: ([_node(7)], {}), now=2000)
    assert not cfg.exists()            # 旧配置作废，下次启动用新节点重建


def test_run_due_respects_limit_and_offs(tmp_home):
    reg = Registry().load()
    a = _line(id="a", name="a", sub_interval_h=0)
    b = _line(id="b", name="b")
    c = _line(id="c", name="c")
    for l in (a, b, c):
        reg.upsert(l)
    reg.save()
    calls: list[str] = []

    def loader_for(url):
        calls.append(url)
        return [_node(1)], {}

    rows = autosub.run_due(reg, loader=loader_for, now=1000, limit=1)
    assert len(rows) == 1                     # limit 生效
    assert len(calls) == 1
    rows = autosub.run_due(reg, loader=loader_for, now=1001, limit=9)
    # 第一轮已经把 b 拉过了（刚拉过不再到期），a 关着，所以只剩 c
    assert {r["line"] for r in rows} == {"c"}
    assert {r["line"] for r in (rows)} != {"a"}


def test_status_rows_have_human_text(tmp_home):
    reg = Registry().load()
    reg.upsert(_line())
    reg.save()
    rows = autosub.status(reg, now=int(time.time()))
    assert rows and "更新" in rows[0]["text"]

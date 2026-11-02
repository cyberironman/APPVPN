# -*- coding: utf-8 -*-
"""线路上的两个整合动作：固定最快节点、开代理终端。

钉住四件事：
  1. pin-fast 实测后把最快的节点写成 mode=single + node_key（「固定快速 IP」的核心）；
  2. 全部节点不通时明确报错，不许假装固定成功；
  3. terminal 建出的档案：exe 是真的终端程序、绑对线路、env 注入代理变量；
  4. 终端档案已开着时不重复拉起。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from appproxy import gui_support, latency, pool, procwatch, xrayconf
from appproxy.models import Line, Node
from appproxy.profiles import ProfileStore
from appproxy.registry import Registry
from appproxy.webui import api

PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"


def _node(remark: str, addr: str) -> Node:
    return Node(scheme="vless", address=addr, port=443, secret="uuid-" + addr,
                remark=remark, network="ws", security="tls", sni="example.com")


@pytest.fixture()
def env(tmp_home, monkeypatch):
    """一条已有 3 个节点快照的 sing-box 线路；不起真内核。"""
    reg = Registry().load()
    reg.upsert(Line(id="wa", name="WhatsApp 专线", http_port=19980,
                    socks_port=19981, engine="singbox"))
    reg.save()
    nodes = [_node("US-1", "1.1.1.1"), _node("HK-1", "2.2.2.2"), _node("US-2", "3.3.3.3")]
    reg.save_nodes(reg.get("wa"), nodes, {"source": "test"})
    # 不真的重启内核
    monkeypatch.setattr(pool, "ensure_core", lambda r, l, **kw: "（测试：不启动）")
    monkeypatch.setattr(api.coreproc, "stop", lambda cfg: 0)

    class E:
        pass

    e = E()
    e.client = TestClient(api.app)
    e.reg = reg
    e.nodes = nodes
    return e


def _line():
    return Registry().load().require("wa")


def test_pin_fast_fixes_fastest_node(env, monkeypatch):
    # 2.2.2.2 (HK-1) 最快
    monkeypatch.setattr(latency, "measure",
                        lambda nodes, binary, target, **kw:
                        {n.uid: 300 for n in nodes} | {nodes[1].uid: 80})
    r = env.client.post("/api/line/wa/pin-fast")
    assert r.status_code == 200
    _wait_done(env, r.json()["id"])
    line = _line()
    assert line.mode == "single"
    assert line.node_key == env.nodes[1].uid


def test_pin_fast_all_down_errors(env, monkeypatch):
    monkeypatch.setattr(latency, "measure",
                        lambda nodes, binary, target, **kw:
                        {n.uid: None for n in nodes})
    r = env.client.post("/api/line/wa/pin-fast")
    assert r.status_code == 200          # 后台任务：HTTP 层先受理
    task = r.json()
    assert task["status"] == "running"
    _wait_done(env, task["id"])
    assert "都不通" in env.task_detail
    assert _line().mode == "auto"        # 没固定成功，原样保留


def _wait_done(env, tid: str) -> None:
    import time
    for _ in range(100):
        t = api.TASKS.get(tid)
        if t and t["status"] != "running":
            env.task_detail = t["detail"]
            return
        time.sleep(0.02)
    raise AssertionError("后台任务 2 秒内没跑完")


def test_pin_fast_rebuilds_single_mode_config(env, monkeypatch):
    monkeypatch.setattr(latency, "measure",
                        lambda nodes, binary, target, **kw:
                        {n.uid: 100 for n in nodes} | {nodes[2].uid: 60})
    r = env.client.post("/api/line/wa/pin-fast")
    _wait_done(env, r.json()["id"])
    line = _line()
    cfg = gui_support.build_config(line, env.nodes, None)
    # single 模式：路由 final 指向固定节点（sing-box 的 outbounds 仍全挂，无碍）
    assert cfg["route"]["final"] == "n-" + line.node_key
    assert line.node_key == env.nodes[2].uid      # 测得最快的 US-2


def test_terminal_creates_profile_and_launches(env, monkeypatch):
    monkeypatch.setattr(api, "_find_terminal", lambda: PWSH)
    launched = {}
    monkeypatch.setattr(api.launcher, "launch",
                        lambda prof, reg: launched.setdefault("id", prof.id))
    r = env.client.post("/api/line/wa/terminal")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    store = ProfileStore().load()
    prof = store.get(d["id"])
    assert prof is not None
    assert prof.exe == PWSH
    assert prof.line_id == "wa"
    assert prof.id.startswith("terminal-")
    # env 注入：generic 程序靠 HTTP_PROXY/HTTPS_PROXY 走线
    plan = __import__("appproxy.profiles", fromlist=["plan"]).plan(
        prof, Registry().load().require("wa"))
    assert plan.env["HTTP_PROXY"] == "http://127.0.0.1:19980"
    assert plan.env["HTTPS_PROXY"] == "http://127.0.0.1:19980"
    assert launched["id"] == prof.id


def test_terminal_reuses_running(env, monkeypatch):
    monkeypatch.setattr(api, "_find_terminal", lambda: PWSH)
    calls = []
    monkeypatch.setattr(api.launcher, "launch",
                        lambda prof, reg: calls.append(prof.id))
    env.client.post("/api/line/wa/terminal")
    # 第二次：假装终端还开着
    monkeypatch.setattr(api.procwatch, "snapshot",
                        lambda: _fake_snap(ProfileStore().load().get("terminal-wa")))
    r = env.client.post("/api/line/wa/terminal").json()
    assert r["ok"] is True
    assert len(calls) == 1               # 没有再拉起第二个


def _fake_snap(prof):
    snap = procwatch.Snapshot()
    if prof is not None:
        snap.by_exe[procwatch._norm(prof.exe)] = [4321]
    return snap


def test_find_terminal_prefers_pwsh(tmp_path, monkeypatch):
    fake = tmp_path / "pwsh.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(api, "_TERMINAL_CANDIDATES", (str(fake),))
    assert api._find_terminal() == str(fake)
    monkeypatch.setattr(api, "_TERMINAL_CANDIDATES", (str(tmp_path / "none.exe"),))
    with pytest.raises(RuntimeError):
        api._find_terminal()

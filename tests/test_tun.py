# -*- coding: utf-8 -*-
"""步骤 5：需代理软件库 / TUN 配置 / 提权任务。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from appproxy import appdb, elevate, settings, tunconf
from appproxy.models import Line, Node


def _node(addr="1.2.3.4", uid="11111111-2222-3333-4444-555555555555") -> Node:
    return Node(scheme="vless", address=addr, port=443, secret=uid, remark="US",
                network="ws", security="tls", sni="example.com", path="/ws")


# --------------------------------------------------------------------------- 清单
def test_find_by_exe_name_and_path():
    assert appdb.find("chrome.exe")["group"] == "浏览器与 Google"
    assert appdb.find(r"C:\Program Files\Google\Chrome\Application\Chrome.EXE")
    assert appdb.find("Codex.exe")["name"] == "OpenAI Codex CLI"
    assert appdb.find("telegram.exe")["level"] == "must"


def test_find_unknown_returns_none():
    assert appdb.find("notepad.exe") is None
    assert appdb.find("") is None
    assert appdb.needed("mspaint.exe") is False


def test_web_only_entries_have_no_exe():
    """YouTube / Facebook 这类只有网页版，不能按进程匹配，但必须留在清单里当说明。"""
    names = {e["name"] for e in appdb.web_only()}
    assert "YouTube" in names
    assert "Facebook" in names
    assert all(not (e.get("exe") or []) for e in appdb.web_only())


def test_mark_sorts_needed_first():
    items = [{"name": "记事本", "exe": r"C:\Windows\notepad.exe"},
             {"name": "Telegram", "exe": r"C:\Users\p\AppData\Roaming\Telegram Desktop\Telegram.exe"},
             {"name": "计算器", "exe": r"C:\Windows\calc.exe"}]
    out = appdb.mark(items)
    assert out[0]["needed"] is True and out[0]["name"] == "Telegram"
    assert out[1]["needed"] is False
    assert out[0]["why"]


def test_override_file_wins(tmp_path, monkeypatch):
    """数据目录里放一份就能盖掉内置清单 —— 用户/在线更新都不该改程序文件。"""
    home = tmp_path / "h"
    monkeypatch.setenv("APPPROXY_HOME", str(home))
    path = appdb.override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": "test", "apps": [
        {"exe": ["foo.exe"], "name": "Foo", "group": "G", "level": "must", "why": "x"}]},
        ensure_ascii=False), encoding="utf-8")
    assert appdb.find("foo.exe")["name"] == "Foo"
    assert appdb.find("chrome.exe") is None      # 被整份覆盖，不是合并
    appdb.load(reload=True)


# --------------------------------------------------------------------------- TUN
def test_tun_config_shape():
    cfg = tunconf.build({"alpha": [_node()]},
                        {"chrome.exe": "auto-alpha", "Telegram.exe": "auto-alpha"})
    assert cfg["inbounds"][0]["type"] == "tun"
    assert cfg["inbounds"][0]["auto_route"] is True
    # 没建档案的软件一律直连
    assert cfg["route"]["final"] == "direct"
    # sing-box 1.12+ 不写这个会直接 check 失败
    assert cfg["route"]["default_domain_resolver"] == "local-dns"
    procs = [p for r in cfg["route"]["rules"] for p in (r.get("process_name") or [])]
    assert sorted(procs) == ["Telegram.exe", "chrome.exe"]
    # 走代理的进程用加密 DNS，否则解析结果照样被污染
    dns_rules = cfg["dns"]["rules"]
    assert dns_rules and dns_rules[0]["server"] == "remote-dns"
    assert cfg["dns"]["final"] == "local-dns"


def test_tun_rejects_unknown_line():
    with pytest.raises(tunconf.ConfigError):
        tunconf.build({"alpha": [_node()]}, {"chrome.exe": "auto-nope"})


def test_tun_requires_nodes():
    with pytest.raises(tunconf.ConfigError):
        tunconf.build({}, {})


def test_tun_config_passes_real_check(tmp_home):
    """有 sing-box 就真校验一次：TUN 配置写错，用户那边就是整机断网。"""
    from appproxy import coreproc
    binary = settings.find_engine("singbox", None)
    if binary is None:
        pytest.skip("本机没有 sing-box")
    cfg = tunconf.build({"alpha": [_node()]}, {"chrome.exe": "auto-alpha"})
    path = settings.runtime_dir() / "tun-test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    ok, out = coreproc.validate(path, binary, "singbox")
    assert ok, out


# --------------------------------------------------------------------------- 提权
def test_task_xml_has_no_trigger_and_highest_runlevel():
    xml = elevate.task_xml(Path(r"D:\a b\sing-box.exe"), Path(r"D:\x y\data\runtime\tun.json"))
    assert "<Triggers />" in xml                       # 没有触发器 = 只能手动点
    assert "<RunLevel>HighestAvailable</RunLevel>" in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml   # 不设时限，否则 3 天被掐
    assert 'run -c "D:\\x y\\data\\runtime\\tun.json"' in xml       # 路径带空格必须带引号


def test_task_xml_is_utf16_because_schtasks_needs_it(tmp_path):
    xml_path = elevate.write_xml(Path(r"D:\sb\sing-box.exe"),
                                 Path(r"D:\sb\tun.json"), tmp_path / "t.xml")
    raw = xml_path.read_bytes()
    assert raw[:2] in (b"\xff\xfe", b"\xfe\xff")
    assert "<Task" in xml_path.read_text(encoding="utf-16")


def test_status_reports_not_installed_without_touching_system(monkeypatch):
    """查询走 schtasks；这里替换掉，确认返回结构稳定（不是真去查本机）。"""
    monkeypatch.setattr(elevate, "_schtasks", lambda *a, **k: (1, "ERROR: not found"))
    elevate.cache_clear()
    st = elevate.status()
    assert st == {"installed": False, "running": False, "raw": ""}


def test_install_reports_admin_hint_when_denied(monkeypatch, tmp_path):
    monkeypatch.setattr(elevate, "_schtasks", lambda *a, **k: (1, "错误: 拒绝访问。"))
    with pytest.raises(elevate.ElevateError) as ei:
        elevate.install(Path(r"D:\sb\sing-box.exe"), tmp_path / "tun.json")
    assert "管理员" in str(ei.value)


# --------------------------------------------------------------------------- 自己弹 UAC
def test_install_with_uac_skips_uac_when_already_admin(monkeypatch, tmp_path):
    """已经是管理员：直接装完，不该弹任何窗口。"""
    called = []
    monkeypatch.setattr(elevate, "install", lambda b, c: called.append("install") or "装好了")
    monkeypatch.setattr(elevate, "_launch_elevated_install",
                        lambda b, c: called.append("uac") or 42)
    msg = elevate.install_with_uac(Path(r"D:\sb\sing-box.exe"), tmp_path / "tun.json")
    assert "装好" in msg
    assert called == ["install"]          # 没走 UAC


def test_install_with_uac_launches_uac_then_waits_for_task(monkeypatch, tmp_path):
    """普通权限：先失败 → 弹 UAC → 任务出现 → 返回成功。"""
    def boom(binary, cfg):
        raise elevate.ElevateError("拒绝访问")

    monkeypatch.setattr(elevate, "install", boom)
    launched = []
    monkeypatch.setattr(elevate, "_launch_elevated_install",
                        lambda b, c: launched.append(str(b)) or 42)   # >32 = 已弹出
    seq = iter([False, False, True])
    monkeypatch.setattr(elevate, "_query_installed", lambda: next(seq, True))
    monkeypatch.setattr(elevate, "cache_clear", lambda: None)

    msg = elevate.install_with_uac(Path(r"D:\sb\sing-box.exe"), tmp_path / "tun.json",
                                   timeout=5)
    assert "提权任务已装好" in msg
    assert launched, "必须真的去弹 UAC"


def test_install_with_uac_user_says_no(monkeypatch, tmp_path):
    """用户在 UAC 弹窗点「否」→ 说人话，不要 traceback。"""
    monkeypatch.setattr(elevate, "install",
                        lambda b, c: (_ for _ in ()).throw(elevate.ElevateError("拒绝访问")))
    monkeypatch.setattr(elevate, "_launch_elevated_install", lambda b, c: 5)   # 5 = 取消
    with pytest.raises(elevate.ElevateError) as ei:
        elevate.install_with_uac(Path(r"D:\sb\sing-box.exe"), tmp_path / "tun.json")
    assert "取消" in str(ei.value)
    assert "再点一次" in str(ei.value)


def test_install_with_uac_timeout_is_readable(monkeypatch, tmp_path):
    monkeypatch.setattr(elevate, "install",
                        lambda b, c: (_ for _ in ()).throw(elevate.ElevateError("拒绝访问")))
    monkeypatch.setattr(elevate, "_launch_elevated_install", lambda b, c: 42)
    monkeypatch.setattr(elevate, "_query_installed", lambda: False)
    with pytest.raises(elevate.ElevateError) as ei:
        elevate.install_with_uac(Path(r"D:\sb\sing-box.exe"), tmp_path / "tun.json",
                                 timeout=0.3)
    assert "还没装好" in str(ei.value)


def test_elevated_child_entrypoint_needs_args(tmp_path):
    """提权子进程入口参数不全时，返回非 0（不能静默成功）。"""
    assert elevate.main(["install"]) == 2
    assert elevate.main([]) == 2


# --------------------------------------------------------------------------- 总控
def _prepare(home: Path, *, bind_line: bool) -> None:
    from appproxy import profiles
    from appproxy.registry import Registry

    reg = Registry().load()
    line = Line(id="alpha", name="alpha", http_port=10801, socks_port=10802)
    reg.upsert(line)
    reg.save()
    reg.save_nodes(line, [_node()], {})

    store = profiles.ProfileStore().load()
    store.upsert(profiles.AppProfile(id="c1", name="Chrome·示例A",
                                     exe=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                     line_id="alpha" if bind_line else ""))
    store.upsert(profiles.AppProfile(id="t1", name="Telegram",
                                     exe=r"C:\Users\p\AppData\Roaming\Telegram Desktop\Telegram.exe",
                                     line_id="alpha" if bind_line else ""))
    store.save()


def test_collect_maps_profiles_to_process_names(tmp_home):
    from appproxy import tunmgr
    _prepare(tmp_home, bind_line=True)
    groups, rules = tunmgr.collect()
    assert list(groups) == ["alpha"]
    assert rules == {"chrome.exe": "auto-alpha", "Telegram.exe": "auto-alpha"}


def test_collect_skips_profiles_bound_to_direct(tmp_home):
    """绑定「直连」的档案不参与 TUN 规则 —— 用户明确说过它不用代理。"""
    from appproxy import tunmgr
    _prepare(tmp_home, bind_line=False)
    _, rules = tunmgr.collect()
    assert rules == {}


def test_collect_without_lines_raises(tmp_home):
    from appproxy import tunmgr
    with pytest.raises(tunmgr.TunError):
        tunmgr.collect()


def test_status_does_not_raise_without_task(tmp_home, monkeypatch):
    from appproxy import tunmgr
    monkeypatch.setattr(elevate, "_schtasks", lambda *a, **k: (1, "not found"))
    elevate.cache_clear()
    st = tunmgr.status()
    assert st["on"] is False and st["installed"] is False
    assert st["config"].endswith("tun.json")

# -*- coding: utf-8 -*-
"""内核池与进程探测的单元测试（不碰真实进程、不碰真实内核）。"""
from __future__ import annotations

from appproxy import pool, procwatch, settings
from appproxy.models import Line
from appproxy.profiles import AppProfile

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def _chrome(pid: str, data: str) -> AppProfile:
    return AppProfile(id=pid, name=pid, exe=CHROME, kind="chromium",
                      data_dir=data, line_id="alpha")


def _generic(pid: str, exe: str = r"C:\Windows\System32\notepad.exe") -> AppProfile:
    return AppProfile(id=pid, name=pid, exe=exe, kind="generic", line_id="alpha")


# --------------------------------------------------------------------------- 路径归一
def test_norm_path_forms():
    a = procwatch._norm(r"D:\AppProxy\apps\chrome-alpha\data")
    b = procwatch._norm("d:/appproxy/apps/chrome-alpha/data/")
    assert a == b
    assert a.endswith("chrome-alpha\\data")


def test_norm_keeps_drive_root():
    # 盘符根不能把反斜杠削掉，否则 C:\ 会变成 "c:"
    assert procwatch._norm("C:\\") == "c:\\"


# --------------------------------------------------------------------------- 匹配规则
def test_chromium_matches_by_user_data_dir():
    """同一 exe 的两个档案，靠各自的数据目录区分。"""
    snap = procwatch.Snapshot()
    a = _chrome("a", r"D:\AppProxy\apps\a\data")
    b = _chrome("b", r"D:\AppProxy\apps\b\data")
    snap.by_data_dir[procwatch._norm(r"D:\AppProxy\apps\a\data")] = [111]
    assert snap.alive(a) is True
    assert snap.alive(b) is False


def test_chromium_never_falls_back_to_exe():
    """Chromium 档案不能按 exe 匹配，否则三个档案会互相误判成全在跑。"""
    snap = procwatch.Snapshot()
    snap.by_exe[procwatch._norm(CHROME)] = [111, 222]
    assert snap.alive(_chrome("a", r"D:\AppProxy\apps\a\data")) is False


def test_generic_matches_by_exe():
    snap = procwatch.Snapshot()
    snap.by_exe[procwatch._norm(r"C:\Windows\System32\notepad.exe")] = [333]
    assert snap.alive(_generic("n")) is True
    assert snap.alive(_generic("m", r"C:\Windows\System32\mspaint.exe")) is False


def test_data_dir_with_spaces_is_not_truncated():
    """路径带空格时 Chrome 把整个参数加引号，值必须完整取出。"""
    cmd = '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ' \
          '"--user-data-dir=D:\\AppProxy\\apps\\my app\\data" --no-first-run'
    got = procwatch.extract_data_dirs(cmd)
    assert got == [r"D:\AppProxy\apps\my app\data"]


def test_data_dir_without_quotes():
    """路径没空格时不加引号，也要能取到。"""
    cmd = "chrome.exe --user-data-dir=D:\\AppProxy\\apps\\a\\data --no-first-run"
    assert procwatch.extract_data_dirs(cmd) == [r"D:\AppProxy\apps\a\data"]


def test_data_dir_missing_returns_empty():
    assert procwatch.extract_data_dirs("chrome.exe --no-first-run") == []


# --------------------------------------------------------------------------- 登记表
def test_ledger_mark_and_unmark(tmp_home):
    settings.ensure_dirs()
    pool._mark("alpha", "a")
    pool._mark("alpha", "b")
    pool._mark("alpha", "a")           # 重复登记不该重复
    assert pool.ledger()["alpha"]["by"] == ["a", "b"]

    pool._unmark("alpha", "a")
    assert pool.ledger()["alpha"]["by"] == ["b"]

    pool._unmark("alpha", "b")         # 最后一个走了，整条登记消失
    assert "alpha" not in pool.ledger()


def test_ledger_force_drop(tmp_home):
    settings.ensure_dirs()
    pool._mark("x", "p")
    pool._unmark("x", drop=True)
    assert pool.ledger() == {}


def test_gc_no_ledger_is_noop(tmp_home):
    """没有登记（都是用户手动起的）时，回收必须什么都不做。"""
    settings.ensure_dirs()
    assert pool.gc() == []


# --------------------------------------------------------------------------- 状态快照
def test_state_shape(tmp_home, monkeypatch):
    settings.ensure_dirs()
    from appproxy.registry import Registry

    reg = Registry()
    reg.lines = [Line(id="alpha", name="示例A", http_port=19999, engine="singbox")]
    reg.save()

    from appproxy.profiles import ProfileStore

    store = ProfileStore()
    store.profiles = [_chrome("a", str(settings.apps_dir() / "a" / "data"))]
    store.profiles[0].line_id = "alpha"
    store.save()

    snap = procwatch.Snapshot()          # 空快照 -> 全部未运行
    monkeypatch.setattr(procwatch, "snapshot", lambda: snap)
    monkeypatch.setattr(pool.coreproc, "port_open", lambda *a, **k: False)

    st = pool.state()
    assert len(st["lines"]) == 1
    assert st["lines"][0]["users"] == ["a"]
    assert st["lines"][0]["running"] is False
    assert st["profiles"][0]["alive"] is False
    assert st["profiles"][0]["proxy"] == "127.0.0.1:19999"
    assert st["kernel_running"] == []


def test_state_direct_profile_has_no_proxy(tmp_home, monkeypatch):
    settings.ensure_dirs()
    from appproxy.profiles import ProfileStore

    store = ProfileStore()
    p = _generic("n")
    p.line_id = ""
    store.profiles = [p]
    store.save()
    monkeypatch.setattr(procwatch, "snapshot", lambda: procwatch.Snapshot())
    st = pool.state()
    assert st["profiles"][0]["proxy"] == ""
    assert st["profiles"][0]["line_name"] == "直连"


# --------------------------------------------------------------------------- 回收判断
def test_gc_keeps_line_when_program_alive(tmp_home, monkeypatch):
    """程序还在跑 -> 内核必须留着。"""
    settings.ensure_dirs()
    from appproxy.profiles import ProfileStore
    from appproxy.registry import Registry

    reg = Registry()
    reg.lines = [Line(id="alpha", name="示例A", http_port=19999, engine="singbox")]
    reg.save()

    store = ProfileStore()
    p = _chrome("a", str(settings.apps_dir() / "a" / "data"))
    p.line_id = "alpha"
    store.profiles = [p]
    store.save()
    pool._mark("alpha", "a")

    snap = procwatch.Snapshot()
    snap.by_data_dir[procwatch._norm(p.data_path())] = [999]
    monkeypatch.setattr(procwatch, "snapshot", lambda: snap)
    monkeypatch.setattr(pool, "GRACE_SECONDS", 0)

    stopped: list = []
    monkeypatch.setattr(pool.coreproc, "stop", lambda cfg: stopped.append(cfg) or 1)

    assert pool.gc() == []
    assert stopped == []
    assert "alpha" in pool.ledger()


def test_gc_stops_line_when_nobody_uses_it(tmp_home, monkeypatch):
    """程序全关了 -> 内核回收，登记清空。"""
    settings.ensure_dirs()
    from appproxy.profiles import ProfileStore
    from appproxy.registry import Registry

    reg = Registry()
    reg.lines = [Line(id="alpha", name="示例A", http_port=19999, engine="singbox")]
    reg.save()

    store = ProfileStore()
    p = _generic("n")
    p.line_id = "alpha"
    store.profiles = [p]
    store.save()
    pool._mark("alpha", "n")

    monkeypatch.setattr(procwatch, "snapshot", lambda: procwatch.Snapshot())
    monkeypatch.setattr(pool, "GRACE_SECONDS", 0)
    stopped: list = []
    monkeypatch.setattr(pool.coreproc, "stop", lambda cfg: stopped.append(cfg) or 1)

    done = pool.gc()
    assert len(done) == 1
    assert done[0]["line"] == "alpha"
    assert stopped, "应该调用了停内核"
    assert pool.ledger() == {}


def test_gc_respects_grace_period(tmp_home, monkeypatch):
    """刚登记的内核不能被宽限期内的回收停掉（程序还没进进程表）。"""
    settings.ensure_dirs()
    from appproxy.profiles import ProfileStore
    from appproxy.registry import Registry

    reg = Registry()
    reg.lines = [Line(id="alpha", name="示例A", http_port=19999, engine="singbox")]
    reg.save()
    store = ProfileStore()
    store.profiles = []
    store.save()
    pool._mark("alpha", "gone")
    monkeypatch.setattr(procwatch, "snapshot", lambda: procwatch.Snapshot())
    stopped: list = []
    monkeypatch.setattr(pool.coreproc, "stop", lambda cfg: stopped.append(cfg) or 1)

    assert pool.gc() == []          # 默认 60 秒宽限
    assert stopped == []


def test_release_keeps_kernel_if_other_program_uses_line(tmp_home, monkeypatch):
    """两个软件共用一条线路：关掉一个，内核要留给另一个。"""
    settings.ensure_dirs()
    from appproxy.profiles import ProfileStore
    from appproxy.registry import Registry

    reg = Registry()
    reg.lines = [Line(id="alpha", name="示例A", http_port=19999, engine="singbox")]
    reg.save()

    a = _generic("a", r"C:\Windows\System32\notepad.exe")
    b = _generic("b", r"C:\Windows\System32\mspaint.exe")
    for p in (a, b):
        p.line_id = "alpha"
    store = ProfileStore()
    store.profiles = [a, b]
    store.save()
    pool._mark("alpha", "a")
    pool._mark("alpha", "b")

    snap = procwatch.Snapshot()
    snap.by_exe[procwatch._norm(b.exe)] = [222]      # 只有 b 还在跑
    monkeypatch.setattr(procwatch, "snapshot", lambda: snap)
    stopped: list = []
    monkeypatch.setattr(pool.coreproc, "stop", lambda cfg: stopped.append(cfg) or 1)

    msg = pool.release(reg, store, a)
    assert "仍有" in msg
    assert stopped == [], "另一个软件还在用，不该停内核"

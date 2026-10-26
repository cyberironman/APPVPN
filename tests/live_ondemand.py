# -*- coding: utf-8 -*-
"""按需起停链路自检（真实进程 + 真实内核）。

验五件事：
    1. 直连档案启动时**不起内核**
    2. 代理档案启动时**只起它那一条线路**
    3. 程序还在跑时，自动回收**不会**把内核停掉
    4. 程序关掉后，自动回收把内核**停掉**
    5. 「关闭」按钮同时收程序和内核

用法：python tests/live_ondemand.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from appproxy import coreproc, pool, profiles, settings  # noqa: E402
from appproxy.registry import Registry  # noqa: E402
from appproxy.webui.api import app  # noqa: E402

PASS, FAIL = "[OK]", "[!!]"
NOTEPAD = r"C:\Windows\System32\notepad.exe"
LINE = "alpha"
PORT = 10801
TAG = "测试·"


def step(msg: str) -> None:
    print(f"\n--- {msg}")


def check(cond: bool, msg: str) -> bool:
    print(f"{PASS if cond else FAIL} {msg}")
    return bool(cond)


def notepad_pids() -> list[int]:
    import psutil
    return [p.pid for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() == "notepad.exe"]


def kill_notepads() -> None:
    import psutil
    for pid in notepad_pids():
        try:
            psutil.Process(pid).kill()
        except Exception:  # noqa: BLE001
            pass


def _unmark_all() -> None:
    for line_id in list(pool.ledger()):
        pool._unmark(line_id, drop=True)


def cleanup() -> None:
    """不管成败，把临时档案、进程、内核、登记全清干净。"""
    reg = Registry().load()
    store = profiles.ProfileStore().load()
    for prof in list(store.profiles):
        if prof.name.startswith(TAG):
            try:
                pool.close_profile(reg, store, prof)
            except Exception:  # noqa: BLE001
                pass
            store.remove(prof.id)
            for p in (prof.icon_path(), settings.icons_dir() / "png" / f"{prof.id}.png"):
                try:
                    if Path(p).is_file():
                        Path(p).unlink()
                except OSError:
                    pass
    store.save()
    for line in reg.lines:
        coreproc.stop(line.config_path())
    _unmark_all()
    kill_notepads()


def main() -> int:
    settings.ensure_dirs()
    ok = True
    c = TestClient(app)

    step("清场：停掉所有内核")
    reg = Registry().load()
    for line in reg.lines:
        coreproc.stop(line.config_path())
    _unmark_all()
    time.sleep(1)
    ok &= check(not coreproc.port_open(PORT), f"端口 {PORT} 已释放")

    step("建两个临时档案（同一个程序，一个走代理、一个直连）")
    ids: dict[str, str] = {}
    for tag, line, name in (("proxy", LINE, TAG + "走代理"), ("direct", "", TAG + "直连")):
        r = c.post("/api/apps/add", json={"exe": NOTEPAD, "name": name, "line_id": line})
        body = r.json()
        ids[tag] = body.get("id", "")
        ok &= check(r.status_code == 200 and bool(ids[tag]),
                    f"添加「{name}」-> id={ids[tag]}")
    store = profiles.ProfileStore().load()
    got = store.get(ids.get("proxy", ""))
    ok &= check(got is not None and got.line_id == LINE, "档案确实绑定了 alpha")

    step("直连档案启动：不该起内核")
    r = c.post(f"/api/profile/{ids['direct']}/start")
    ok &= check(r.status_code == 200, f"启动返回 {r.status_code} {r.json().get('message', '')}")
    time.sleep(2)
    ok &= check(len(notepad_pids()) >= 1, f"记事本进程在跑 ({notepad_pids()})")
    ok &= check(not coreproc.port_open(PORT), f"直连档案没有拉起内核（{PORT} 仍关闭）")
    c.post(f"/api/profile/{ids['direct']}/stop")
    time.sleep(1)

    step("代理档案启动：只起它那一条线路")
    t0 = time.time()
    r = c.post(f"/api/profile/{ids['proxy']}/start")
    ms = int((time.time() - t0) * 1000)
    ok &= check(r.status_code == 200, f"启动返回 {r.status_code}，整条链路耗时 {ms}ms")
    ok &= check(coreproc.port_open(PORT), f"内核已起（端口 {PORT}）")
    ok &= check(not coreproc.port_open(10803), "另一条线路（10803）没被牵连")
    led = pool.ledger()
    ok &= check(LINE in led and ids["proxy"] in led[LINE]["by"],
                f"登记正确：{ {k: v['by'] for k, v in led.items()} }")
    ok &= check(len(notepad_pids()) >= 1, "记事本进程在跑")

    step("自动回收：程序还在跑，内核必须留着")
    old = pool.GRACE_SECONDS
    pool.GRACE_SECONDS = 0
    done = pool.gc()
    ok &= check(not done, f"没有回收任何内核（{done}）")
    ok &= check(coreproc.port_open(PORT), "内核仍然在跑")

    step("程序自己关掉 -> 自动回收生效")
    kill_notepads()
    time.sleep(1.5)
    ok &= check(not notepad_pids(), "记事本已退出")
    done = pool.gc()
    ok &= check(bool(done), f"回收了内核：{done}")
    ok &= check(not coreproc.port_open(PORT), f"端口 {PORT} 已释放")
    ok &= check(not pool.ledger(), f"登记已清空：{pool.ledger()}")
    pool.GRACE_SECONDS = old

    step("「关闭」按钮：同时收程序和内核")
    c.post(f"/api/profile/{ids['proxy']}/start")
    ok &= check(coreproc.port_open(PORT), "再次启动后内核在跑")
    r = c.post(f"/api/profile/{ids['proxy']}/stop")
    msg = r.json().get("message", "")
    time.sleep(1.5)
    ok &= check(r.status_code == 200 and bool(msg), f"关闭返回：{msg}")
    ok &= check(not notepad_pids(), "程序已被结束")
    ok &= check(not coreproc.port_open(PORT), "内核已被释放")
    ok &= check(not pool.ledger(), "登记已清空")

    step("手动起的内核不受自动回收影响")
    c.post(f"/api/line/{LINE}/start")
    ok &= check(coreproc.port_open(PORT), "手动启动内核成功")
    pool.GRACE_SECONDS = 0
    done = pool.gc()
    ok &= check(not done, f"自动回收没碰它（{done}）")
    ok &= check(coreproc.port_open(PORT), "手动起的内核仍在跑")
    pool.GRACE_SECONDS = old

    print("\n" + "=" * 56)
    print("结论：" + ("全部通过" if ok else "有失败项，见上面 [!!]"))
    return 0 if ok else 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        try:
            cleanup()
            print("已清理临时档案与进程")
        except Exception as e:  # noqa: BLE001
            print(f"[!!] 清理失败：{e}")
    sys.exit(code)

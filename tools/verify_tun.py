# -*- coding: utf-8 -*-
"""TUN 实机验证：双击 → 点一次「是」→ 看弹窗结果。

为什么单独做这个工具
    TUN 要建虚拟网卡，必须管理员令牌；而装「最高权限计划任务」这一步在
    某些受限会话里被安全策略直接拦掉（没法自动验证）。所以做成一个双击运行的小工具：
    把能自动做的全做完（生成配置、真校验证、装任务、拉起内核、检查网卡与进程），
    只剩一件事交给人 —— 在 UAC 弹窗上点「是」。

用法
    双击桌面「验证TUN（点一次授权）」，等弹窗。
    结果同时写进 D:\\AppProxy\\logs\\tun-verify.txt，方便事后核对。
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from appproxy import elevate, settings, tunconf, tunmgr  # noqa: E402
from appproxy.registry import Registry  # noqa: E402

REPORT = "tun-verify.txt"
LINES: list[str] = []


def say(line: str = "") -> None:
    LINES.append(line)
    # 用 pythonw 跑时 sys.stdout 是 None，print 会抛；用 python 跑时能看到进度。
    # 两种都要能工作，所以吞掉这个异常。
    try:
        print(line, flush=True)
    except (AttributeError, OSError, ValueError):
        pass


def popup(title: str, text: str, ask: bool = False) -> bool:
    """Windows 原生气泡：pythonw 下没有控制台，只能用对话框说话。

    ask=True 时返回用户是否点了「是」。
    """
    import ctypes

    MB_ICONINFORMATION = 0x40
    MB_ICONWARNING = 0x30
    MB_YESNO = 0x04
    MB_SETFOREGROUND = 0x10000
    IDYES = 6
    flags = MB_SETFOREGROUND | (MB_YESNO if ask else 0)
    flags |= MB_ICONINFORMATION if not ask else MB_ICONWARNING
    rc = ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    return rc == IDYES


def write_report(verdict: str) -> Path:
    d = settings.logs_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / REPORT
    head = [f"=== TUN 实机验证 {time.strftime('%Y-%m-%d %H:%M:%S')} ===", f"结论：{verdict}", ""]
    p.write_text("\n".join(head + LINES) + "\n", encoding="utf-8")
    return p


def core_running() -> list[int]:
    """找出真的在跑 TUN 的那个 sing-box 进程（认 tun.json，别的内核不算）。"""
    try:
        import psutil
    except ImportError:
        return []
    found = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        name = (p.info.get("name") or "").lower()
        if not name.startswith("sing-box"):
            continue
        cl = " ".join(p.info.get("cmdline") or [])
        if "tun.json" in cl:
            found.append(p.pid)
    return found


def nic_present() -> list[str]:
    """虚拟网卡出没出现、拿到什么地址。"""
    try:
        import psutil
    except ImportError:
        return []
    out = []
    try:
        for name, addrs in psutil.net_if_addrs().items():
            if "appproxy" not in name.lower():
                continue
            for a in addrs:
                if getattr(a, "family", None) is not None and "." in str(a.address):
                    out.append(f"{name} {a.address}")
    except Exception:  # noqa: BLE001
        pass
    return out


def main(dry: bool = False) -> int:
    settings.ensure_dirs()

    # ---- 1. 前置条件：必须有线路和节点，否则 TUN 没意义 ----
    reg = Registry().load()
    if not reg.lines:
        write_report("没做成：一条线路都没有")
        if not dry:
            popup("先添加代理", "还没有任何代理线路。\n\n"
                  "先双击桌面「分应用代理管家」→ 设置 → 添加订阅，\n"
                  "然后再回到这里点第二次。")
        return 1
    lines = []
    for line in reg.lines:
        nodes, _ = reg.load_nodes(line)
        lines.append(f"{line.name}（{line.id}）端口 {line.http_port}，{len(nodes)} 个节点")
    say("线路：")
    for x in lines:
        say(f"  - {x}")

    # ---- 2. 生成配置并**真校验**（不通过就是整机断网，不能跳过）----
    try:
        cfg = tunmgr.build()
    except Exception as e:  # noqa: BLE001
        write_report(f"没做成：{e}")
        if not dry:
            popup("配置生成失败", str(e))
        return 1
    say(f"分流：{tunconf.describe(cfg)}")

    path = tunmgr.config_path()
    tunmgr.tunconf_write(cfg, path)
    from appproxy import coreproc

    binary = reg.binary("singbox")
    if binary is None:
        write_report("没做成：找不到 sing-box")
        if not dry:
            popup("找不到 sing-box", "TUN 模式需要 sing-box 内核，但没找到。")
        return 1
    ok, out = coreproc.validate(path, binary, "singbox")
    say(f"配置：{path}")
    say(f"sing-box check：{'通过' if ok else '没通过'}")
    if not ok:
        say(out[:800])
        write_report("没做成：配置校验失败")
        if not dry:
            popup("配置没通过校验", f"sing-box 说：\n\n{out[:600]}\n\n"
                  "这个状态开 TUN 会让你整机断网，所以没有继续。")
        return 1

    if dry:
        say("（--dry：到此为止，没有装提权任务，也没有起内核）")
        rp = write_report("预检通过（dry）")
        print("\n".join(LINES))
        print(f"报告：{rp}")
        return 0

    # ---- 3. 装提权任务（就这一步要你在 UAC 上点「是」）----
    say("等一下屏幕上会弹出「用户账户控制」窗口 —— 请点「是」（只需这一次）。")
    try:
        msg = elevate.install_with_uac(binary, path)
        say(f"提权任务：{msg}")
    except Exception as e:  # noqa: BLE001
        say(f"提权任务：失败 —— {e}")
        write_report(f"没做成：{e}")
        popup("没能拿到管理员授权", str(e))
        return 1

    # ---- 4. 拉起内核 ----
    try:
        elevate.start()
    except Exception as e:  # noqa: BLE001
        say(f"启动内核：失败 —— {e}")
        write_report(f"没做成：{e}")
        popup("内核没起来", str(e))
        return 1
    say("启动内核：已下发")

    # ---- 5. 等它真的建出网卡，再下结论 ----
    pids: list[int] = []
    nics: list[str] = []
    for _ in range(20):                    # 最多等 10 秒
        time.sleep(0.5)
        pids = core_running()
        nics = nic_present()
        if pids and nics:
            break

    say(f"内核进程：{'运行中 PID ' + ', '.join(map(str, pids)) if pids else '没找到 sing-box'}")
    say(f"虚拟网卡：{'、'.join(nics) if nics else '没出现 AppProxy 网卡'}")
    st = tunmgr.status()
    say(f"提权任务状态：{'已装' if st['installed'] else '未装'}，"
        f"内核{'运行中' if st['on'] else '未运行'}")
    say(f"走代理的进程：{'、'.join(st['procs']) if st['procs'] else '（无 —— 先去主界面给软件建档绑线路）'}")

    good = bool(pids and nics)
    verdict = "通过" if good else "失败：内核或网卡没起来"
    rp = write_report(verdict)
    say(f"报告：{rp}")

    if not good:
        elev = elevate.status().get("raw", "")[:500]
        popup("TUN 没起来",
              "内核或虚拟网卡没出现。\n\n"
              f"报告已写到：\n{rp}\n\n"
              f"{elev}")
        return 1

    # ---- 6. 成功：问一句留不留 ----
    keep = popup("TUN 验证通过",
                 "虚拟网卡和内核都起来了，分流规则已生效。\n\n"
                 f"{tunconf.describe(cfg)}\n\n"
                 "现在要保留它继续用吗？\n\n"
                 "  「是」= 保留（TUN 一直在后台跑，系统代理已让路）\n"
                 "  「否」= 立刻关掉并还原系统代理",
                 ask=True)
    if keep:
        say("用户选择：保留运行")
        write_report("通过（保留运行）")
        return 0

    try:
        tunmgr.stop()
        say("已关闭 TUN 并还原系统代理")
    except Exception as e:  # noqa: BLE001
        say(f"关闭失败：{e}")
    write_report("通过（验证后已关闭）")
    return 0


if __name__ == "__main__":
    _dry = "--dry" in sys.argv[1:]
    try:
        sys.exit(main(_dry))
    except Exception:  # noqa: BLE001  任何意外都要留下痕迹，不能静默退出
        tb = traceback.format_exc()
        LINES.append(tb)
        p = write_report("崩了（下面是堆栈）")
        if not _dry:
            popup("验证工具出错", f"{tb[-800:]}\n\n报告：{p}")
        else:
            print(tb)
        sys.exit(2)

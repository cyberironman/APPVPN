# -*- coding: utf-8 -*-
"""核心层命令行：不启动界面也能把一整条代理链路跑起来（也用于自检）。

    python -m appproxy.cli list
    python -m appproxy.cli add --id alpha --name myproxy --sub <订阅地址> --http 10898 --socks 10899
    python -m appproxy.cli update alpha
    python -m appproxy.cli nodes alpha --region US
    python -m appproxy.cli select alpha 3          # 固定用第 3 个节点（mode 自动切 single）
    python -m appproxy.cli mode alpha auto
    python -m appproxy.cli build alpha
    python -m appproxy.cli start alpha | stop alpha | status
    python -m appproxy.cli health alpha
    python -m appproxy.cli ping alpha --top 5

应用档案（步骤 2）—— 同一个程序、多个代理、多个图标：

    python -m appproxy.cli profile add --id chrome-work --name Chrome工作 \\
        --exe "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --line alpha
    python -m appproxy.cli profile list | show chrome-work
    python -m appproxy.cli profile launch chrome-work --dry-run
    python -m appproxy.cli profile icon | shortcut

托盘与系统级开关（步骤 3）：

    python -m appproxy.cli tray --install      # 起托盘，顺便建桌面快捷方式
    python -m appproxy.cli sysproxy status | on alpha | off | toggle
    python -m appproxy.cli autostart status | on | off
    python -m appproxy.cli apps scan | list    # 扫本机已安装程序，给建档挑

订阅 / 排障 / 搬家（v0.6.5）：

    python -m appproxy.cli auto                # 每条线路的订阅更新状态
    python -m appproxy.cli auto --interval 6   # 改周期（--off 关闭，--run 立刻拉）
    python -m appproxy.cli diag                # 诊断包（凭据自动打码）
    python -m appproxy.cli export --with-sub   # 导出配置
    python -m appproxy.cli import cfg.json     # 导入配置（端口重新随机）
"""
from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from appproxy import coreproc, latency, settings, singconf, subscription, xrayconf  # noqa: E402
from appproxy import appscan, autostart, icons, profiles, sysproxy  # noqa: E402
from appproxy import shortcut as shortcutmod  # noqa: E402
from appproxy.models import Line  # noqa: E402
from appproxy.registry import PORT_MIN, Registry  # noqa: E402

OK = "[OK]"
FAIL = "[!!]"


def _reg() -> Registry:
    settings.ensure_dirs()
    return Registry().load()


def _need_xray(reg: Registry) -> Path:
    x = reg.xray
    if x is None:
        raise SystemExit("找不到 xray.exe。用 `set APPPROXY_XRAY=<路径>` 指定，"
                         "或把路径写进 lines.json 的 defaults.xray")
    return x


def _need_binary(reg: Registry, line: Line) -> Path:
    b = reg.binary(line.engine)
    if b is None:
        tip = "third_party\\sing-box\\ 下放 sing-box.exe" if line.engine.startswith("sing") \
            else "设置 APPPROXY_XRAY"
        raise SystemExit(f"线路 {line.id} 用 {line.engine} 引擎，但找不到可执行文件（{tip}）")
    return b


def _resolve_ref(reg: Registry, line: Line, ref: str):
    nodes, _ = reg.load_nodes(line)
    if not nodes:
        nodes = _refresh(reg, line)
    if ref.isdigit():
        idx = int(ref)
        if not 1 <= idx <= len(nodes):
            raise SystemExit(f"序号超范围：1~{len(nodes)}")
        return nodes, nodes[idx - 1]
    for n in nodes:
        if n.uid == ref or n.tag == ref or n.uid.startswith(ref):
            return nodes, n
    raise SystemExit(f"找不到节点：{ref}")


def _refresh(reg: Registry, line: Line):
    if not line.sub_url:
        raise SystemExit(f"线路 {line.id} 没有订阅地址")
    print(f"拉订阅：{line.id} ...")
    nodes, meta = subscription.load(line.sub_url)
    reg.save_nodes(line, nodes, meta)
    print(f"{OK} {len(nodes)} 个节点已缓存 -> {line.nodes_path()}")
    return nodes


# --------------------------------------------------------------------------- 命令
def cmd_list(reg: Registry, a) -> int:
    if not reg.lines:
        print("还没有线路。示例：\n  python -m appproxy.cli add --id alpha --name myproxy "
              "--sub <订阅地址>")
        return 0
    print(f"{'ID':<10}{'名称':<16}{'模式':<8}{'HTTP':<7}{'SOCKS':<7}{'状态':<8}订阅")
    for l in reg.lines:
        st = coreproc.status(l)
        state = "运行中" if st["port_up"] else ("有进程" if st["running"] else "停止")
        sub = (l.sub_url[:42] + "...") if len(l.sub_url) > 45 else l.sub_url
        print(f"{l.id:<10}{l.name:<16}{l.mode:<8}{l.http_port:<7}{l.socks_port or '-':<7}"
              f"{state:<8}{sub}")
    return 0


def cmd_add(reg: Registry, a) -> int:
    if reg.get(a.id):
        raise SystemExit(f"线路 {a.id} 已存在，用 `set` 改参数")
    http_port, socks_port = a.http, a.socks
    if not http_port:
        http_port, socks_port = reg.alloc_ports()
    line = Line(id=a.id, name=a.name or a.id, sub_url=a.sub or "", http_port=http_port,
                socks_port=socks_port, mode=a.mode, health_url=a.health or "",
                engine=a.engine)
    reg.upsert(line)
    reg.save()
    print(f"{OK} 已添加线路 {line.id}（引擎 {line.engine}，http {http_port} / "
          f"socks {socks_port or '-'}，模式 {line.mode}）")
    return 0


def cmd_set(reg: Registry, a) -> int:
    line = reg.require(a.id)
    for field in ("name", "sub", "mode", "health"):
        v = getattr(a, field)
        if v:
            if field == "sub":
                line.sub_url = v
            elif field == "health":
                line.health_url = v
            else:
                setattr(line, field, v)
    if a.http:
        line.http_port = a.http
    if a.socks:
        line.socks_port = a.socks
    if a.engine:
        line.engine = a.engine
    reg.upsert(line)
    reg.save()
    print(f"{OK} 已更新 {line.id}")
    return 0


def cmd_remove(reg: Registry, a) -> int:
    line = reg.require(a.id)
    coreproc.stop(line.config_path())
    reg.remove(a.id)
    reg.save()
    for p in (line.config_path(), line.nodes_path()):
        if p.exists():
            p.unlink()
    print(f"{OK} 已删除线路 {a.id}")
    return 0


def cmd_update(reg: Registry, a) -> int:
    line = reg.require(a.id)
    nodes = _refresh(reg, line)
    by_region: dict[str, int] = {}
    for n in nodes:
        by_region[n.region] = by_region.get(n.region, 0) + 1
    top = sorted(by_region.items(), key=lambda kv: -kv[1])
    print("按地区：" + "  ".join(f"{k}×{v}" for k, v in top))
    return 0


def cmd_nodes(reg: Registry, a) -> int:
    line = reg.require(a.id)
    nodes, meta = reg.load_nodes(line)
    if a.refresh or not nodes:
        nodes = _refresh(reg, line)
    if a.region:
        nodes = [n for n in nodes if n.region.upper() == a.region.upper()]
    if not nodes:
        print("没有匹配的节点")
        return 1
    for i, n in enumerate(nodes[: a.limit], 1):
        print(f"{n.label(i)}  uid={n.uid}")
    if len(nodes) > a.limit:
        print(f"... 共 {len(nodes)} 个（用 --limit 调整）")
    return 0


def cmd_select(reg: Registry, a) -> int:
    line = reg.require(a.id)
    nodes, node = _resolve_ref(reg, line, a.ref)
    line.mode = "single"
    line.node_key = node.uid
    reg.upsert(line)
    reg.save()
    print(f"{OK} {line.id} 固定为：{node.label()}（uid={node.uid}）")
    print("提示：订阅按天轮换时，固定单节点可能踩到慢节点；需要自动择优请 `mode <id> auto`")
    return 0


def cmd_mode(reg: Registry, a) -> int:
    line = reg.require(a.id)
    line.mode = a.mode
    reg.upsert(line)
    reg.save()
    print(f"{OK} {line.id} 模式 = {line.mode}")
    return 0


def cmd_build(reg: Registry, a) -> int:
    line = reg.require(a.id)
    nodes, _ = reg.load_nodes(line)
    if not nodes:
        nodes = _refresh(reg, line)
    usable = None
    binary = reg.binary(line.engine)
    if getattr(a, "probe", False) and binary:
        print(f"先实测筛选节点（{len(nodes)} 个）...")
        res = latency.measure(nodes, binary,
                              line.health_url or settings.DEFAULT_HEALTH_URL,
                              engine=line.engine)
        usable = {uid for uid, ms in res.items() if ms is not None}
        print(f"{OK if usable else FAIL} 实测可用 {len(usable)}/{len(nodes)}")

    if (line.engine or "").lower().startswith("sing"):
        cfg = singconf.build(line, nodes, usable=usable)
    else:
        cfg = xrayconf.build(line, nodes)
    path = xrayconf.write(cfg, line.config_path())
    print(f"{OK} 配置已生成：{path}（引擎 {line.engine}，{len(nodes)} 个节点，模式 {line.mode}）")
    if binary:
        ok, msg = coreproc.validate(path, binary, line.engine)
        print(f"{OK if ok else FAIL} 配置校验：{'通过' if ok else '失败'}")
        if not ok:
            print(msg)
        return 0 if ok else 1
    print(f"[--] 没找到 {line.engine} 可执行文件，跳过校验")
    return 0


def cmd_start(reg: Registry, a) -> int:
    """起内核。

    刻意走 `pool.ensure_core`，而不是自己判断「配置在不在」再 `coreproc.start`：
    配置可能是上一代留下的（端口体检换过号、订阅在后台更新过节点），
    这两件事只有 ensure_core 里的两道闸会处理。命令行和界面走同一条路，
    才不会出现「界面能起、命令行起不来」这种鬼问题。
    """
    from appproxy import pool

    line = reg.require(a.id)
    msg = pool.ensure_core(reg, line)
    ok, code, ms = coreproc.probe_via_proxy(line.http_port, settings.DEFAULT_HEALTH_URL)
    print(f"{OK} {msg}")
    print(f"{OK if ok else FAIL} 链路探测 {code} / {ms}ms")
    return 0 if ok else 1


def cmd_stop(reg: Registry, a) -> int:
    line = reg.require(a.id)
    n = coreproc.stop(line.config_path())
    print(f"{OK} 已停止 {line.id}（结束 {n} 个进程）")
    return 0


def cmd_status(reg: Registry, a) -> int:
    targets = [reg.require(a.id)] if a.id else reg.lines
    if not targets:
        print("还没有线路")
        return 0
    for line in targets:
        st = coreproc.status(line)
        state = "运行中" if st["port_up"] else ("有进程但端口未监听" if st["running"] else "停止")
        print(f"[{line.id}] {line.name}  模式={line.mode}  {state}")
        print(f"    http {st['http_port']}  socks {st['socks_port'] or '-'}  "
              f"PID {st['pids'] or '-'}")
        print(f"    config {st['config']}{'' if st['config_exists'] else '（尚未生成）'}")
    return 0


def cmd_health(reg: Registry, a) -> int:
    line = reg.require(a.id)
    if not coreproc.port_open(line.http_port):
        print(f"{FAIL} 端口 {line.http_port} 未监听，先 start")
        return 1
    ok, code, ms = coreproc.probe_via_proxy(line.http_port, a.url or settings.DEFAULT_HEALTH_URL,
                                            timeout=a.timeout)
    print(f"{OK if ok else FAIL} {line.id} -> {code or '超时'} / {ms}ms")
    return 0 if ok else 1


def cmd_ping(reg: Registry, a) -> int:
    line = reg.require(a.id)
    nodes, _ = reg.load_nodes(line)
    if a.refresh or not nodes:
        nodes = _refresh(reg, line)
    if a.region:
        nodes = [n for n in nodes if n.region.upper() == a.region.upper()]
    if not nodes:
        print("没有可测的节点")
        return 1
    xray = _need_binary(reg, line)
    print(f"正在测速 {len(nodes)} 个节点（引擎 {line.engine}，目标 {settings.DEFAULT_HEALTH_URL}）...")
    result = latency.measure(nodes, xray, settings.DEFAULT_HEALTH_URL, timeout=a.timeout,
                             engine=line.engine)
    ranked = latency.rank(nodes, result)
    good = sum(1 for _, ms in ranked if ms is not None)
    for i, (n, ms) in enumerate(ranked[: a.top], 1):
        print(f"{i:>2}. {ms if ms is not None else '  不通':>6}ms  {n.label()}  uid={n.uid}")
    print(f"{OK} 通了 {good}/{len(ranked)}")
    if a.select and ranked and ranked[0][1] is not None:
        line.mode, line.node_key = "single", ranked[0][0].uid
        reg.upsert(line)
        reg.save()
        print(f"{OK} 已把最快的节点设为 {line.id} 的固定节点")
    return 0 if good else 1


def cmd_xray(reg: Registry, a) -> int:
    import subprocess
    print(f"用户目录: {settings.home()}")
    for engine in ("singbox", "xray"):
        b = reg.binary(engine)
        if b is None:
            print(f"{FAIL} {engine}: 未找到")
            continue
        try:
            out = subprocess.run([str(b), "version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=10)
            ver = (out.stdout or out.stderr).strip().splitlines()
            print(f"{OK} {engine}: {b}")
            print(f"     {ver[0] if ver else ''}")
        except Exception as e:  # noqa: BLE001
            print(f"{FAIL} {engine}: {b}（读版本失败：{e}）")
    return 0


def cmd_ports(reg: Registry, a) -> int:
    print(f"{'端口':<8}{'占用':<6}{'线路'}")
    owner = {}
    for l in reg.lines:
        if l.http_port:
            owner[l.http_port] = f"{l.id} (http)"
        if l.socks_port:
            owner[l.socks_port] = f"{l.id} (socks)"
    for port in range(a.start, a.start + a.count):
        used = owner.get(port, "")
        try:
            with socket.socket() as s:
                s.bind(("127.0.0.1", port))
                busy = False
        except OSError:
            busy = True
        if busy or used:
            print(f"{port:<8}{'占用' if busy else '空闲':<6}{used}")
    return 0


# --------------------------------------------------------------------------- 应用档案
def _pstore() -> profiles.ProfileStore:
    settings.ensure_dirs()
    return profiles.ProfileStore().load()


def _check_line(reg: Registry, line_id: str) -> Line:
    if not line_id:
        raise SystemExit("必须指定 --line（不带 --line 的档案是直连，请用 profile add --direct）")
    return reg.require(line_id)


def _publish(store: profiles.ProfileStore, prof: profiles.AppProfile, a) -> list[Path]:
    """生成图标 + 快捷方式。失败只警告，不回滚档案本身。"""
    made: list[Path] = []
    if not getattr(a, "no_icon", False):
        if icons.available():
            try:
                made.append(icons.render(prof))
            except Exception as e:  # noqa: BLE001
                print(f"[--] 图标生成失败（{type(e).__name__}: {e}）")
        else:
            print("[--] 没装 Pillow，跳过图标生成（快捷方式会用程序原图标）")
    if not getattr(a, "no_shortcut", False):
        try:
            made.extend(shortcutmod.install(prof))
        except Exception as e:  # noqa: BLE001
            print(f"[--] 快捷方式生成失败（{type(e).__name__}: {e}）")
    return made


def cmd_profile_list(reg: Registry, a) -> int:
    store = _pstore()
    if not store.profiles:
        print("还没有应用档案。示例：\n"
              "  python -m appproxy.cli profile add --id chrome-work --name Chrome工作 "
              "--exe \"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" --line alpha")
        return 0
    print(f"{'ID':<16}{'名称':<18}{'类型':<10}{'线路':<12}{'快捷方式'}")
    for p in store.profiles:
        line = p.line_id or "直连"
        lnk = "有" if (p.shortcut and Path(p.shortcut).is_file()) else "无"
        print(f"{p.id:<16}{p.name:<18}{p.resolved_kind:<10}{line:<12}{lnk}")
    return 0


def cmd_profile_add(reg: Registry, a) -> int:
    store = _pstore()
    if a.direct:
        line_id = ""
    else:
        line_id = _check_line(reg, a.line).id

    exe = Path(a.exe)
    if not exe.is_file():
        print(f"{FAIL} 程序不存在：{exe}")
        return 1

    pid = a.id or store.unique_id(exe.stem)
    if store.get(pid):
        raise SystemExit(f"档案 {pid} 已存在，用 profile set 改参数")

    prof = profiles.AppProfile(id=pid, name=a.name or exe.stem, exe=str(exe),
                               line_id=line_id, kind=a.kind, url=a.url or "",
                               data_dir=a.data_dir or "", args=a.arg or [])
    store.upsert(prof)
    made = _publish(store, prof, a)
    store.save()
    print(f"{OK} 已建档案 {prof.id}（{prof.name}，{prof.resolved_kind}，"
          f"线路 {line_id or '直连'}）")
    for m in made:
        print(f"     {m}")
    return 0


def cmd_profile_set(reg: Registry, a) -> int:
    store = _pstore()
    prof = store.require(a.id)
    if a.name:
        prof.name = a.name
    if a.exe:
        if not Path(a.exe).is_file():
            print(f"{FAIL} 程序不存在：{a.exe}")
            return 1
        prof.exe = a.exe
    if a.direct:
        prof.line_id = ""
    elif a.line:
        prof.line_id = _check_line(reg, a.line).id
    if a.kind:
        prof.kind = a.kind
    if a.url is not None:
        prof.url = a.url
    if a.data_dir is not None:
        prof.data_dir = a.data_dir
    if a.arg:
        prof.args = a.arg
    store.upsert(prof)
    made = _publish(store, prof, a)
    store.save()
    print(f"{OK} 已更新档案 {prof.id}（线路 {prof.line_id or '直连'}）")
    for m in made:
        print(f"     {m}")
    return 0


def cmd_profile_remove(reg: Registry, a) -> int:
    store = _pstore()
    prof = store.require(a.id)
    n = shortcutmod.remove(prof)
    if a.data:
        import shutil
        d = prof.data_path()
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            print(f"{OK} 已删除数据目录 {d}")
    store.remove(a.id)
    store.save()
    print(f"{OK} 已删除档案 {a.id}（清掉 {n} 个快捷方式）")
    return 0


def cmd_profile_show(reg: Registry, a) -> int:
    store = _pstore()
    prof = store.require(a.id)
    line = reg.get(prof.line_id) if prof.line_id else None
    p = profiles.plan(prof, line)
    print(f"档案     {prof.id}  ({prof.name})")
    print(f"程序     {prof.exe}{'' if prof.exists() else '  [!! 不存在]'}")
    print(f"类型     {p.kind}")
    print(f"线路     {prof.line_id or '直连'}"
          f"{'' if line or not prof.line_id else '  [!! 线路不存在]'}")
    print(f"数据目录 {prof.data_path()}")
    print(f"代理     {p.proxy or '不使用代理'}")
    print(f"图标     {prof.icon_path()}{'' if prof.icon_path().is_file() else '  [未生成]'}")
    print(f"快捷方式 {prof.shortcut or '未生成'}")
    print(f"启动命令 {p.command_line()}")
    for k, v in p.env.items():
        print(f"    env {k}={v or '<清空>'}")
    return 0


def cmd_profile_launch(reg: Registry, a) -> int:
    from appproxy import launcher
    return launcher.main(["--profile", a.id] + (["--dry-run"] if a.dry_run else [])
                         + (["--no-core"] if a.no_core else []))


def cmd_profile_icon(reg: Registry, a) -> int:
    store = _pstore()
    prof = store.require(a.id) if a.id else None
    targets = [prof] if prof else store.profiles
    if not targets:
        print("还没有应用档案")
        return 0
    for p in targets:
        path = icons.render(p)
        print(f"{OK} {p.id} -> {path}")
    store.save()
    return 0


def cmd_profile_shortcut(reg: Registry, a) -> int:
    store = _pstore()
    prof = store.require(a.id) if a.id else None
    targets = [prof] if prof else store.profiles
    if not targets:
        print("还没有应用档案")
        return 0
    for p in targets:
        if not p.icon_path().is_file() and icons.available():
            icons.render(p)
        for m in shortcutmod.install(p, desktops=not a.no_desktop):
            print(f"{OK} {m}")
    store.save()
    return 0


# --------------------------------------------------------------------------- 托盘 / 系统代理 / 自启 / 扫描
def cmd_tray(reg: Registry, a) -> int:
    from appproxy import tray
    if a.quit:
        try:
            print(f"{OK} 已通知托盘退出（{tray.send_command('quit')}）")
        except OSError:
            print("[--] 没有正在运行的托盘")
        return 0
    argv: list[str] = []
    if a.install:
        argv.append("--install")
    if a.warm:
        argv.append("--warm")
    return tray.main(argv)


def cmd_gui(reg: Registry, a) -> int:
    """打开主界面（列表 + 详情那种）。--view settings 可直达设置页。"""
    from appproxy import gui
    argv: list[str] = []
    if getattr(a, "debug", False):
        argv.append("--debug")
    view = getattr(a, "view", "") or ""
    if view and view != "profile":
        argv += ["--view", view]
    return gui.main(argv)


def cmd_sysproxy(reg: Registry, a) -> int:
    if a.action == "status":
        st = sysproxy.status()
        print(f"系统代理：{st['text']}")
        print(f"  备份：{'有（关闭时会还原）' if st['backup'] else '无'}")
        for l in reg.lines:
            hit = "  ← 当前" if sysproxy.matches_port(st, l.http_port) else ""
            print(f"  {'✓' if sysproxy.matches_port(st, l.http_port) else ' '} "
                  f"{l.name}（{l.http_port}）{hit}")
        return 0
    if a.action == "off":
        print(f"{OK} {sysproxy.describe(sysproxy.disable())}")
        return 0
    line = reg.require(a.line) if a.line else (reg.lines[0] if reg.lines else None)
    if line is None:
        raise SystemExit("没有线路可用")
    if not coreproc.port_open(line.http_port):
        print(f"{FAIL} 线路 {line.id} 端口 {line.http_port} 没在监听，先 start 或让托盘拉起来")
        return 1
    if a.action == "toggle":
        st = sysproxy.toggle(line.http_port)
    else:
        st = sysproxy.enable(line.http_port)
    print(f"{OK} {sysproxy.describe(st)}")
    return 0


def cmd_autostart(reg: Registry, a) -> int:
    if a.action == "status":
        st = autostart.status()
        print(f"开机自启：{'已开启' if st['enabled'] else '未开启'}")
        print(f"  快捷方式 {st['lnk']}")
        return 0
    if a.action == "on":
        print(f"{OK} {autostart.enable()}")
    else:
        print(f"{OK} 已关闭" if autostart.disable() else "本来就是关的")
    return 0


def cmd_apps(reg: Registry, a) -> int:
    items = appscan.load_cache() if a.action == "list" else appscan.cached_or_scan(refresh=True)
    if not items:
        print("没有扫描结果，先 `apps scan`")
        return 1
    by_group: dict[str, int] = {}
    for it in items:
        by_group[it["group"]] = by_group.get(it["group"], 0) + 1
    print(f"共 {len(items)} 个可启动程序：" +
          "  ".join(f"{k}×{v}" for k, v in by_group.items()))
    for it in items[: a.limit]:
        print(f"  [{it['group']}] {it['name']:<24}{it['exe']}")
    if len(items) > a.limit:
        print(f"  ... 共 {len(items)} 个（--limit 调整）")
    print("\n建档：python -m appproxy.cli profile add --id <id> --name <名字> "
          "--exe \"<exe 路径>\" --line <线路>")
    return 0


# --------------------------------------------------------------------------- 入口
def cmd_tun(reg: Registry, a) -> int:
    from appproxy import elevate, tunconf, tunmgr
    if a.action == "status":
        st = tunmgr.status()
        print(f"TUN：{'运行中' if st['on'] else '未运行'}")
        print(f"  提权任务：{'已装' if st['installed'] else '未装（第一次 start 会装，需要管理员）'}")
        print(f"  配置：{st['config']}")
        print(f"  规则：{st['count']} 个进程走代理，其余直连")
        for p in st["procs"]:
            print(f"    - {p}")
        return 0
    if a.action == "start":
        print(f"{OK} {tunmgr.start()}")
        return 0
    if a.action == "stop":
        print(f"{OK} {tunmgr.stop()}")
        return 0
    if a.action == "remove":
        print(f"{OK} {elevate.delete()}")
        return 0
    binary = reg.binary("singbox")
    if binary is None:
        print(f"{FAIL} 找不到 sing-box")
        return 1
    cfg = tunmgr.build()
    path = tunmgr.tunconf_write(cfg, tunmgr.config_path())
    ok, out = coreproc.validate(path, binary, "singbox")
    print(f"配置：{path}（{tunconf.describe(cfg)}）")
    if not ok:
        print(f"{FAIL} 校验没通过：{out}")
        return 1
    print(f"{OK} {elevate.install(binary, path)}")
    return 0


def cmd_appdb(reg: Registry, a) -> int:
    from appproxy import appdb
    if a.action == "summary":
        data = appdb.load()
        print(f"清单版本 {data.get('version')}（{data.get('updated')}）")
        print(f"  共 {appdb.summary()}")
        for g, desc in appdb.groups().items():
            print(f"  · {g}：{desc}")
        print("\n用法：appdb list | appdb find chrome.exe | appdb update <清单网址>")
        return 0
    if a.action == "update":
        if not a.value:
            print(f"{FAIL} 用法：appdb update <清单网址>")
            return 1
        print(f"{OK} {appdb.update_from(a.value)}")
        return 0
    if a.action == "find":
        if not a.value:
            print(f"{FAIL} 用法：appdb find chrome.exe")
            return 1
        hit = appdb.find(a.value)
        if hit is None:
            print(f"{OK} {a.value} 不在清单里 —— 按「不是明显需要代理」处理")
            return 0
        print(f"{OK} {hit['name']}（{hit.get('group')}，{hit.get('level')}）")
        print(f"  为什么：{hit.get('why')}")
        return 0
    for item in appdb.entries()[: a.limit]:
        exe = ",".join(item.get("exe") or []) or "（仅网页版）"
        print(f"  [{(item.get('level') or ''):5}] {item['name']:<22}{exe:<26}{item.get('why', '')}")
    if len(appdb.entries()) > a.limit:
        print(f"  ... 共 {len(appdb.entries())} 条（--limit 调整）")
    return 0


def cmd_auto(reg: Registry, a) -> int:
    """订阅自动更新：看状态 / 改间隔 / 立刻拉一遍。

    自动更新本身跑在托盘后台（见 autosub.run_due），这条命令是给人手动检查用的
    —— 「我的订阅到底还更不更新」这个问题必须能被一句话回答，不能让人去翻日志。
    """
    from appproxy import autosub

    changed = False
    if a.off:
        reg.defaults["sub_interval_h"] = 0
        changed = True
    elif a.on:
        reg.defaults["sub_interval_h"] = autosub.DEFAULT_INTERVAL_H
        changed = True
    elif a.interval is not None:
        n = int(a.interval)
        if n <= 0:
            raise SystemExit("间隔必须大于 0；想彻底关掉请用 auto --off")
        if a.line:
            line = reg.require(a.line)
            line.sub_interval_h = n
            reg.upsert(line)
        else:
            reg.defaults["sub_interval_h"] = n
        changed = True
    if changed:
        reg.save()
        print(f"{OK} 已更新订阅自动更新设置")

    if a.run:
        rows = autosub.run_due(reg, limit=99)
        if not rows:
            print("没有到期的线路（都是最新的）")
        for r in rows:
            if r.get("ok"):
                msg = f"{OK} {r['line']}：{r['count']} 个节点"
                if r.get("changed"):
                    msg += "（节点有变化，内核在跑时重启后生效）" if r.get("running") else "（节点有变化）"
                print(msg)
            else:
                print(f"{FAIL} {r['line']}：{r.get('error')}")
        reg = _reg()                       # 重新读，下面展示用的是最新状态

    if not reg.lines:
        print("还没有线路")
        return 0
    default = autosub.global_interval(reg)
    print(f"全局间隔：{default if default else '已关闭'}（小时）")
    for row in autosub.status(reg):
        flag = "到期" if row["due"] else "  "
        print(f"  [{flag}] {row['id']:<12} {row['text']}")
    if a.stale_only:
        return 0
    return 0


def cmd_diag(reg: Registry, a) -> int:
    """打一个诊断包：日志 + 配置 + 体检，用户报障不用打字。"""
    from appproxy import diag

    out = Path(a.out) if a.out else None
    path = diag.build(out, reg=reg)
    info = diag.verify(path)
    print(f"{OK} 诊断包已生成：{path}")
    print(f"     大小 {info['size'] / 1024:.1f} KB")
    if not info["clean"]:
        print(f"{FAIL} 包里有 {len(info['hits'])} 处疑似未打码的凭据，先别外发：")
        for h in info["hits"][:10]:
            print(f"     {h}")
        return 1
    print("     已自检：订阅地址、节点 uuid、密码均已打码，可直接贴到 issue")
    if a.open:
        try:
            import os
            os.startfile(str(path.parent))          # noqa: S606  本机桌面打开目录
        except OSError:
            pass
    return 0


def cmd_export(reg: Registry, a) -> int:
    """把线路 + 档案导出成一份文件（换电脑用）。"""
    from appproxy import transfer
    from appproxy.profiles import ProfileStore

    store = ProfileStore().load()
    bundle = transfer.export_bundle(reg, store, include_secrets=a.with_sub)
    path = transfer.dump(bundle, Path(a.out) if a.out else None)
    print(f"{OK} 已导出 {len(bundle['lines'])} 条线路、{len(bundle['profiles'])} 个档案")
    print(f"     文件：{path}")
    if a.with_sub:
        print(f"{FAIL} 这份文件里有**订阅地址**，别公开分享、别提交到仓库")
    else:
        print("     未包含订阅地址：换电脑后要在每条线路上重新填一次")
    return 0


def cmd_import(reg: Registry, a) -> int:
    """把导出的配置文件合并进本机（端口重新随机分配）。"""
    from appproxy import transfer
    from appproxy.profiles import ProfileStore

    try:
        bundle = transfer.load(Path(a.file))
    except ValueError as e:
        print(f"{FAIL} {e}")
        return 1
    if not bundle.get("contains_secrets"):
        print("[!] 这份配置不含订阅地址，导入后需要自己补订阅")
    store = ProfileStore().load()
    r = transfer.import_bundle(reg, store, bundle, fetch_sub=a.fetch)
    print(transfer.summary_text(r))
    print(f"\n{OK} 导入完成。端口是重新随机分配的（每台机器各自一套）")
    return 0


def cmd_doctor(reg: Registry, a) -> int:
    """环境体检 —— 用户报「打不开」时，先让他跑这一条，比问十个问题快。

    报告同时写一份到 logs 目录：打包后的 exe 是无控制台窗口的，用户在 cmd 里
    能不能看到输出取决于终端，但**文件一定拿得到**，好让他发给别人看。
    """
    from appproxy import diag, preflight

    if a.fix:
        r = preflight.fix(a.fix)
        print(f"{OK if r.get('ok') else FAIL} {r.get('message')}")
        if r.get("url"):
            print(f"     地址：{r['url']}")
        return 0 if r.get("ok") else 1

    # 报告正文统一由 diag.doctor_text() 生成：诊断包里放的是同一份文本，
    # 两处各写一套的话，早晚会出现「doctor 说没问题、包里说有问题」。
    text = diag.doctor_text()
    print(text, end="")
    blocks = [i for i in preflight.report() if i["level"] == "block"]
    try:
        settings.logs_dir().mkdir(parents=True, exist_ok=True)
        out = settings.logs_dir() / "doctor.txt"
        out.write_text(text, encoding="utf-8")
        print(f"\n     报告已存：{out}")
    except OSError:
        pass
    return 1 if blocks else 0


def cmd_cleanup(reg: Registry, a) -> int:
    """上线前把私人代理清干净。

    为什么要单独一个命令：开发机上一定有真实订阅（含 token）和真实节点缓存，
    这些一旦跟着安装包发出去，等于把用户的机场账号公开。清理必须可重复执行、
    必须先备份、必须看得见删了什么。
    """
    import shutil
    import time

    from appproxy import profiles

    data = settings.data_dir()
    store = profiles.ProfileStore().load()
    lines = reg.lines

    targets: list[Path] = []
    targets.append(settings.registry_path())
    targets += [l.nodes_path() for l in lines]
    targets += [l.config_path() for l in lines]
    targets.append(settings.runtime_dir() / "tun.json")
    targets.append(settings.runtime_dir() / "tun-state.json")
    targets += [l for l in settings.shortcuts_dir().glob("*.lnk")] if settings.shortcuts_dir().is_dir() else []

    tied = [p for p in store.profiles if p.line_id]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = settings.home() / f"backup-cleanup-{stamp}"

    print(f"将清理 {len(lines)} 条线路、{len(tied)} 个绑了线路的档案、"
          f"{len(targets)} 个文件")
    for l in lines:
        print(f"  · 线路 {l.id}（{l.name}）：{l.sub_url[:60] or '（无订阅地址）'}")
    if not a.yes:
        print(f"\n{OK} 这只是预演。真执行加 --yes（会先备份到 {backup}）")
        return 0

    backup.mkdir(parents=True, exist_ok=True)
    for f in (settings.registry_path(), settings.profiles_path()):
        try:
            if Path(f).is_file():
                shutil.copy2(f, backup / Path(f).name)
        except OSError as e:
            print(f"{FAIL} 备份失败，停下：{e}")
            return 1
    print(f"{OK} 已备份原始数据 → {backup}")

    for f in targets:
        try:
            if Path(f).is_file():
                Path(f).unlink()
        except OSError as e:  # noqa: PERF203
            print(f"  [--] 删不掉 {f}：{e}")

    # 档案保留（用户自己装了什么软件不算隐私），只把绑定线路清空
    for p in tied:
        p.line_id = ""
    store.save()

    reg.lines = []
    reg.save()
    print(f"{OK} 线路、节点缓存、生成的配置、快捷方式已清空；档案改成「直连」")
    print("提醒：")
    print("  1. TUN 提权任务还在 —— 需要时执行 `tun remove`")
    print("  2. dist/ 安装包如果已经打过，要用清理后的数据重新打包")
    print("  3. 检查 third_party 里的内核 exe 是否还在（这个要留着）")
    return 0


def cmd_elevate(a) -> int:
    """内部入口：提权后的子进程走这里装 TUN 计划任务。

        AppProxy.exe elevate install --binary ... --config ...
        源码模式等价：python -m appproxy.elevate install --binary ... --config ...

    不给用户直接用，所以 help 里隐藏。
    """
    from . import elevate
    return elevate.main(list(a.rest))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="appproxy", description="分应用代理管家 · 核心层")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出线路").set_defaults(fn=cmd_list)

    pa = sub.add_parser("add", help="新增线路")
    pa.add_argument("--id", required=True)
    pa.add_argument("--name", default="")
    pa.add_argument("--sub", default="", help="订阅地址（可留空稍后 set）")
    pa.add_argument("--http", type=int, default=0)
    pa.add_argument("--socks", type=int, default=0)
    pa.add_argument("--mode", choices=("auto", "single"), default="auto")
    pa.add_argument("--health", default="")
    pa.add_argument("--engine", choices=("singbox", "xray"), default="singbox")
    pa.set_defaults(fn=cmd_add)

    ps = sub.add_parser("set", help="修改线路参数")
    ps.add_argument("id")
    ps.add_argument("--name")
    ps.add_argument("--sub")
    ps.add_argument("--http", type=int)
    ps.add_argument("--socks", type=int)
    ps.add_argument("--mode", choices=("auto", "single"))
    ps.add_argument("--health")
    ps.add_argument("--engine", choices=("singbox", "xray"))
    ps.set_defaults(fn=cmd_set)

    pr = sub.add_parser("remove", help="删除线路")
    pr.add_argument("id")
    pr.set_defaults(fn=cmd_remove)

    pu = sub.add_parser("update", help="拉取/更新订阅并缓存节点")
    pu.add_argument("id")
    pu.set_defaults(fn=cmd_update)

    pn = sub.add_parser("nodes", help="列出节点")
    pn.add_argument("id")
    pn.add_argument("--region")
    pn.add_argument("--limit", type=int, default=20)
    pn.add_argument("--refresh", action="store_true")
    pn.set_defaults(fn=cmd_nodes)

    psel = sub.add_parser("select", help="固定使用某个节点（序号或 uid）")
    psel.add_argument("id")
    psel.add_argument("ref")
    psel.set_defaults(fn=cmd_select)

    pm = sub.add_parser("mode", help="切换 auto / single")
    pm.add_argument("id")
    pm.add_argument("mode", choices=("auto", "single"))
    pm.set_defaults(fn=cmd_mode)

    pb = sub.add_parser("build", help="生成内核配置并校验")
    pb.add_argument("id")
    pb.add_argument("--probe", action="store_true", help="先实测，只用通的节点")
    pb.set_defaults(fn=cmd_build)

    for name, fn, help_text in (("start", cmd_start, "启动内核"),
                                ("stop", cmd_stop, "停止内核")):
        q = sub.add_parser(name, help=help_text)
        q.add_argument("id")
        q.set_defaults(fn=fn)

    pst = sub.add_parser("status", help="查看状态")
    pst.add_argument("id", nargs="?")
    pst.set_defaults(fn=cmd_status)

    ph = sub.add_parser("health", help="通过线路请求一次，验证是否通")
    ph.add_argument("id")
    ph.add_argument("--url", default="")
    ph.add_argument("--timeout", type=float, default=8.0)
    ph.set_defaults(fn=cmd_health)

    pp = sub.add_parser("ping", help="节点测速（并发，给每个节点单独入口）")
    pp.add_argument("id")
    pp.add_argument("--top", type=int, default=10)
    pp.add_argument("--region")
    pp.add_argument("--timeout", type=float, default=8.0)
    pp.add_argument("--refresh", action="store_true")
    pp.add_argument("--select", action="store_true", help="把最快的设为固定节点")
    pp.set_defaults(fn=cmd_ping)

    sub.add_parser("bin", help="显示两个内核的路径与版本").set_defaults(fn=cmd_xray)

    pports = sub.add_parser("ports", help="看端口占用")
    # 默认从本程序的随机段起点开始看，不再默认扫 108xx 祖传段。
    pports.add_argument("--start", type=int, default=PORT_MIN)
    pports.add_argument("--count", type=int, default=40)
    pports.set_defaults(fn=cmd_ports)

    # ------------------------------------------------------------ 应用档案
    pprof = sub.add_parser("profile", help="应用档案：一个程序 + 一条线路 + 一个图标")
    psub = pprof.add_subparsers(dest="pcmd", required=True)

    psub.add_parser("list", help="列出应用档案").set_defaults(fn=cmd_profile_list)

    pap = psub.add_parser("add", help="新建应用档案（并生成图标与快捷方式）")
    pap.add_argument("--id", default="")
    pap.add_argument("--name", default="")
    pap.add_argument("--exe", required=True)
    pap.add_argument("--line", default="", help="绑定的线路 id")
    pap.add_argument("--direct", action="store_true", help="不挂代理（直连档案）")
    pap.add_argument("--kind", choices=("auto", "chromium", "generic"), default="auto")
    pap.add_argument("--url", default="", help="启动后打开的地址")
    pap.add_argument("--arg", action="append", help="额外启动参数，可重复")
    pap.add_argument("--data-dir", default="", help="自定义数据目录")
    pap.add_argument("--no-icon", action="store_true")
    pap.add_argument("--no-shortcut", action="store_true")
    pap.set_defaults(fn=cmd_profile_add)

    pps = psub.add_parser("set", help="修改档案（改完自动重做图标与快捷方式）")
    pps.add_argument("id")
    pps.add_argument("--name")
    pps.add_argument("--exe")
    pps.add_argument("--line")
    pps.add_argument("--direct", action="store_true", help="改为直连（清掉绑定线路）")
    pps.add_argument("--kind", choices=("auto", "chromium", "generic"))
    pps.add_argument("--url")
    pps.add_argument("--arg", action="append")
    pps.add_argument("--data-dir")
    pps.add_argument("--no-icon", action="store_true")
    pps.add_argument("--no-shortcut", action="store_true")
    pps.set_defaults(fn=cmd_profile_set)

    ppr = psub.add_parser("rm", help="删除档案")
    ppr.add_argument("id")
    ppr.add_argument("--data", action="store_true", help="连数据目录一起删")
    ppr.set_defaults(fn=cmd_profile_remove)

    ppsh = psub.add_parser("show", help="看档案详情与启动命令")
    ppsh.add_argument("id")
    ppsh.set_defaults(fn=cmd_profile_show)

    ppl = psub.add_parser("launch", help="按档案启动程序（快捷方式调的就是它）")
    ppl.add_argument("id")
    ppl.add_argument("--dry-run", action="store_true")
    ppl.add_argument("--no-core", action="store_true")
    ppl.set_defaults(fn=cmd_profile_launch)

    ppi = psub.add_parser("icon", help="重做图标（不给 id 就全做）")
    ppi.add_argument("id", nargs="?")
    ppi.set_defaults(fn=cmd_profile_icon)

    pplnk = psub.add_parser("shortcut", help="重做快捷方式（不给 id 就全做）")
    pplnk.add_argument("id", nargs="?")
    pplnk.add_argument("--no-desktop", action="store_true", help="只做母版，不投桌面")
    pplnk.set_defaults(fn=cmd_profile_shortcut)

    # ------------------------------------------------------------ 界面 / 托盘 / 系统代理 / 自启 / 扫描
    pg = sub.add_parser("gui", help="打开主界面（软件列表 + 详情 + 设置）")
    pg.add_argument("--debug", action="store_true", help="打开开发者工具")
    pg.add_argument("--view", default="profile",
                    choices=("profile", "settings", "apps"),
                    help="直接落在哪一页（默认主界面；settings = 设置页）")
    pg.set_defaults(fn=cmd_gui)

    pt = sub.add_parser("tray", help="启动托盘（右下角图标）")
    pt.add_argument("--install", action="store_true", help="顺便在桌面建托盘快捷方式")
    pt.add_argument("--warm", action="store_true", help="启动时预热所有内核（默认不预热）")
    pt.add_argument("--quit", action="store_true", help="让正在运行的托盘退出")
    pt.set_defaults(fn=cmd_tray)

    psp = sub.add_parser("sysproxy", help="系统代理开关（接管 / 还原）")
    psp.add_argument("action", choices=("status", "on", "off", "toggle"))
    psp.add_argument("line", nargs="?", help="用哪条线路（默认第一条）")
    psp.set_defaults(fn=cmd_sysproxy)

    pas = sub.add_parser("autostart", help="开机自启开关")
    pas.add_argument("action", choices=("status", "on", "off"))
    pas.set_defaults(fn=cmd_autostart)

    pap2 = sub.add_parser("apps", help="扫描本机已安装的程序（给建档用）")
    pap2.add_argument("action", choices=("scan", "list"), nargs="?", default="scan")
    pap2.add_argument("--limit", type=int, default=25)
    pap2.set_defaults(fn=cmd_apps)

    ptn = sub.add_parser("tun", help="TUN 虚拟网卡接管（需要一次管理员授权）")
    ptn.add_argument("action", choices=("status", "start", "stop", "install", "remove"),
                     nargs="?", default="status")
    ptn.set_defaults(fn=cmd_tun)

    pad = sub.add_parser("appdb", help="「需要代理」软件清单：查/列/换源")
    pad.add_argument("action", choices=("list", "find", "update", "summary"),
                     nargs="?", default="summary")
    pad.add_argument("value", nargs="?", help="find 的进程名，或 update 的网址")
    pad.add_argument("--limit", type=int, default=40)
    pad.set_defaults(fn=cmd_appdb)

    pcl = sub.add_parser("cleanup", help="上线前清理：删掉所有私人代理与派生数据")
    pcl.add_argument("--yes", action="store_true", help="真的执行（不加只打印计划）")
    pcl.set_defaults(fn=cmd_cleanup)

    pdoc = sub.add_parser("doctor", help="环境体检：系统版本 / 运行库 / 内核 / 目录权限")
    pdoc.add_argument("--fix", help="直接修某项，例如 --fix vc_runtime 或 wintun")
    pdoc.set_defaults(fn=cmd_doctor)

    # ------------------------------------------------------------ 订阅自动更新
    pau = sub.add_parser("auto", help="订阅自动更新：看状态 / 改间隔 / 立刻拉一遍")
    pau.add_argument("--interval", type=int, help="每隔几小时更新一次（默认 12）")
    pau.add_argument("--line", help="只改某条线路的间隔（不给就是全局默认）")
    pau.add_argument("--off", action="store_true", help="全部关掉自动更新")
    pau.add_argument("--on", action="store_true", help="恢复默认（每 12 小时）")
    pau.add_argument("--run", action="store_true", help="立刻把所有到期的拉一遍")
    pau.add_argument("--stale-only", action="store_true", help=argparse.SUPPRESS)
    pau.set_defaults(fn=cmd_auto)

    # ------------------------------------------------------------ 诊断包 / 配置搬运
    pdg = sub.add_parser("diag", help="打一个诊断包（日志+配置+体检，凭据自动打码）")
    pdg.add_argument("--out", help="输出 zip 路径（默认放 logs 目录）")
    pdg.add_argument("--open", action="store_true", help="生成后打开所在目录")
    pdg.set_defaults(fn=cmd_diag)

    pex = sub.add_parser("export", help="导出配置（线路 + 应用档案），换电脑用")
    pex.add_argument("--out", help="输出文件（默认放 数据目录/exports/）")
    pex.add_argument("--with-sub", action="store_true",
                     help="连同订阅地址一起导出（含凭据，别公开分享）")
    pex.set_defaults(fn=cmd_export)

    pim = sub.add_parser("import", help="导入配置文件（端口重新随机分配）")
    pim.add_argument("file", help="export 出来的 json")
    pim.add_argument("--fetch", action="store_true", help="顺便把订阅拉一次（导入即可用）")
    pim.set_defaults(fn=cmd_import)

    # 内部用：提权子进程的落点。源码模式走 `-m appproxy.elevate`，
    # 打包成 exe 后没有解释器了，只能转成 `AppProxy.exe elevate ...`。
    pel = sub.add_parser("elevate", help=argparse.SUPPRESS)
    pel.add_argument("rest", nargs=argparse.REMAINDER)
    pel.set_defaults(fn=cmd_elevate)

    a = p.parse_args(argv)
    reg = _reg()
    try:
        return int(a.fn(reg, a) or 0)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        print(f"{FAIL} {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

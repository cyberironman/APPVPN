# -*- coding: utf-8 -*-
"""本地 Web 控制台：界面用 HTML 写，pywebview 套一个原生窗口。

为什么不用 tkinter
    本机托管 Python 没带 tkinter，而且 tkinter 也做不出微信那种「列表 + 详情」布局。

为什么走本地 HTTP
    界面和逻辑彻底解耦：主窗口关了不影响托盘和内核；同一套页面用浏览器也能开
    （调试、截图都方便），不必为调试另做一套 UI。

两条并发规则
    1. 起内核、改档案的动作串行执行（一把全局锁）——两个软件同时点启动时，
       同一个端口不能被抢两次，profiles.json 也不能被两个线程同时写。
    2. 慢活（更新订阅、测速、扫描程序）一律丢后台线程 + 任务表，
       界面用轮询展示进度。绝不让任何一个 HTTP 请求等测速。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from .. import (appdb, appscan, autostart, coreproc, gui_support, icons, latency,
                launcher, pool, preflight, procwatch, profiles, settings, setup,
                shortcut, subscription, sysproxy, tunmgr, xrayconf)
from ..profiles import AppProfile
from ..registry import Registry

ACTION_LOCK = threading.RLock()
# TUN 单独一把锁：等 UAC 的时间可能有几十秒，不能占着 ACTION_LOCK
TUN_LOCK = threading.RLock()
# 选目录是模态对话框，同一时间只允许一个
PICK_LOCK = threading.RLock()
STATIC = Path(__file__).resolve().parent / "static"
APP_TAG = "appproxy"          # /api/ping 的身份标记，防止把端口撞给别人时认错门

app = FastAPI(title="分应用代理管家", docs_url=None, redoc_url=None)

TASKS: dict[str, dict[str, Any]] = {}

# 「托盘点设置」→「已经开着的窗口跳到设置页」。
# 托盘进程和界面进程不是同一个，只能用这条内存信箱传话，
# 前端轮询取走即清空（一次性投递，不会反复抢走用户的当前视图）。
PENDING_VIEW: dict[str, str] = {"view": ""}


# --------------------------------------------------------------------------- 工具
def _reg() -> Registry:
    settings.ensure_dirs()
    return Registry().load()


def _store() -> profiles.ProfileStore:
    return profiles.ProfileStore().load()


def _health(line) -> str:
    return line.health_url or settings.DEFAULT_HEALTH_URL


def _task(name: str) -> dict[str, Any]:
    t = {"id": f"t{int(time.time() * 1000) % 10 ** 9}", "name": name,
         "status": "running", "detail": "", "started": int(time.time()), "ended": 0}
    TASKS[t["id"]] = t
    # 只留最近 20 条，避免长跑之后内存里堆一堆历史
    for old in sorted(TASKS.values(), key=lambda x: x["started"])[:-20]:
        TASKS.pop(old["id"], None)
    return t


def _run_bg(name: str, fn) -> dict[str, Any]:
    task = _task(name)

    def wrap() -> None:
        try:
            task["detail"] = fn(task) or "完成"
            task["status"] = "done"
        except Exception as e:  # noqa: BLE001
            task["detail"] = f"{type(e).__name__}: {e}"
            task["status"] = "error"
        finally:
            task["ended"] = int(time.time())

    threading.Thread(target=wrap, daemon=True, name=name).start()
    return task


def _safe_unlink(path: Path) -> None:
    try:
        p = Path(path)
        if p.is_file():
            p.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- 探活 / 视图
@app.get("/api/ping")
def ping() -> dict[str, Any]:
    """给托盘用：确认这个端口上跑的确实是我们自己。"""
    return {"ok": True, "app": APP_TAG, "pid": os.getpid()}


class ViewBody(BaseModel):
    view: str = "profile"       # profile / settings / apps


@app.post("/api/view")
def post_view(body: ViewBody) -> dict[str, Any]:
    """托盘说「把窗口切到设置页」，先存起来，等界面下一轮轮询取走。"""
    PENDING_VIEW["view"] = body.view
    return {"ok": True, "view": body.view}


@app.get("/api/view/pending")
def get_pending_view() -> dict[str, Any]:
    """取走一次待切换的视图（取完就空，避免反复劫持用户当前页面）。"""
    view = PENDING_VIEW["view"]
    PENDING_VIEW["view"] = ""
    return {"view": view}


CURRENT_VIEW: dict[str, str] = {"view": ""}


@app.post("/api/view/current")
def post_current_view(body: ViewBody) -> dict[str, Any]:
    """界面自己回报「我现在停在哪一页」——给托盘点完设置后确认用。"""
    CURRENT_VIEW["view"] = body.view
    return {"ok": True, "view": body.view}


@app.get("/api/view/current")
def get_current_view() -> dict[str, Any]:
    return {"view": CURRENT_VIEW["view"]}


# --------------------------------------------------------------------------- 页面
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/favicon.ico")
def favicon() -> Response:
    """窗口图标：--app 模式下浏览器会拿它当左上角图标。"""
    icon = settings.icons_dir() / "tray.ico"
    if not icon.is_file():
        raise HTTPException(404, "没有图标")
    return Response(icon.read_bytes(), media_type="image/x-icon",
                    headers={"Cache-Control": "max-age=3600"})


@app.get("/api/icon/{pid}")
def profile_icon(pid: str) -> Response:
    """档案图标（ico → png）。没有就 404，前端自己画占位。"""
    prof = _store().get(pid)
    if prof is None:
        raise HTTPException(404, "没有这个档案")
    png = gui_support.icon_png(prof)
    if png is None:
        raise HTTPException(404, "没有图标")
    return Response(png.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "max-age=30"})


# --------------------------------------------------------------------------- 状态
@app.get("/api/state")
def state() -> dict[str, Any]:
    reg = _reg()
    snap = pool.state(reg, _store())
    sp = sysproxy.read()
    snap["sysproxy"] = {"on": bool(sp.get("enable")), "detail": sysproxy.describe(sp)}
    snap["autostart"] = autostart.status()
    # 一条线路都没有 = 新用户，界面要弹「先告诉我你的代理在哪」
    snap["needs_setup"] = not reg.lines
    snap["tasks"] = [t for t in TASKS.values() if t["status"] == "running"]
    snap["recent_tasks"] = sorted(TASKS.values(), key=lambda x: x["started"],
                                  reverse=True)[:5]
    snap["preflight"] = preflight.report()
    return snap


@app.get("/api/logs")
def logs(lines: int = 60, name: str = "launcher") -> dict[str, Any]:
    path = settings.logs_dir() / f"{name}.log"
    if not path.exists():
        return {"name": name, "text": "（还没有日志）"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # pragma: no cover
        return {"name": name, "text": f"读日志失败：{e}"}
    return {"name": name, "text": "\n".join(text.strip().splitlines()[-lines:])}


# --------------------------------------------------------------------------- 档案动作
class LineRef(BaseModel):
    line_id: str = ""


class NewProfile(BaseModel):
    exe: str
    name: str = ""
    line_id: str = ""
    url: str = ""


@app.post("/api/profile/{pid}/start")
def profile_start(pid: str) -> dict[str, Any]:
    """启动这个软件：需要代理就先按需拉起它那条线路（约 1 秒，不测速）。"""
    with ACTION_LOCK:
        store, reg = _store(), _reg()
        prof = store.get(pid)
        if prof is None:
            raise HTTPException(404, "没有这个档案")
        if not prof.exists():
            raise HTTPException(400, f"找不到程序：{prof.exe}")
        if procwatch.snapshot().alive(prof):
            return {"ok": True, "message": f"{prof.name} 已在运行", "already": True}
        try:
            launcher.launch(prof, reg)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"{type(e).__name__}: {e}") from e
        return {"ok": True, "message": f"{prof.name} 已启动"}


@app.post("/api/profile/{pid}/stop")
def profile_stop(pid: str) -> dict[str, Any]:
    """关闭这个软件：结束它的进程；这条线路没别人用了就顺手停内核。"""
    with ACTION_LOCK:
        store, reg = _store(), _reg()
        prof = store.get(pid)
        if prof is None:
            raise HTTPException(404, "没有这个档案")
        res = pool.close_profile(reg, store, prof)
        return {"ok": True,
                "message": f"已关闭（结束 {res['ended']} 个进程）；{res['message']}"}


@app.post("/api/profile/{pid}/line")
def profile_set_line(pid: str, body: LineRef) -> dict[str, Any]:
    """改绑定线路，顺带重做图标和快捷方式，让角标跟着变。"""
    with ACTION_LOCK:
        store, reg = _store(), _reg()
        prof = store.get(pid)
        if prof is None:
            raise HTTPException(404, "没有这个档案")
        if body.line_id and reg.get(body.line_id) is None:
            raise HTTPException(400, f"没有这条线路：{body.line_id}")
        prof.line_id = body.line_id
        store.upsert(prof)
        store.save()
        made: list[str] = []
        try:
            if icons.available():
                made.append(str(icons.render(prof)))
            made.extend(str(p) for p in shortcut.install(prof))
        except Exception as e:  # noqa: BLE001  图标失败不该挡住改线路
            return {"ok": True, "made": made,
                    "message": f"已改为 {body.line_id or '直连'}；图标/快捷方式没做成：{e}"}
        return {"ok": True, "made": made,
                "message": f"已改为 {body.line_id or '直连'}"}


@app.post("/api/profile/{pid}/icon")
def profile_remake(pid: str) -> dict[str, Any]:
    with ACTION_LOCK:
        prof = _store().get(pid)
        if prof is None:
            raise HTTPException(404, "没有这个档案")
        made: list[str] = []
        if icons.available():
            made.append(str(icons.render(prof)))
        try:
            made.extend(str(p) for p in shortcut.install(prof))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"快捷方式生成失败：{e}") from e
        return {"ok": True, "made": made, "message": f"已重做 {len(made)} 个文件"}


@app.delete("/api/profile/{pid}")
def profile_delete(pid: str) -> dict[str, Any]:
    with ACTION_LOCK:
        store = _store()
        prof = store.get(pid)
        if prof is None:
            raise HTTPException(404, "没有这个档案")
        try:
            shortcut.remove(prof)
        except Exception:  # noqa: BLE001
            pass
        _safe_unlink(prof.icon_path())
        _safe_unlink(settings.icons_dir() / "png" / f"{prof.id}.png")
        store.remove(pid)
        store.save()
        return {"ok": True, "message": f"{prof.name} 已删除（程序本身没动）"}


# --------------------------------------------------------------------------- 线路动作
@app.post("/api/line/{lid}/start")
def line_start(lid: str) -> dict[str, Any]:
    with ACTION_LOCK:
        reg = _reg()
        line = reg.get(lid)
        if line is None:
            raise HTTPException(404, "没有这条线路")
        try:
            msg = pool.ensure_core(reg, line)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"{type(e).__name__}: {e}") from e
        # 手动启动不写登记 —— 自动回收不会把它停掉
        return {"ok": True, "message": msg}


@app.post("/api/line/{lid}/stop")
def line_stop(lid: str) -> dict[str, Any]:
    with ACTION_LOCK:
        reg = _reg()
        line = reg.get(lid)
        if line is None:
            raise HTTPException(404, "没有这条线路")
        n = pool.stop_line(line)
        return {"ok": True, "message": f"{line.name} 已停止（{n} 个进程）"}


@app.post("/api/line/{lid}/refresh")
def line_refresh(lid: str) -> dict[str, Any]:
    """更新订阅 + 实测 + 重建配置 + 重启。慢活，丢后台，绝不阻塞界面。"""

    def work(task: dict[str, Any]) -> str:
        with ACTION_LOCK:
            reg = _reg()
            line = reg.get(lid)
            if line is None:
                raise RuntimeError("没有这条线路")
            if not line.sub_url:
                raise RuntimeError("这条线路没有订阅地址")

            task["detail"] = "拉取订阅"
            nodes, meta = subscription.load(line.sub_url)
            reg.save_nodes(line, nodes, meta)
            task["detail"] = f"拉到 {len(nodes)} 个节点，开始测速"

            alive: set[str] = set()
            binary = reg.binary(line.engine)
            if binary:
                def tick(done: int, total: int) -> None:
                    task["detail"] = f"测速 {done}/{total}"

                res = latency.measure(nodes, binary, _health(line),
                                      engine=line.engine, progress=tick)
                alive = {uid for uid, ms in res.items() if ms is not None}

            task["detail"] = "重建配置"
            cfg = gui_support.build_config(line, nodes, alive or None)
            xrayconf.write(cfg, line.config_path())

            task["detail"] = "重启内核"
            coreproc.stop(line.config_path())
            pool.ensure_core(reg, line)
            return f"{len(nodes)} 个节点，实测可用 {len(alive) or '未测'}；配置已重建并重启"

    return _run_bg(f"更新 {lid}", work)


# --------------------------------------------------------------------------- 程序库
@app.get("/api/apps")
def apps(cached: bool = True) -> dict[str, Any]:
    items = appscan.load_cache() if cached else []
    if not items:
        items = appscan.scan()
        appscan.save_cache(items)
    # 打标：哪些是「明显需要代理」的（Facebook / YouTube / Codex 这类），
    # 并排到前面 —— 新用户第一屏就该看到自己装的那些。
    items = appdb.mark(items)
    existing = {p.exe.lower() for p in _store().profiles}
    return {"items": items, "have": sorted(existing),
            "appdb": {"summary": appdb.summary(), "version": appdb.load().get("version", ""),
                      "groups": appdb.groups()}}


@app.post("/api/apps/scan")
def apps_scan() -> dict[str, Any]:
    def work(task: dict[str, Any]) -> str:
        with ACTION_LOCK:
            items = appscan.scan()
            appscan.save_cache(items)
            return f"扫到 {len(items)} 个可启动程序"

    return _run_bg("扫描已安装程序", work)


@app.post("/api/apps/add")
def apps_add(body: NewProfile) -> dict[str, Any]:
    with ACTION_LOCK:
        store = _store()
        exe = Path(body.exe)
        if not exe.is_file():
            raise HTTPException(400, f"找不到程序：{body.exe}")
        name = body.name or exe.stem
        # id 用 exe 文件名（英文/数字，路径里安全）；
        # 中文名放 name 字段，两者分开，别让 slug 把 id 全抹成 profile。
        base = profiles.slug(exe.stem) if exe.stem else "app"
        if base == "profile":
            base = f"app-{abs(hash(str(exe).lower())) % 10000:04d}"
        prof = AppProfile(id=store.unique_id(base), name=name, exe=str(exe),
                          line_id=body.line_id, url=body.url)
        store.upsert(prof)
        store.save()
        made: list[str] = []
        if icons.available():
            try:
                made.append(str(icons.render(prof)))
            except Exception:  # noqa: BLE001
                pass
        try:
            made.extend(str(p) for p in shortcut.install(prof))
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "id": prof.id, "made": made,
                "message": f"已添加 {prof.name}"}


# --------------------------------------------------------------------------- 设置
@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    from .. import autosub
    reg = _reg()
    sp = sysproxy.read()
    return {
        "lines": [{
            "id": l.id, "name": l.name, "engine": l.engine,
            "http_port": l.http_port, "socks_port": l.socks_port,
            "sub_url": (l.sub_url[:40] + "…") if len(l.sub_url) > 40 else l.sub_url,
            "mode": l.mode,
        } for l in reg.lines],
        # 订阅自动更新：每行的状态文案和「要不要重启内核」都在这里，
        # 前端只负责摆位置，不自己拼时间差
        "subs": autosub.status(reg),
        "subs_global_hours": autosub.global_interval(reg),
        # 「新档案默认走哪条线路」——程序库页的下拉框读写这个值
        "default_line": reg.defaults.get("default_line", ""),
        "sysproxy": {"on": bool(sp.get("enable")), "detail": sysproxy.describe(sp),
                     "server": sp.get("server", "")},
        "autostart": autostart.status(),
        "tun": tunmgr.status(),
        "appdb": appdb.summary(),
        "needs_setup": not reg.lines,
        "home": str(settings.home()),
        # 数据目录：默认是「软件目录\AppProxyData」，用户可以在设置里改
        "home_default": str(settings.exe_dir() / settings.PORTABLE_DIR),
        "home_custom": settings.data_dir_pref() is not None,
        # 「软件放在哪」和「数据放哪」是两件事，界面上分开显示，
        # 用户换文件夹、拷 U 盘之后一眼能确认自己跑的是哪一份
        "exe_dir": str(settings.exe_dir()),
        "portable": settings.is_portable(),
        # 源码运行（开发机）既不是安装版也不是绿色版，界面上要说清楚
        "frozen": bool(getattr(sys, "frozen", False)),
        "preflight": preflight.report(),
        "engines": {
            "singbox": str(reg.binary("singbox") or "未找到"),
            "xray": str(reg.binary("xray") or "未找到"),
        },
    }


class DefaultLineBody(BaseModel):
    line_id: str = ""


@app.post("/api/settings/default-line")
def set_default_line(body: DefaultLineBody) -> dict[str, Any]:
    """新档案默认绑哪条线路（托盘里那项已移到设置页，这里才是正门）。"""
    with ACTION_LOCK:
        reg = _reg()
        if body.line_id and reg.get(body.line_id) is None:
            raise HTTPException(400, f"没有这条线路：{body.line_id}")
        reg.defaults["default_line"] = body.line_id
        reg.save()
        return {"ok": True, "line_id": body.line_id,
                "message": "新档案默认走「直连」" if not body.line_id
                           else f"新档案默认走 {body.line_id}"}


class OpenDirBody(BaseModel):
    what: str = "data"          # data / logs / shortcuts / apps / code


@app.post("/api/open-dir")
def open_dir(body: OpenDirBody) -> dict[str, Any]:
    """打开本机文件夹。托盘菜单瘦身后，这些入口统一挪到设置页底部。"""
    targets = {
        "data": settings.data_dir,
        "logs": settings.logs_dir,
        "shortcuts": settings.shortcuts_dir,
        "apps": settings.apps_dir,
        "code": settings.code_root,
    }
    fn = targets.get(body.what)
    if fn is None:
        raise HTTPException(400, f"不认识的位置：{body.what}")
    path = fn()
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))     # noqa: S606
    except OSError as e:
        raise HTTPException(500, f"打不开 {path}：{e}") from e
    return {"ok": True, "path": str(path), "message": f"已打开 {path}"}


class SysProxyBody(BaseModel):
    mode: str = "off"           # on / off
    line_id: str = ""

@app.post("/api/settings/sysproxy")
def set_sysproxy(body: SysProxyBody) -> dict[str, Any]:
    with ACTION_LOCK:
        if body.mode != "on":
            st = sysproxy.disable()
            return {"ok": True, "message": f"系统代理已关闭（{sysproxy.describe(st)}）"}
        reg = _reg()
        line = reg.get(body.line_id) if body.line_id else None
        if line is None:
            raise HTTPException(400, "先选一条线路")
        if not coreproc.port_open(line.http_port):
            pool.ensure_core(reg, line)
        st = sysproxy.enable(line.http_port)
        return {"ok": True, "message": f"系统代理已指向 {line.name}（{line.http_port}）",
                "state": sysproxy.describe(st)}


class AutoBody(BaseModel):
    on: bool


@app.post("/api/settings/autostart")
def set_autostart(body: AutoBody) -> dict[str, Any]:
    with ACTION_LOCK:
        if body.on:
            shortcut.install_tray()
            p = autostart.enable()
            return {"ok": True, "message": f"已开机自启：{p}"}
        autostart.disable()
        return {"ok": True, "message": "已取消开机自启"}


# --------------------------------------------------------------------------- 环境体检
# 软件是打包分发的，用户机器上没有 Python，缺什么组件只能靠程序自己说清楚。
# 单独一把锁：下载运行库要几十秒，不能占着 ACTION_LOCK 把别的按钮卡死。
PREFLIGHT_LOCK = threading.RLock()


@app.get("/api/preflight")
def get_preflight() -> dict[str, Any]:
    return {
        "issues": preflight.report(),
        "healthy": preflight.is_healthy(),
        "home": str(settings.home()),
        "exe_dir": str(settings.exe_dir()),
        "portable": settings.is_portable(),
        "version": preflight.os_release(),
        "free_mb": preflight.free_space_mb(),
    }


class FixBody(BaseModel):
    key: str = ""


@app.post("/api/preflight/fix")
def post_preflight_fix(body: FixBody) -> dict[str, Any]:
    with PREFLIGHT_LOCK:
        r = preflight.fix(body.key)
    return r


# --------------------------------------------------------------------------- 数据目录
# 默认就是「软件目录\AppProxyData」，装在哪个目录数据就在哪。
# 想放别的盘（C 盘紧张、想放移动硬盘），在设置页点「更改」，弹系统原生选目录框。
class PickBody(BaseModel):
    title: str = "选择数据存放目录"
    initial: str = ""


@app.post("/api/pick-folder")
def post_pick_folder(body: PickBody) -> dict[str, Any]:
    """弹原生「选择文件夹」对话框。会阻塞到这个请求返回，前端要显示等待态。

    单独一把锁：用户可以连点两下，绝不能同时弹两个模态框。
    """
    from .. import folderpick
    with PICK_LOCK:
        path = folderpick.pick_folder(title=body.title or "选择文件夹",
                                      initial=body.initial or None)
    return {"ok": bool(path), "path": path or "", "cancelled": not path}


class DataDirBody(BaseModel):
    path: str = ""                  # 空 = 恢复默认
    copy_existing: bool = True      # 是否把现有配置一起搬过去（叫 copy 会和 pydantic 冲突）


@app.post("/api/settings/data-dir")
def post_data_dir(body: DataDirBody) -> dict[str, Any]:
    """改数据目录。**只记位置不搬程序**，下次启动生效。

    copy=True 时把现有配置复制过去（原目录保留，不删 —— 用户反悔还能改回来）。
    """
    import shutil

    if settings.is_portable():
        raise HTTPException(
            400, "绿色版的数据跟着文件夹走：把整个软件文件夹移到你想放的位置就行。"
                 "（也可以删掉 portable.txt，再在这里指定目录。）")

    old = settings.home()
    raw = (body.path or "").strip().strip('"')
    if not raw:
        settings.clear_data_dir()
        return {"ok": True, "restart": True, "path": str(settings.exe_dir() / settings.PORTABLE_DIR),
                "message": "已恢复默认位置（软件目录下的 AppProxyData）。重新打开软件后生效。"}

    new = Path(raw).expanduser()
    try:
        new = new.resolve()
    except OSError:
        pass

    # 目标在老目录里面（或反过来）会让复制无限递归，直接拒
    try:
        if new == old or new.is_relative_to(old) or old.is_relative_to(new):
            raise HTTPException(400, "新目录和当前数据目录互相包含，换一个位置")
    except AttributeError:          # Python < 3.9 没有 is_relative_to
        if str(new).startswith(str(old)) or str(old).startswith(str(new)):
            raise HTTPException(400, "新目录和当前数据目录互相包含，换一个位置") from None

    try:
        new.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"建不了这个目录：{e}") from e
    if not settings.dir_writable(new):
        raise HTTPException(400, f"没有写权限：{new}")

    copied = 0
    note = ""
    if body.copy_existing and old.is_dir():
        try:
            # 日志没必要搬（还在写，也没价值）
            shutil.copytree(old, new, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("logs", "*.tmp"))
            copied = sum(1 for _ in new.rglob("*"))
            note = f"，已复制 {copied} 个文件"
        except OSError as e:
            note = f"，复制时出错（{e}），可以重新点一次"

    settings.set_data_dir(new)
    return {"ok": True, "restart": True, "path": str(new),
            "message": f"数据目录改成 {new}{note}。重新打开软件后生效（旧目录保留着，没删）。"}


# --------------------------------------------------------------------------- 新用户接线
# 这个软件不自带代理。第一次打开必须问清楚「你的代理在哪」，
# 三种答案（订阅链接 / 分享链接 / 本地端口）都落到同一条 Line 上。
class SubscribeBody(BaseModel):
    url: str = ""


class UriBody(BaseModel):
    uri: str = ""


class LocalBody(BaseModel):
    protocol: str = "http"          # http / socks
    port: int = 0
    host: str = "127.0.0.1"


@app.get("/api/setup")
def get_setup() -> dict[str, Any]:
    return {**setup.describe(), "appdb": appdb.summary(),
            "appdb_note": "内置清单：命中即建议挂代理（Facebook/YouTube/Codex 这类）"}


@app.post("/api/setup/subscribe")
def setup_subscribe(body: SubscribeBody) -> dict[str, Any]:
    with ACTION_LOCK:
        try:
            msg = setup.from_subscription(body.url)
        except setup.SetupError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "message": msg}


@app.post("/api/setup/uri")
def setup_uri(body: UriBody) -> dict[str, Any]:
    with ACTION_LOCK:
        try:
            msg = setup.from_uri(body.uri)
        except setup.SetupError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "message": msg}


@app.post("/api/setup/local")
def setup_local(body: LocalBody) -> dict[str, Any]:
    with ACTION_LOCK:
        try:
            msg = setup.from_local(body.protocol, body.port, body.host)
        except setup.SetupError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "message": msg}


# --------------------------------------------------------------------------- TUN
@app.get("/api/tun")
def get_tun() -> dict[str, Any]:
    return tunmgr.status()


@app.post("/api/tun/start")
def tun_start() -> dict[str, Any]:
    """开 TUN。第一次会装「最高权限计划任务」，此时会在屏幕上弹一次 UAC。

    刻意**不拿 ACTION_LOCK**：等用户点 UAC 可能几十秒，拿了锁会连带卡住
    启动/关闭软件那些按钮。TUN 内核与按档案起的那些内核互不相干。
    """
    with TUN_LOCK:
        try:
            msg = tunmgr.start()
        except Exception as e:  # noqa: BLE001  提权/配置/内核错误都翻成人话给前端
            raise HTTPException(500, str(e)) from e
        return {"ok": True, "message": msg, "state": tunmgr.status()}


@app.post("/api/tun/stop")
def tun_stop() -> dict[str, Any]:
    with TUN_LOCK:
        try:
            msg = tunmgr.stop()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, str(e)) from e
        return {"ok": True, "message": msg, "state": tunmgr.status()}


# --------------------------------------------------------------------------- 订阅自动更新
class IntervalBody(BaseModel):
    hours: int = 12                 # 0 = 关闭
    line_id: str = ""               # 空 = 全局默认


@app.get("/api/subs")
def get_subs() -> dict[str, Any]:
    """每条线路的订阅状态：上次什么时候更的、失败了没有、要不要重启内核。"""
    from .. import autosub
    reg = _reg()
    return {
        "global_hours": autosub.global_interval(reg),
        "lines": autosub.status(reg),
    }


@app.post("/api/subs/interval")
def post_subs_interval(body: IntervalBody) -> dict[str, Any]:
    from .. import autosub
    with ACTION_LOCK:
        reg = _reg()
        hours = max(0, int(body.hours))
        if body.line_id:
            line = reg.get(body.line_id)
            if line is None:
                raise HTTPException(404, "没有这条线路")
            line.sub_interval_h = hours if hours else 0
            reg.upsert(line)
        else:
            reg.defaults["sub_interval_h"] = hours
        reg.save()
    text = "已关闭订阅自动更新" if hours == 0 else f"改为每 {hours} 小时自动更新"
    return {"ok": True, "message": text,
            "state": {"global_hours": autosub.global_interval(_reg())}}


@app.post("/api/subs/run")
def post_subs_run() -> dict[str, Any]:
    """立刻把到期的拉一遍。慢活，丢后台（一次可能十几秒）。"""
    from .. import autosub

    def work(task: dict[str, Any]) -> str:
        rows = autosub.run_due(limit=99)
        if not rows:
            return "没有到期的线路（都是最新的）"
        ok = [r for r in rows if r.get("ok")]
        bad = [r for r in rows if not r.get("ok")]
        task["detail"] = f"{len(ok)} 条成功"
        msg = f"更新 {len(ok)} 条" + (f"，{len(bad)} 条失败" if bad else "")
        if bad:
            msg += "：" + "；".join(f"{r['line']} {r.get('error')}" for r in bad[:3])
        return msg

    return _run_bg("更新订阅", work)


# --------------------------------------------------------------------------- 诊断包 / 配置搬运
class ExportBody(BaseModel):
    with_sub: bool = False


@app.post("/api/diag")
def post_diag() -> dict[str, Any]:
    """打诊断包。凭据全部打码，生成后自检一遍再交给用户。"""
    from .. import diag
    try:
        path = diag.build()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}") from e
    info = diag.verify(path)
    if not info["clean"]:
        return {"ok": False, "path": str(path),
                "message": f"包里还有 {len(info['hits'])} 处疑似凭据，已提示不要外发",
                "hits": info["hits"][:5], "size": info["size"]}
    return {"ok": True, "path": str(path), "size": info["size"],
            "message": f"诊断包已生成（{info['size'] / 1024:.0f} KB），凭据已打码，可直接发给别人"}


@app.post("/api/export")
def post_export(body: ExportBody) -> dict[str, Any]:
    from .. import transfer
    with ACTION_LOCK:
        reg = _reg()
        store = _store()
        bundle = transfer.export_bundle(reg, store, include_secrets=body.with_sub)
        path = transfer.dump(bundle)
    msg = (f"已导出 {len(bundle['lines'])} 条线路、{len(bundle['profiles'])} 个档案"
           + ("（含订阅地址，别公开分享）" if body.with_sub else "（不含订阅地址）"))
    return {"ok": True, "path": str(path), "message": msg}


class ImportBody(BaseModel):
    path: str = ""
    fetch: bool = False


@app.post("/api/import/pick")
def post_import_pick() -> dict[str, Any]:
    """弹原生「打开文件」框，只选路径不读内容。"""
    from .. import folderpick
    with PICK_LOCK:
        path = folderpick.pick_file(title="选择导出的配置文件",
                                    initial=str(settings.home() / "exports"))
    return {"ok": bool(path), "path": path or "", "cancelled": not path}


@app.post("/api/import")
def post_import(body: ImportBody) -> dict[str, Any]:
    from .. import transfer
    if not body.path:
        raise HTTPException(400, "没给文件路径")
    try:
        bundle = transfer.load(Path(body.path))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    def work(task: dict[str, Any]) -> str:
        with ACTION_LOCK:
            reg = _reg()
            store = _store()
            r = transfer.import_bundle(reg, store, bundle, fetch_sub=body.fetch)
        task["detail"] = "导入完成"
        return transfer.summary_text(r)

    return _run_bg("导入配置", work)


def create() -> FastAPI:
    settings.ensure_dirs()
    return app

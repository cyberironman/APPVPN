# -*- coding: utf-8 -*-
"""诊断包：一个按钮把「日志 + 配置 + 环境体检」打成一个 zip，用户报障不用打字。

为什么必须有这个
    现在的排障流程是：让用户跑 doctor → 截图 → 问他线路怎么配的 → 再猜。
    用户描述不准、截图缺页，来回三五轮是常事。一个包下来，环境、线路结构、
    端口、上次订阅更新结果、最近日志全在里头，一次就能定位。

一条硬规则：**包里绝不能有真凭据**
    用户会把包直接贴到 Issue / 群里。里面一旦带订阅 URL 的 token、节点 uuid、
    reality 私钥，等于当场公开账号。所以所有落进包的文本都要过 `redact_text`，
    所有 JSON 都要过 `redact_obj`：订阅地址只留 scheme+主机，凭据一律替换成
    `***`。宁可偶尔把可排障的信息也打掉，也不能漏一次。

包里放什么（都带时间戳目录）
    manifest.txt      版本、系统、路径、生成时间
    doctor.txt        环境体检原文
    state.json        线路/档案/内核/登记的完整快照（脱敏）
    lines.json        线路定义（脱敏；端口保留，端口不敏感）
    profiles.json     应用档案
    runtime/pool.json 内核登记表（哪些线路是给哪个软件拉的）
    runtime/ports.json 随机端口持久化（排查「端口怎么变了」）
    ports.log         端口变更流水
    logs/*.log        最近日志（每个文件只留末尾 256 KB）
    runtime/*.json    编译好的内核配置（脱敏，用来看配置结构对不对）
    README.txt        这份包是什么 / 哪些字段被打码了
"""
from __future__ import annotations

import json
import os
import platform
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from . import settings

# 只留「协议 + 主机 + 端口」，路径和查询串（token 基本都在那里）全打掉
_URL_RE = re.compile(r"\b((?:https?|socks5?|vless|vmess|trojan|ss|hysteria2?)://)"
                     r"(?:[^/\s\"']*@)?([^/\s\"'?#]+)[^\s\"']*", re.I)
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_B64ISH_RE = re.compile(r"\b[A-Za-z0-9+/_-]{24,}={0,2}\b")

# 键名命中即整段打码（大小写不敏感，按下划线/驼峰切词后比对）
SECRET_KEYS = {
    "secret", "password", "passwd", "pwd", "uuid", "token", "pbk", "publickey",
    "sid", "shortid", "spx", "auth", "psk", "privatekey", "apikey", "cookie",
}
# 这些键里的 URL 只留主机
URL_KEYS = {"sub_url", "url", "health_url", "subscribe", "subscription"}

LOG_MAX_BYTES = 256 * 1024
MAX_LOGS = 12


# --------------------------------------------------------------------------- 脱敏
def redact_url(text: str) -> str:
    """https://host/path?token=xxx → https://host/***；节点分享链接 → scheme://***"""
    def sub(m: re.Match) -> str:
        scheme, host = m.group(1), m.group(2)
        if scheme.lower().startswith(("vless", "vmess", "trojan", "hysteria")):
            return f"{scheme}***"
        return f"{scheme}{host}/***"
    return _URL_RE.sub(sub, text)


def redact_text(text: str) -> str:
    """文本级脱敏：URL / uuid / 长 base64 串。"""
    if not text:
        return text
    out = redact_url(text)
    out = _UUID_RE.sub("***uuid***", out)
    out = _B64ISH_RE.sub("***", out)
    return out


def _is_secret_key(key: str) -> bool:
    k = re.sub(r"[^a-z]", "", str(key).lower())
    return k in {re.sub(r"[^a-z]", "", s) for s in SECRET_KEYS}


def redact_obj(obj: Any, *, key: str = "") -> Any:
    """递归脱敏。命中凭据键 → `***`；命中 URL 键 → 只留主机。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _is_secret_key(k) and not isinstance(v, (dict, list)):
                out[k] = "***" if v not in (None, "", 0) else v
            elif str(k).lower() in URL_KEYS and isinstance(v, str):
                out[k] = redact_url(v) if v else v
            else:
                out[k] = redact_obj(v, key=k)
        return out
    if isinstance(obj, list):
        return [redact_obj(v, key=key) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


# --------------------------------------------------------------------------- 体检原文
def doctor_text() -> str:
    """环境体检报告（cli doctor 与诊断包共用同一份文本，避免两边说法不一致）。"""
    from . import preflight

    buf: list[str] = []
    issues = preflight.report()
    major, minor, build = preflight.os_release()
    buf.append("分应用代理管家 · 环境体检")
    buf.append(f"  时间      {time.strftime('%Y-%m-%d %H:%M:%S')}")
    buf.append(f"  系统      Windows {major}.{minor} 内部版本 {build}")
    buf.append(f"  软件位置  {settings.exe_dir()}")
    buf.append(f"  数据目录  {settings.home()}"
               f"{'（绿色版，跟着文件夹走）' if settings.is_portable() else ''}")
    buf.append(f"  剩余空间  {preflight.free_space_mb()} MB")
    buf.append(f"  sing-box  {settings.find_engine('singbox') or '没找到'}")
    buf.append(f"  xray      {settings.find_engine('xray') or '没找到'}")
    buf.append(f"  wintun    {'在' if preflight.has_wintun() else '缺（只影响 TUN）'}")
    buf.append(f"  VC++ 库   {'已装' if preflight.vc_runtime_installed() else '未装（建议装）'}")
    buf.append("")
    if not issues:
        buf.append("[OK] 一切正常，没有需要处理的项目")
    else:
        for i in issues:
            mark = "[!!]" if i["level"] == "block" else "[!] "
            buf.append(f"{mark} {i['title']}")
            buf.append(f"     {i['detail']}")
            if i.get("fix"):
                buf.append(f"     修法：doctor --fix {i['fix']}")
        blocks = [i for i in issues if i["level"] == "block"]
        buf.append("")
        buf.append(f"[!!] 有 {len(blocks)} 项会阻止程序运行" if blocks
                   else "[OK] 都能跑，只是有几项建议处理")
    return "\n".join(buf) + "\n"


# --------------------------------------------------------------------------- 打包
def _read_json_redacted(path: Path) -> str:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return json.dumps({"__unreadable__": str(e)}, ensure_ascii=False, indent=2)
    return json.dumps(redact_obj(raw), ensure_ascii=False, indent=2)


def _tail(path: Path, limit: int = LOG_MAX_BYTES) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > limit:
                f.seek(size - limit)
                head = f.read(limit)
                return "…（只保留末尾 %d KB）\n" % (limit // 1024) + \
                    head.decode("utf-8", "replace")
            return f.read().decode("utf-8", "replace")
    except OSError as e:
        return f"（读不了：{e}）"


def default_out() -> Path:
    settings.logs_dir().mkdir(parents=True, exist_ok=True)
    return settings.logs_dir() / f"diag-{time.strftime('%Y%m%d-%H%M%S')}.zip"


def build(out: Path | None = None, *, reg=None, store=None) -> Path:
    """打一个诊断包，返回 zip 路径。任何一项读不到都不影响其它项。"""
    from . import pool, profiles
    from .registry import Registry

    settings.ensure_dirs()
    target = Path(out) if out else default_out()
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")

    try:
        reg = reg or Registry().load()
    except Exception:                       # noqa: BLE001  坏掉的注册表也照样出包
        reg = None
    try:
        store = store or profiles.ProfileStore().load()
    except Exception:                       # noqa: BLE001
        store = None

    manifest = [
        "分应用代理管家 · 诊断包",
        f"生成时间  {stamp}",
        f"软件版本  {_version()}",
        f"Python    {platform.python_version()}",
        f"系统      {platform.platform()}",
        f"数据目录  {settings.home()}",
        f"软件目录  {settings.exe_dir()}",
        f"线路数    {len(reg.lines) if reg else 0}",
        f"档案数    {len(store.profiles) if store else 0}",
        "",
        "说明：包里所有订阅地址、节点 uuid、密码、reality 密钥都已打码成 ***，",
        "      只保留协议与主机名。可以直接贴到 issue 里，不必再手动隐藏。",
    ]

    readme = (
        "这份包是什么\n"
        "------------\n"
        "出问题时把整个 zip 发出来就行，不用再描述环境、不用截图。\n\n"
        "内容\n"
        "  manifest.txt   版本 / 系统 / 路径\n"
        "  doctor.txt     环境体检（缺什么、能不能跑）\n"
        "  state.json     线路、软件档案、内核、端口占用的快照\n"
        "  lines.json     线路定义（订阅地址已打码，端口保留）\n"
        "  profiles.json  每个软件绑的线路\n"
        "  runtime/pool.json  内核登记表（哪些线路是给哪个软件拉的）\n"
        "  runtime/ports.json 随机分配的端口记录\n"
        "  ports.log      端口变更流水（哪条线什么时候换了端口、为什么）\n"
        "  logs/*.log     最近日志（每个文件只留末尾 256 KB）\n"
        "  runtime/*.json 编译好的内核配置（凭据已打码，只看结构）\n\n"
        "已打码的字段\n"
        "  订阅地址的路径与 token   → scheme://主机/***\n"
        "  节点 uuid / 密码 / reality 公钥私钥 / shortId → ***\n"
    )

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.txt", "\n".join(manifest) + "\n")
        zf.writestr("README.txt", readme)
        try:
            zf.writestr("doctor.txt", doctor_text())
        except Exception as e:              # noqa: BLE001
            zf.writestr("doctor.txt", f"（体检失败：{e}）\n")

        if reg is not None:
            try:
                zf.writestr("lines.json", _read_json_redacted(reg.path))
            except Exception:               # noqa: BLE001
                pass
        if store is not None:
            try:
                zf.writestr("profiles.json", _read_json_redacted(store.path))
            except Exception:               # noqa: BLE001
                pass
            try:
                zf.writestr("state.json", json.dumps(
                    redact_obj(pool.state(reg, store)), ensure_ascii=False, indent=2))
            except Exception as e:          # noqa: BLE001
                zf.writestr("state.json", json.dumps({"error": str(e)}, ensure_ascii=False))

        for name, path in (("runtime/pool.json", settings.data_dir() / "pool.json"),
                           ("runtime/localports.json",
                            settings.data_dir() / "runtime" / "localports.json"),
                           ("runtime/sysproxy-backup.json",
                            settings.data_dir() / "sysproxy-backup.json"),
                           ("ports.log", settings.logs_dir() / "ports.log")):
            if path.exists():
                try:
                    zf.writestr(name, _read_json_redacted(path)
                                if path.suffix == ".json" else redact_text(_tail(path)))
                except Exception:           # noqa: BLE001
                    pass

        logs = sorted(settings.logs_dir().glob("*.log"),
                      key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_LOGS]
        for p in logs:
            try:
                zf.writestr(f"logs/{p.name}", redact_text(_tail(p)))
            except OSError:
                continue

        rtdir = settings.runtime_dir()
        if rtdir.exists():
            for p in list(rtdir.glob("*.json"))[:20]:
                try:
                    zf.writestr(f"runtime/{p.name}", _read_json_redacted(p))
                except OSError:
                    continue

    return target


def _version() -> str:
    try:
        from . import __version__
        return str(__version__)
    except Exception:                       # noqa: BLE001
        return "未知"


def verify(path: Path) -> dict[str, Any]:
    """自检：包里有没有漏网的凭据。测试和 `diag --check` 都用它。"""
    hits: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            data = zf.read(name).decode("utf-8", "replace")
            for m in _UUID_RE.finditer(data):
                hits.append(f"{name}: uuid {m.group(0)[:8]}…")
            if re.search(r"[?&](token|key|auth)=", data, re.I):
                hits.append(f"{name}: URL 里还有 token 参数")
    return {"path": str(path), "clean": not hits, "hits": hits,
            "size": os.path.getsize(path) if path.exists() else 0}

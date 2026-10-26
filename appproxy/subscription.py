# -*- coding: utf-8 -*-
"""订阅拉取与分享链接解析。

要点：
    * 拉订阅必须**绕开系统代理**（本机常出现"系统代理指向已死端口"，
      urllib 默认会读 Windows 系统代理 → 拉取直接失败）；
    * 订阅体可能是明文 URI 列表，也可能是整体 base64（含 urlsafe）；
    * 只认 vless / vmess / trojan / ss；广告节点按关键词过滤。
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .models import Node

UA = "v2rayN/7.24.3"
SCHEMES = ("vless://", "vmess://", "trojan://", "ss://")

# 订阅里常混入的推广节点，不能当真实节点用
SKIP_KEYWORDS = (
    "t.me/", "telegram", "加入我的频道", "解锁更多", "剩余流量",
    "过期时间", "官网", "订阅", "试用", "购买",
)


class SubscriptionError(RuntimeError):
    pass


# --------------------------------------------------------------------------- 拉取
def _direct_opener() -> urllib.request.OpenerDirector:
    """显式清空代理，避免走系统代理 / HTTP_PROXY 环境变量。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(url: str, timeout: float = 20.0) -> tuple[str, dict[str, str]]:
    """返回 (订阅原文, 响应头)。支持 http(s) 与本机文件。"""
    if not url:
        raise SubscriptionError("订阅地址为空")
    if url.startswith(("file://", "raw:")):
        p = Path(urllib.parse.unquote(url.split(":", 1)[1].lstrip("/")))
        return p.read_text(encoding="utf-8", errors="ignore"), {}
    if re.match(r"^[A-Za-z]:[\\/]", url) or url.startswith("\\\\"):
        return Path(url).read_text(encoding="utf-8", errors="ignore"), {}

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with _direct_opener().open(req, timeout=timeout) as r:
            raw = r.read()
            headers = {k.lower(): v for k, v in r.headers.items()}
    except Exception as e:  # noqa: BLE001
        raise SubscriptionError(f"拉取订阅失败：{e}") from e

    text = _maybe_b64(raw.decode("utf-8", "ignore"))
    return text, headers


def parse_userinfo(headers: dict[str, str]) -> dict[str, int]:
    """subscription-userinfo: upload=..; download=..; total=..; expire=.."""
    raw = headers.get("subscription-userinfo") or ""
    out: dict[str, int] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        try:
            out[k.strip()] = int(float(v.strip()))
        except ValueError:
            continue
    return out


# --------------------------------------------------------------------------- 解码
def _b64decode_loose(s: str) -> str:
    s = s.strip().replace("\r", "").replace("\n", "")
    pad = (-len(s)) % 4
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decoder(s + "=" * pad).decode("utf-8", "ignore")
        except (binascii.Error, ValueError):
            continue
    return ""


def _maybe_b64(text: str) -> str:
    """整体 base64 的订阅解成明文；已是明文就原样返回。"""
    if any(s in text for s in SCHEMES):
        return text
    decoded = _b64decode_loose(text)
    if decoded and any(s in decoded for s in SCHEMES):
        return decoded
    raise SubscriptionError("订阅内容里没有可识别的节点（既不是明文也不是 base64）")


# --------------------------------------------------------------------------- 解析
def _q(urlsplit) -> dict[str, str]:
    return {k: v for k, v in urllib.parse.parse_qsl(urlsplit.query, keep_blank_values=True)}


def _parse_vless(uri: str) -> Node | None:
    u = urllib.parse.urlsplit(uri)
    if not u.hostname or not u.port or not u.username:
        return None
    q = _q(u)
    return Node(
        scheme="vless",
        address=u.hostname,
        port=int(u.port),
        secret=urllib.parse.unquote(u.username),
        remark=urllib.parse.unquote(u.fragment).strip(),
        network=q.get("type", "tcp"),
        security=q.get("security", "none"),
        sni=q.get("sni") or q.get("host") or "",
        host=q.get("host", ""),
        path=q.get("path", "/"),
        service_name=q.get("serviceName", ""),
        flow=q.get("flow", ""),
        alpn=[a for a in q.get("alpn", "").split(",") if a],
        fp=q.get("fp", ""),
        pbk=q.get("pbk", ""),
        sid=q.get("sid", ""),
        spx=q.get("spx", ""),
        raw=uri,
    )


def _parse_vmess(uri: str) -> Node | None:
    body = uri[len("vmess://"):].strip()
    try:
        data = json.loads(_b64decode_loose(body))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    host = str(data.get("add") or "").strip()
    try:
        port = int(str(data.get("port") or 0))
    except ValueError:
        return None
    try:
        aid = int(str(data.get("aid") or 0))
    except ValueError:
        aid = 0
    return Node(
        scheme="vmess",
        address=host,
        port=port,
        secret=str(data.get("id") or ""),
        remark=str(data.get("ps") or "").strip(),
        network=str(data.get("net") or "tcp"),
        security=str(data.get("tls") or "none"),
        sni=str(data.get("sni") or ""),
        host=str(data.get("host") or ""),
        path=str(data.get("path") or "/"),
        alpn=[a for a in str(data.get("alpn") or "").split(",") if a],
        fp=str(data.get("fp") or ""),
        aid=aid,
        scy=str(data.get("scy") or "auto"),
        raw=uri,
    )


def _parse_trojan(uri: str) -> Node | None:
    u = urllib.parse.urlsplit(uri)
    if not u.hostname or not u.port or not u.username:
        return None
    q = _q(u)
    return Node(
        scheme="trojan",
        address=u.hostname,
        port=int(u.port),
        secret=urllib.parse.unquote(u.username),
        remark=urllib.parse.unquote(u.fragment).strip(),
        network=q.get("type", "tcp"),
        security="tls" if q.get("security", "tls") in ("", "tls") else q["security"],
        sni=q.get("sni") or q.get("peer") or "",
        host=q.get("host", ""),
        path=q.get("path", "/"),
        service_name=q.get("serviceName", ""),
        alpn=[a for a in q.get("alpn", "").split(",") if a],
        fp=q.get("fp", ""),
        pbk=q.get("pbk", ""),
        sid=q.get("sid", ""),
        raw=uri,
    )


def _parse_ss(uri: str) -> Node | None:
    body = uri[len("ss://"):]
    remark = ""
    if "#" in body:
        body, frag = body.split("#", 1)
        remark = urllib.parse.unquote(frag).strip()
    query = ""
    if "?" in body:
        body, query = body.split("?", 1)
    q = dict(urllib.parse.parse_qsl(query))
    if q.get("plugin"):
        return None          # 插件（obfs/v2ray-plugin）本期不支持

    method = password = ""
    if "@" in body:          # SIP002: ss://b64(method:password)@host:port
        userinfo, hostport = body.rsplit("@", 1)
        cred = _b64decode_loose(urllib.parse.unquote(userinfo)) or urllib.parse.unquote(userinfo)
        if ":" in cred:
            method, password = cred.split(":", 1)
    else:                    # 旧格式: ss://b64(method:password@host:port)
        plain = _b64decode_loose(urllib.parse.unquote(body))
        if "@" not in plain:
            return None
        cred, hostport = plain.rsplit("@", 1)
        if ":" not in cred:
            return None
        method, password = cred.split(":", 1)

    if ":" not in hostport:
        return None
    host, port_s = hostport.rsplit(":", 1)
    try:
        port = int(port_s)
    except ValueError:
        return None
    return Node(scheme="ss", address=host.strip("[]"), port=port, secret=password,
                method=method.lower(), remark=remark, security="none", network="tcp", raw=uri)


def parse_uri(uri: str) -> Node | None:
    uri = uri.strip()
    try:
        if uri.startswith("vless://"):
            return _parse_vless(uri)
        if uri.startswith("vmess://"):
            return _parse_vmess(uri)
        if uri.startswith("trojan://"):
            return _parse_trojan(uri)
        if uri.startswith("ss://"):
            return _parse_ss(uri)
    except (ValueError, IndexError):
        return None
    return None


def is_ad(remark: str) -> bool:
    low = (remark or "").lower()
    return any(k in low for k in SKIP_KEYWORDS)


def parse_text(text: str) -> list[Node]:
    """解析订阅明文 → 去重去广告后的节点列表。"""
    nodes: list[Node] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.lower().startswith(SCHEMES):
            continue
        node = parse_uri(line)
        if node is None or not node.healthy or is_ad(node.remark):
            continue
        if node.uid in seen:
            continue
        seen.add(node.uid)
        nodes.append(node)
    return nodes


def load(url: str, timeout: float = 20.0) -> tuple[list[Node], dict[str, Any]]:
    """一步到位：拉订阅 → 解析 → 返回 (节点, 元信息)。"""
    text, headers = fetch(url, timeout=timeout)
    nodes = parse_text(text)
    if not nodes:
        raise SubscriptionError("订阅里没有可用节点（可能已过期或被限流）")
    return nodes, {"userinfo": parse_userinfo(headers), "count": len(nodes)}

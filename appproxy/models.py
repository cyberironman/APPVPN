# -*- coding: utf-8 -*-
"""节点模型：把各种分享链接归一化成同一份字段，并能生成 xray outbound。

只做数据变换，不发网络请求、不起进程，方便单测。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

# 网络类型别名 -> xray 的 network 取值
NET_ALIASES = {
    "": "tcp",
    "tcp": "tcp",
    "raw": "tcp",
    "ws": "ws",
    "websocket": "ws",
    "grpc": "grpc",
    "gun": "grpc",
    "h2": "h2",
    "http": "h2",
    "h2c": "h2",
    "httpupgrade": "httpupgrade",
    "xhttp": "xhttp",
    "splithttp": "xhttp",
    "quic": "quic",
    "kcp": "tcp",
}

SEC_ALIASES = {"": "none", "none": "none", "tls": "tls", "reality": "reality", "xtls": "tls"}

PRIVATE_CIDRS = [
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "::1/128",
]

_REGION_RE = re.compile(r"(?:^|[^A-Za-z])([A-Z]{2})(?![A-Za-z])")


def guess_region(remark: str) -> str:
    """从备注里取国家/地区码：'🇺🇸US-LA 01' -> US，取不到返回 XX。"""
    if not remark:
        return "XX"
    m = _REGION_RE.search(remark)
    if m:
        return m.group(1)
    return "XX"


@dataclass
class Node:
    scheme: str                      # vless / vmess / trojan / ss
    address: str
    port: int
    secret: str = ""                 # vless/vmess 的 uuid、trojan/ss 的密码
    remark: str = ""
    network: str = "tcp"
    security: str = "none"           # none / tls / reality
    sni: str = ""
    host: str = ""
    path: str = "/"
    service_name: str = ""
    flow: str = ""
    alpn: list[str] = field(default_factory=list)
    fp: str = ""
    pbk: str = ""                    # reality publicKey
    sid: str = ""                    # reality shortId
    spx: str = ""                    # reality spiderX
    method: str = ""                 # ss 加密方式
    aid: int = 0                     # vmess alterId
    scy: str = "auto"                # vmess 加密
    raw: str = ""

    def __post_init__(self) -> None:
        self.network = NET_ALIASES.get((self.network or "").lower(), "tcp")
        self.security = SEC_ALIASES.get((self.security or "").lower(), "none")
        self.address = (self.address or "").strip().strip("[]")
        if not self.path:
            self.path = "/"

    # ---------------------------------------------------------------- 标识
    @property
    def uid(self) -> str:
        base = f"{self.scheme}|{self.address}|{self.port}|{self.secret}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]

    @property
    def tag(self) -> str:
        """稳定 tag：订阅更新后节点顺序变了也不会串线。"""
        return "n-" + self.uid

    @property
    def region(self) -> str:
        return guess_region(self.remark)

    @property
    def healthy(self) -> bool:
        # 本地代理（socks/http 端口）不需要密钥；加密协议必须有
        if self.scheme in ("socks", "http"):
            return bool(self.address and 0 < self.port < 65536)
        return bool(self.address and 0 < self.port < 65536 and self.secret)

    def label(self, index: int | None = None) -> str:
        head = f"{index:02d}." if index is not None else ""
        sec = "" if self.security == "none" else "/" + self.security
        parts = [f"{head}{self.region}", f"{self.scheme}/{self.network}{sec}",
                 f"{self.address}:{self.port}"]
        if self.remark:
            parts.append(self.remark)
        return "  ".join(parts)

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Node":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    # ---------------------------------------------------------------- 配置
    def stream_settings(self) -> dict[str, Any]:
        ss: dict[str, Any] = {"network": self.network, "security": self.security}
        sni = self.sni or self.host or self.address

        if self.security == "tls":
            tls: dict[str, Any] = {"serverName": sni, "fingerprint": self.fp or "chrome",
                                   "allowInsecure": False}
            if self.alpn:
                tls["alpn"] = list(self.alpn)
            ss["tlsSettings"] = tls
        elif self.security == "reality":
            ss["realitySettings"] = {
                "serverName": sni,
                "fingerprint": self.fp or "chrome",
                "publicKey": self.pbk,
                "shortId": self.sid,
                "spiderX": self.spx or "/",
            }

        if self.network == "ws":
            ss["wsSettings"] = {"path": self.path or "/",
                                "headers": {"Host": self.host or sni}}
        elif self.network == "grpc":
            ss["grpcSettings"] = {"serviceName": self.service_name or self.path.strip("/")}
        elif self.network == "h2":
            ss["httpSettings"] = {"path": self.path or "/",
                                  "host": [self.host or sni]}
        elif self.network == "httpupgrade":
            ss["httpupgradeSettings"] = {"path": self.path or "/", "host": self.host or sni}
        elif self.network == "xhttp":
            ss["xhttpSettings"] = {"path": self.path or "/", "host": self.host or sni}
        elif self.network == "quic":
            ss["quicSettings"] = {"security": "none", "key": "",
                                  "header": {"type": "none"}}
        return ss

    def outbound(self) -> dict[str, Any]:
        """生成一条 xray outbound（tag 固定为 self.tag）。"""
        if self.scheme == "vless":
            user: dict[str, Any] = {"id": self.secret, "encryption": "none"}
            if self.flow:
                user["flow"] = self.flow
            ob: dict[str, Any] = {
                "tag": self.tag,
                "protocol": "vless",
                "settings": {"vnext": [{"address": self.address, "port": int(self.port),
                                        "users": [user]}]},
                "streamSettings": self.stream_settings(),
            }
        elif self.scheme == "vmess":
            ob = {
                "tag": self.tag,
                "protocol": "vmess",
                "settings": {"vnext": [{"address": self.address, "port": int(self.port),
                                        "users": [{"id": self.secret, "alterId": int(self.aid),
                                                   "security": self.scy or "auto"}]}]},
                "streamSettings": self.stream_settings(),
            }
        elif self.scheme == "trojan":
            ob = {
                "tag": self.tag,
                "protocol": "trojan",
                "settings": {"servers": [{"address": self.address, "port": int(self.port),
                                          "password": self.secret}]},
                "streamSettings": self.stream_settings(),
            }
        elif self.scheme == "ss":
            ob = {
                "tag": self.tag,
                "protocol": "shadowsocks",
                "settings": {"servers": [{"address": self.address, "port": int(self.port),
                                          "method": self.method, "password": self.secret,
                                          "uot": False}]},
            }
        else:
            raise ValueError(f"不支持的协议：{self.scheme}")
        return ob


@dataclass
class Line:
    """一条代理线路 = 一个订阅（或一组节点）+ 一对本地端口 + 一个 xray 进程。"""

    id: str
    name: str
    sub_url: str = ""
    http_port: int = 0
    socks_port: int = 0
    mode: str = "auto"          # auto = 全节点自动择优；single = 固定某一个节点
    node_key: str = ""          # single 模式下选中的 Node.uid
    enabled: bool = True
    health_url: str = ""
    engine: str = "singbox"     # singbox（支持分应用） / xray（兼容老线路）

    def __post_init__(self) -> None:
        self.mode = (self.mode or "auto").lower()
        if self.mode not in ("auto", "single"):
            raise ValueError(f"mode 只能是 auto / single：{self.mode}")
        self.http_port = int(self.http_port or 0)
        self.socks_port = int(self.socks_port or 0)

    def config_path(self):
        from . import settings
        return settings.runtime_dir() / f"{self.id}.json"

    def nodes_path(self):
        from . import settings
        return settings.data_dir() / f"nodes-{self.id}.json"

    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Line":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

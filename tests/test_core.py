# -*- coding: utf-8 -*-
"""协议解析与配置生成的核心单测（不联网、不起进程）。"""
from __future__ import annotations

import base64
import json

from appproxy import singconf, subscription, xrayconf
from appproxy.models import Line, Node

UUID = "11111111-2222-3333-4444-555555555555"

VLESS_WS = (f"vless://{UUID}@1.2.3.4:443?encryption=none&security=tls&type=ws"
            "&path=%2Fws&host=example.com&sni=example.com&fp=chrome#US-LA-01")
VLESS_REALITY = (f"vless://{UUID}@5.6.7.8:443?security=reality&type=tcp"
                 "&sni=www.microsoft.com&fp=chrome&pbk=PUBKEYabc&sid=ab12"
                 "&flow=xtls-rprx-vision#JP-Tokyo-01")
TROJAN = "trojan://pass123@9.9.9.9:443?security=tls&type=grpc&serviceName=gun#HK-01"
SS = ("ss://" + base64.b64encode(b"aes-256-gcm:sspass").decode() + "@8.8.8.8:8388#SG-01")

VMESS_JSON = {
    "v": "2", "ps": "DE-Frankfurt", "add": "7.7.7.7", "port": "8443",
    "id": UUID, "aid": "0", "scy": "auto", "net": "ws", "type": "none",
    "host": "de.example.com", "path": "/ray", "tls": "tls", "sni": "de.example.com",
}
VMESS = "vmess://" + base64.b64encode(json.dumps(VMESS_JSON).encode()).decode()


def test_parse_vless_ws_tls():
    n = subscription.parse_uri(VLESS_WS)
    assert n and n.scheme == "vless" and n.address == "1.2.3.4" and n.port == 443
    assert n.network == "ws" and n.security == "tls"
    assert n.path == "/ws" and n.host == "example.com"
    assert n.region == "US"


def test_parse_vless_reality_keeps_flow_and_keys():
    n = subscription.parse_uri(VLESS_REALITY)
    assert n.security == "reality" and n.pbk == "PUBKEYabc" and n.sid == "ab12"
    assert n.flow == "xtls-rprx-vision"
    assert n.region == "JP"


def test_parse_vmess_trojan_ss():
    v = subscription.parse_uri(VMESS)
    assert v.scheme == "vmess" and v.host == "de.example.com" and v.network == "ws"
    t = subscription.parse_uri(TROJAN)
    assert t.scheme == "trojan" and t.service_name == "gun" and t.security == "tls"
    s = subscription.parse_uri(SS)
    assert s.scheme == "ss" and s.method == "aes-256-gcm" and s.secret == "sspass"


def test_parse_text_filters_ads_and_dupes():
    text = "\n".join([VLESS_WS, VLESS_WS, TROJAN,
                      "vless://x@1.1.1.1:443?type=tcp#加入我的频道 t.me/abc"])
    nodes = subscription.parse_text(text)
    assert len(nodes) == 2


def test_base64_subscription_decodes():
    payload = base64.b64encode((VLESS_WS + "\n" + TROJAN).encode()).decode()
    text = subscription._maybe_b64(payload)
    assert VLESS_WS in text and TROJAN in text


def test_uid_stable_across_order():
    a = subscription.parse_uri(VLESS_WS)
    b = subscription.parse_uri(VLESS_WS)
    assert a.uid == b.uid and a.tag == b.tag


def _line(**kw) -> Line:
    base = dict(id="t", name="测试", http_port=10910, socks_port=10911)
    base.update(kw)
    return Line(**base)


def test_xray_config_auto_has_balancer():
    nodes = [subscription.parse_uri(u) for u in (VLESS_WS, TROJAN)]
    cfg = xrayconf.build(_line(engine="xray"), nodes)
    ports = [i["port"] for i in cfg["inbounds"]]
    assert ports == [10910, 10911]
    assert cfg["routing"]["balancers"][0]["strategy"]["type"] == "leastPing"
    assert cfg["observatory"]["subjectSelector"] == ["n-"]


def test_xray_config_single_routes_to_node():
    nodes = [subscription.parse_uri(u) for u in (VLESS_WS, TROJAN)]
    node = nodes[0]
    cfg = xrayconf.build(_line(engine="xray", mode="single", node_key=node.uid), nodes)
    assert {"type": "field", "outboundTag": node.tag, "network": "tcp,udp"} in cfg["routing"]["rules"]
    assert "observatory" not in cfg


def test_singbox_auto_urltest_and_ports():
    nodes = [subscription.parse_uri(u) for u in (VLESS_WS, VLESS_REALITY)]
    cfg = singconf.build(_line(), nodes)
    assert cfg["inbounds"][0]["type"] == "mixed"
    urltest = [o for o in cfg["outbounds"] if o["type"] == "urltest"]
    assert urltest and urltest[0]["tag"] == "auto"
    assert cfg["route"]["final"] == "auto"


def test_singbox_node_fields():
    n = subscription.parse_uri(VLESS_REALITY)
    ob = singconf.outbound(n)
    assert ob["type"] == "vless" and ob["flow"] == "xtls-rprx-vision"
    assert ob["tls"]["reality"]["public_key"] == "PUBKEYabc"
    assert ob["tls"]["utls"]["fingerprint"] == "chrome"


def test_singbox_ws_transport():
    n = subscription.parse_uri(VLESS_WS)
    ob = singconf.outbound(n)
    assert ob["transport"] == {"type": "ws", "path": "/ws", "headers": {"Host": "example.com"}}


def test_dispatcher_routes_by_process():
    alpha = [subscription.parse_uri(VLESS_WS)]
    lan = [subscription.parse_uri(TROJAN)]
    cfg = singconf.build_dispatcher(
        {"alpha": alpha, "beta": lan},
        port=10920,
        app_rules={"chrome.exe": "auto-alpha", "Telegram.exe": "auto-beta"},
    )
    rules = cfg["route"]["rules"]
    assert rules[0] == {"action": "sniff"}
    assert {"process_name": ["chrome.exe"], "action": "route", "outbound": "auto-alpha"} in rules
    assert cfg["route"]["final"] == "direct"


def test_dispatcher_rejects_unknown_line():
    import pytest
    alpha = [subscription.parse_uri(VLESS_WS)]
    with pytest.raises(singconf.ConfigError):
        singconf.build_dispatcher({"alpha": alpha}, 10921, {"a.exe": "auto-nope"})

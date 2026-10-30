# -*- coding: utf-8 -*-
"""诊断包：**打码不能漏**。这个包是要被用户贴到公开 issue 里的。"""
from __future__ import annotations

import json
import zipfile

from appproxy import diag
from appproxy.models import Line, Node
from appproxy.registry import Registry

TOKEN_URL = "https://sub.example.com/api/v1/client/subscribe?token=8f3a9c2b7d1e4f6a"
UUID = "b831381d-6324-4d53-ad4f-8cda48b30811"


def test_redact_url_keeps_host_drops_token():
    out = diag.redact_url(TOKEN_URL)
    assert out.startswith("https://sub.example.com")
    assert "token" not in out and "8f3a9c2b" not in out


def test_redact_node_link():
    uri = f"vless://{UUID}@1.2.3.4:443?security=reality&pbk=abc#US-01"
    out = diag.redact_url(uri)
    assert out == "vless://***"          # 整条打掉，不留 host


def test_redact_text_kills_uuid():
    assert UUID not in diag.redact_text(f"uuid is {UUID}")


def test_redact_obj_by_key():
    src = {"id": "alpha", "secret": UUID, "pbk": "PUBKEY", "sub_url": TOKEN_URL,
           "http_port": 15002, "nested": {"password": "hunter2", "keep": "ok"}}
    out = diag.redact_obj(src)
    assert out["id"] == "alpha"                  # 线路 id 不是凭据，保留
    assert out["secret"] == "***" and out["pbk"] == "***"
    assert out["nested"]["password"] == "***"
    assert out["nested"]["keep"] == "ok"
    assert out["http_port"] == 15002             # 端口要留着排障
    assert "token=" not in out["sub_url"]
    assert out["sub_url"].startswith("https://sub.example.com")


def test_redact_obj_keeps_empty_secret_empty():
    assert diag.redact_obj({"secret": ""})["secret"] == ""


def _seed(home):
    reg = Registry().load()
    line = Line(id="alpha", name="alpha", sub_url=TOKEN_URL, http_port=15002)
    reg.upsert(line)
    reg.save()
    reg.save_nodes(line, [Node(scheme="vless", address="1.2.3.4", port=443,
                              secret=UUID, remark="US-01")], {})
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "tray.log").write_text(f"订阅 {TOKEN_URL} 拉取失败\n", encoding="utf-8")
    return reg


def test_build_creates_package_and_has_no_secrets(tmp_home):
    reg = _seed(tmp_home)
    out = tmp_home / "diag.zip"
    path = diag.build(out, reg=reg)
    assert path.exists()
    names = zipfile.ZipFile(path).namelist()
    for must in ("manifest.txt", "README.txt", "doctor.txt", "lines.json", "state.json"):
        assert must in names, names

    info = diag.verify(path)
    assert info["clean"], info["hits"]

    # 再加一层硬断言：整个包里任何文件都不许出现 token 原文或 uuid
    with zipfile.ZipFile(path) as zf:
        blob = "".join(zf.read(n).decode("utf-8", "replace") for n in zf.namelist())
    assert "8f3a9c2b7d1e4f6a" not in blob
    assert UUID not in blob
    assert "sub.example.com" in blob        # 主机名保留，方便看 DNS/证书问题


def test_build_survives_broken_registry(tmp_home):
    """注册表坏掉也要出包 —— 报障的时候往往就是配置已经烂了。"""
    (tmp_home / "data").mkdir(parents=True, exist_ok=True)
    (tmp_home / "data" / "lines.json").write_text("{not json", encoding="utf-8")
    path = diag.build(tmp_home / "diag2.zip")
    assert path.exists()
    with zipfile.ZipFile(path) as zf:
        assert "doctor.txt" in zf.namelist()


def test_verify_flags_unredacted_uuid(tmp_home):
    """兜底：如果有人绕过 redact 往包里塞了明文，verify 必须报出来。"""
    bad = tmp_home / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("leak.txt", f"secret={UUID}")
    info = diag.verify(bad)
    assert info["clean"] is False
    assert info["hits"]


def test_read_json_redacted_masks_secrets(tmp_home):
    p = tmp_home / "x.json"
    p.write_text(json.dumps({"lines": [{"id": "a", "sub_url": TOKEN_URL,
                                        "http_port": 15002}]}), encoding="utf-8")
    text = diag._read_json_redacted(p)
    assert "token" not in text
    assert '"http_port": 15002' in text

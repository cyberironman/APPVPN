# -*- coding: utf-8 -*-
"""系统代理接管/还原的真实验证 —— 一定要能原样还原，否则不许上线。

流程：读原值 -> 备份 -> 指向本机一个本地端口 -> 校验 -> 关闭 -> 校验还原到原值。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from appproxy import sysproxy  # noqa: E402

before = sysproxy.read()
print("原始：", json.dumps(before, ensure_ascii=False))
ok = False
try:
    st = sysproxy.enable(10801)
    print("接管：", sysproxy.describe(st))
    hit = sysproxy.matches_port(st, 10801)
    print(f"{'[OK]' if hit else '[!!]'} 已指向 10801")
    print("   备份：", json.dumps(sysproxy.load_backup(), ensure_ascii=False))
    ok = hit
finally:
    back = sysproxy.disable()
    print("还原：", json.dumps(back, ensure_ascii=False))
    same = (back.get("enable") == before.get("enable")
            and back.get("server") == before.get("server"))
    print(f"{'[OK]' if same else '[!!]'} 是否回到原样：{same}")
    if not same:
        print("   ！！没有还原成功，请手动检查系统代理设置")

sys.exit(0 if ok else 1)

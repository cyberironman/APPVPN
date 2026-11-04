# -*- coding: utf-8 -*-
"""界面语言（zh / en）——中文原文作键的运行时翻译。

WebUI 用 JS 端的同款引擎（index.html 顶部的 i18n 块），这里给托盘菜单
等 Python 侧的可见文案用。语言选什么存数据目录 ui.json（settings.ui_lang）。

设计原则：zh 模式一律原样返回（零维护）；en 模式先查精确字典，再查
带 {x} 占位的模式表 —— {x} 匹配一个「连续段」（非中文、非全角标点、
非空白的连续字符，如数字、名字、路径），按出现顺序回填。
"""
from __future__ import annotations

import re

from . import settings

# ---- 精确字典：托盘菜单 + 状态摘要 ----------------------------------------
DICT: dict[str, str] = {
    "分应用代理管家": "AppProxy",
    "打开主界面": "Open AppProxy",
    "显示主界面（已在后台）": "Show window (running in background)",
    "设置…": "Settings…",
    "设置（切到界面并闪烁提示）": "Settings (switch & flash the window)",
    "启动所有软件": "Start all apps",
    "关闭所有软件（并释放内核）": "Close all apps (and free the cores)",
    "退出": "Quit",
    "系统代理：永不修改（各软件按档案分流）":
        "System proxy: never modified (per-app routing)",
    "没有线路": "No lines",
    "上一个操作还没结束": "Previous action is still in progress",
    "主界面已打开": "Window is open",
}

# ---- 模式表：{x} = 一个连续段，按出现顺序回填 ------------------------------
PATTERNS: list[tuple[str, str]] = [
    ("全部运行中（{x}）", "All running ({x})"),
    ("部分运行中（{x}）", "Partly running ({x})"),
    ("全部停止（{x}）", "All stopped ({x})"),
    ("系统代理已开启（{x}）", "System proxy on ({x})"),
    ("系统代理：由其他程序管理（{x}），本产品不碰",
     "System proxy: managed by another app ({x}) — untouched"),
    ("{x} 个软件已启动", "{x} apps started"),
    ("{x} 个软件已关闭", "{x} apps closed"),
]

# 连续段：既不是中文，也不是充当边界的全角标点/空白
_BLOB = re.compile(r"[^（））：，。、·—｜「」＋？！\s\u4e00-\u9fff]+")


def _norm(s: str) -> str:
    return _BLOB.sub("\x01", s.replace("{x}", "\x01"))


def tr(s: str | None) -> str | None:
    """zh 原样返回；en 先精确、后模式，都查不到就回退中文（宁缺勿错）。"""
    if s is None:
        return s
    s = str(s)
    if settings.ui_lang() != "en":
        return s
    if s in DICT:
        return DICT[s]
    pat = _norm(s)
    for zp, ep in PATTERNS:
        if _norm(zp) == pat:
            out = ep
            for blob in _BLOB.findall(s):
                out = out.replace("{x}", blob, 1)
            return out
    return s

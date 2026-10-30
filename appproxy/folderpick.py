# -*- coding: utf-8 -*-
"""弹一个 Windows 原生的「选择文件夹」对话框，拿回用户选的绝对路径。

为什么不用网页里的输入框
    浏览器出于安全不给页面绝对路径（File System Access 只给 handle，
    拿不到 `D:\\xxx` 这种字符串），而程序需要的是真路径。也不想让用户
    手打 `D:\\我的代理\\数据` —— 打错一个字符就换了个地方存。

为什么不用 tkinter
    打包时把 tkinter 排掉了（省 8 MB，界面也不用它）。这里直接调 shell32，
    零依赖。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

# BROWSEINFO 的标志位
BIF_RETURNONLYFSDIRS = 0x0001       # 只让选文件夹，不给选文件
BIF_EDITBOX = 0x0010                # 允许手打路径
BIF_NEWDIALOGSTYLE = 0x0040         # 新式对话框（可调整窗口大小、可新建文件夹）
BIF_USENEWUI = BIF_EDITBOX | BIF_NEWDIALOGSTYLE

MAX_PATH = 260


class _BrowseInfoW(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", ctypes.c_void_p),
        ("lParam", wintypes.LPARAM),
        ("iImage", ctypes.c_int),
    ]


def pick_folder(title: str = "选择文件夹", initial: str | None = None) -> str | None:
    """弹出对话框，返回选中的路径；用户取消返回 None。

    这个函数**会阻塞**到用户点确定/取消为止（模态对话框），调用方要放在
    单独的工作线程里（FastAPI 的同步端点本来就在线程池里跑，正好）。
    """
    if os.name != "nt":
        return None

    shell32 = ctypes.windll.shell32            # type: ignore[attr-defined]
    ole32 = ctypes.windll.ole32                # type: ignore[attr-defined]

    # SHBrowseForFolder 是 Shell 的模态对话框，需要 STA（单线程套间）
    ole32.CoInitialize(None)
    root = None
    pidl = None
    try:
        if initial:
            # 把初始目录转成 ITEMIDLIST；失败就退化成「我的电脑」根
            shell32.SHParseDisplayName.argtypes = [
                wintypes.LPCWSTR, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p), wintypes.ULONG,
                ctypes.POINTER(wintypes.ULONG)]
            shell32.SHParseDisplayName.restype = ctypes.c_long
            attrs = wintypes.ULONG(0)
            if shell32.SHParseDisplayName(str(initial), None,
                                          ctypes.byref(root), 0,
                                          ctypes.byref(attrs)) != 0:
                root = None

        buf = ctypes.create_unicode_buffer(MAX_PATH)
        bi = _BrowseInfoW()
        bi.hwndOwner = None
        bi.pidlRoot = root
        bi.pszDisplayName = buf
        bi.lpszTitle = title
        bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_USENEWUI
        bi.lpfn = None
        bi.lParam = 0
        bi.iImage = 0

        shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(_BrowseInfoW)]
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return None                       # 用户点了取消

        path_buf = ctypes.create_unicode_buffer(MAX_PATH)
        shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
        shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
        if not shell32.SHGetPathFromIDListW(pidl, path_buf):
            return None
        return path_buf.value or None
    finally:
        if pidl:
            ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
        if root:
            ole32.CoTaskMemFree(root)
        ole32.CoUninitialize()


# --------------------------------------------------------------------------- 选文件
OFN_READONLY = 0x00000001
OFN_OVERWRITEPROMPT = 0x00000002
OFN_NOCHANGEDIR = 0x00000008
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000


class _OpenFileNameW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def pick_file(title: str = "选择文件", initial: str | None = None,
              pattern: str = "*.json", label: str = "配置文件") -> str | None:
    """弹「打开文件」对话框，返回绝对路径；取消返回 None。

    为什么要它：导入配置需要用户给出一个 json 的**绝对路径**，而网页拿不到
    真实路径（浏览器只给 File 对象）。走 shell32/comdlg32 原生对话框最简单，
    也省掉一个 python-multipart 依赖。
    """
    if os.name != "nt":
        return None
    comdlg32 = ctypes.windll.comdlg32          # type: ignore[attr-defined]
    ole32 = ctypes.windll.ole32                # type: ignore[attr-defined]
    ole32.CoInitialize(None)
    try:
        buf = ctypes.create_unicode_buffer(32768)
        ofn = _OpenFileNameW()
        ofn.lStructSize = ctypes.sizeof(_OpenFileNameW)
        ofn.hwndOwner = None
        # 过滤器是「说明\0通配\0说明\0通配\0\0」的双 null 结尾字符串
        ofn.lpstrFilter = f"{label}\0{pattern}\0所有文件\0*.*\0\0"
        ofn.nFilterIndex = 1
        ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
        ofn.nMaxFile = 32768
        ofn.lpstrInitialDir = str(initial) if initial else None
        ofn.lpstrTitle = title
        ofn.Flags = (OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST
                     | OFN_NOCHANGEDIR | OFN_EXPLORER)
        comdlg32.GetOpenFileNameW.argtypes = [ctypes.POINTER(_OpenFileNameW)]
        comdlg32.GetOpenFileNameW.restype = wintypes.BOOL
        if not comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
            return None
        return buf.value or None
    finally:
        ole32.CoUninitialize()

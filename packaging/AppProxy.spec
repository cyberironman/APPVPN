# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把整个软件打成一个双击就能用的文件夹。

为什么 onedir 而不是 onefile：
    onefile 每次启动都要把 88 MB 的 sing-box 内核解压到临时目录再跑，
    冷启动要好几秒，杀毒软件还会反复扫描同一个临时文件。
    onedir 解压一次就位，之后是秒开。

产物体积构成：
    sing-box.exe 79 MB + libcronet.dll 9 MB 是第三方内核，改不了；
    Python 运行时和依赖约 40 MB。
"""
from pathlib import Path

# SPECPATH 是本文件所在目录（packaging/），项目根在它上一级
ROOT = Path(SPECPATH).resolve().parent

datas = [
    # 界面（本地 HTTP 服务的静态页）
    (str(ROOT / "appproxy" / "webui" / "static"), "appproxy/webui/static"),
    # 「明显需要代理的软件」内置清单
    (str(ROOT / "appproxy" / "data"), "appproxy/data"),
    # 内核：随包分发，用户不需要另做任何事
    (str(ROOT / "third_party" / "sing-box"), "third_party/sing-box"),
    (str(ROOT / "third_party" / "来源与许可.md"), "third_party"),
]

# 这些是运行期动态 import 的，静态分析看不到，必须点名
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "pystray._win32",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "PIL.ImageOps",
]

# 明确不要的东西，省体积。
# 注意别把 distutils / unittest / test 写进来：PyInstaller 自带 hook 要给它们做
# alias，一排除就抛 "already imported as ExcludedModule"，直接构建失败。
excludes = [
    "tkinter",
    "numpy", "pandas", "matplotlib", "scipy",
    "IPython", "jupyter", "notebook",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AppProxy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 压内核 dll 容易压出问题，不压
    console=False,           # 双击不弹黑框
    disable_windowed_traceback=False,
    icon=str(ROOT / "packaging" / "app.ico"),
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AppProxy",
)

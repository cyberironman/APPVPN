# -*- coding: utf-8 -*-
"""一键出发布包：打包 exe + 压成 zip，给普通用户下。

    python scripts\\build_release.py              # 完整打包
    python scripts\\build_release.py --skip-pack   # 只压缩已有的 dist\\AppProxy

产出：
    dist\\AppProxy-v<版本>-win64.zip      解压双击 AppProxy.exe 就能用
    dist\\AppProxy-v<版本>-win64.zip.sha256

前置：pip install pyinstaller（脚本会自己检查，缺了直接给一句人话）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "AppProxy"
SPEC = ROOT / "packaging" / "AppProxy.spec"
PKG = ROOT / "appproxy"

README_TXT = """分应用代理管家 v{ver}
========================================

怎么用
------
1. 双击 AppProxy.exe，右下角会出现托盘图标。
2. 第一次运行会直接弹出接线页：填你自己的代理订阅地址，
   或者选一个你已经在用的客户端的本地端口。
3. 左侧挑一个软件，右侧点「启动」，它就带着代理打开。
   关掉程序，后台几秒内自动收掉内核。

不需要装 Python，不需要装任何依赖。整个软件就在这个文件夹里，
想换地方直接整个文件夹搬走。

想放到桌面
----------
主界面左下角「设置」→「创建桌面快捷方式」。

删掉它
------
直接删这个文件夹。运行数据在 %APPDATA% 之外的独立目录（默认 D:\\AppProxy），
不要了可以一起删。

这个文件夹里有什么
------------------
AppProxy.exe         主程序（双击它）
_internal\\           运行时和界面资源，别删
third_party\\         sing-box 内核（GPL-3.0，许可见随附 LICENSE）

内核来源：https://github.com/SagerNet/sing-box
"""


def say(msg: str) -> None:
    print(msg, flush=True)


def version() -> str:
    ns: dict = {}
    init = PKG / "__init__.py"
    for line in init.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            exec(line, ns)          # noqa: S102  自己仓库里的常量，安全
    return ns.get("__version__", "0.0.0")


def check_engine() -> Path:
    found = sorted((ROOT / "third_party" / "sing-box").glob("*/sing-box.exe"))
    if not found:
        say("[x] 没找到 third_party\\sing-box\\*\\sing-box.exe")
        say("    仓库里本该有这份内核。缺了就先把内核放回去：")
        say("    python scripts\\fetch_engine.py")
        sys.exit(1)
    size = found[-1].stat().st_size / 1048576
    say("[1/4] 内核就位：%s（%.1f 兆字节）" % (found[-1].name, size))
    return found[-1]


def check_pyinstaller() -> str:
    exe = Path(sys.executable).parent / "pyinstaller.exe"
    if not exe.is_file():
        try:
            import PyInstaller  # noqa: F401
            return "pyinstaller"
        except ImportError:
            say("[x] 没装 PyInstaller：pip install pyinstaller")
            sys.exit(1)
    return str(exe)


def archive_old() -> None:
    """把上一轮的 dist\\AppProxy 挪走，而不是删掉。

    PyInstaller 重建 COLLECT 时会 rmtree 目标目录；在受限环境（比如带
    safe-delete 拦截的沙箱、或开了受控文件夹访问的 Windows）这一步会被拦。
    改名同样能腾出路径，而且不丢东西 —— 旧的整份挪到 build\\_prev\\ 下，
    想回滚也方便，不想要了手动删掉即可。
    """
    old = ROOT / "dist" / "AppProxy"
    if not old.is_dir():
        return
    import time
    bak = ROOT / "build" / "_prev" / ("AppProxy-%s" % time.strftime("%Y%m%d-%H%M%S"))
    bak.parent.mkdir(parents=True, exist_ok=True)
    try:
        old.rename(bak)
        say("      上一轮产物已挪到 build\\_prev\\%s（可随时删）" % bak.name)
    except OSError as e:
        say("[x] 挪不走旧产物：%s" % e)
        say("    手动把 dist\\AppProxy 改名或删掉，再跑一次")
        sys.exit(1)


def build(pyi: str) -> None:
    # 不做 rmtree：某些受限环境会拦批量删除。
    say("[2/4] 打包（约 30 到 60 秒）")
    archive_old()
    cmd = [pyi, str(SPEC), "--noconfirm",
           "--distpath", str(ROOT / "dist"),
           "--workpath", str(ROOT / "build")]
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        say("[x] 打包失败，看上面 PyInstaller 的输出")
        sys.exit(r.returncode)


def smoke() -> None:
    """跑一句只读命令，退出码 0 就算 exe 真的能起来。"""
    say("[3/4] 冒烟测试")
    exe = APP / "AppProxy.exe"
    if not exe.is_file():
        say("[x] 打包产物里没有 AppProxy.exe")
        sys.exit(1)
    try:
        r = subprocess.run([str(exe), "appdb", "list"],
                           capture_output=True, timeout=120)
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
        r = None
    if not ok:
        say("[x] AppProxy.exe 起不来（退出码 %s）"
            % (getattr(r, "returncode", "超时")))
        sys.exit(1)

    # 内核必须真的躺在包里，不然用户点了启动会报「找不到内核」
    inner = list((APP / "_internal" / "third_party" / "sing-box").glob("*/sing-box.exe"))
    if not inner:
        say("[x] 包里的内核丢了")
        sys.exit(1)
    say("      exe 能跑，内核在包内：%s" % inner[0].relative_to(APP))
    static = APP / "_internal" / "appproxy" / "webui" / "static" / "index.html"
    if not static.is_file():
        say("[x] 包里的界面文件丢了")
        sys.exit(1)
    say("      界面文件在包内：%s" % static.relative_to(APP))


def pack(ver: str) -> Path:
    say("[4/4] 压缩")
    (APP / "使用说明.txt").write_text(README_TXT.format(ver=ver), encoding="utf-8")

    out = ROOT / "dist" / ("AppProxy-v%s-win64.zip" % ver)
    if out.exists():
        out.unlink()
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(APP.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(APP))
                count += 1

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.with_suffix(".zip.sha256")).write_text(
        "%s  %s\n" % (digest, out.name), encoding="utf-8")

    raw = sum(p.stat().st_size for p in APP.rglob("*") if p.is_file())
    say("")
    say("=" * 58)
    say("  出来了：%s" % out)
    say("  压缩前 %.1f 兆字节 / 压缩后 %.1f 兆字节 / %d 个文件"
        % (raw / 1048576, out.stat().st_size / 1048576, count))
    say("  校验值：%s" % digest[:32])
    say("=" * 58)
    say("")
    say("  上传到 GitHub Releases 就能直接发给别人：")
    say("  gh release create v%s \"%s\" --title \"v%s\"" % (ver, out.name, ver))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="打发布包")
    ap.add_argument("--skip-pack", action="store_true", help="不重新打包，只压缩")
    ap.add_argument("--skip-smoke", action="store_true", help="跳过冒烟测试")
    a = ap.parse_args()

    ver = version()
    say("分应用代理管家 v%s · 出发布包" % ver)
    say("")

    if a.skip_pack:
        say("[--] 跳过打包，直接用 dist\\AppProxy")
        if not (APP / "AppProxy.exe").is_file():
            say("[x] dist\\AppProxy\\AppProxy.exe 不存在，先正常跑一次")
            return 1
    else:
        check_engine()
        pyi = check_pyinstaller()
        build(pyi)
        if not a.skip_smoke:
            smoke()

    pack(ver)
    return 0


if __name__ == "__main__":
    sys.exit(main())

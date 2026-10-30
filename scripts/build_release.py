# -*- coding: utf-8 -*-
"""一键出发布包：打包 exe，并生成两种发行形态。

    python scripts\\build_release.py                 # 绿色版 + 安装版（都做）
    python scripts\\build_release.py --zip           # 只做绿色版
    python scripts\\build_release.py --installer     # 只做安装版
    python scripts\\build_release.py --skip-pack     # 不重新打包，只压缩

两种形态
    dist\\AppProxy-v<版本>-win64.zip          绿色版：解压到任意目录，双击 AppProxy.exe
    dist\\AppProxy-v<版本>-win64-setup.exe    安装版：常规向导，可自选安装目录

绿色版里带一个 portable.txt 标记，程序看到它就把数据写在同目录的 AppProxyData
里，整个文件夹拷到哪都能用；安装版不带这个标记，数据落在安装时选定的目录
（默认 D:\\AppProxy，没有 D 盘则用当前用户的 LocalAppData）。

前置：pip install pyinstaller（脚本会自己检查）。安装版还需要 Inno Setup 6，
缺了会提示一句人话，不影响绿色版。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "AppProxy"
SPEC = ROOT / "packaging" / "AppProxy.spec"
ISS = ROOT / "packaging" / "installer.iss"
PKG = ROOT / "appproxy"
PORTABLE_FLAG = "portable.txt"

# 绿色版标记文件的内容 —— 用户点开就知道这个文件是干什么的，别删
PORTABLE_TXT = """这是「绿色版」标记文件，别删。

有它在，本软件的所有数据（代理订阅、日志、每个软件的独立配置）都会写在
同目录的 AppProxyData 文件夹里。所以：

  * 整个文件夹可以随便搬：换盘符、放 U 盘、拷到别的电脑，数据都还在；
  * 不用了直接删整个文件夹，系统里不留任何东西；
  * 想改回「数据放在别处」，删掉这个文件，程序会用安装版的默认位置。

软件说明：https://github.com/phoenixonworld/APPVPN
"""

README_TXT = """分应用代理管家 v{ver} · 绿色版
================================================

怎么用
------
1. 双击 AppProxy.exe（就在这个文件夹里），右下角出现托盘图标。
2. 第一次运行会直接弹出接线页：填你自己的代理订阅地址，
   或者选一个你已经在用的客户端的本地端口。
3. 左侧挑一个软件，右侧点「启动」，它就带着代理打开。
   用完关掉程序，后台几秒内自动收掉内核。

不需要装 Python，不需要装任何依赖，也不需要管理员权限。
整个文件夹拷到哪都能用（U 盘也行），数据写在旁边的 AppProxyData 里。

想放到桌面
----------
主界面左下角「设置」→ 打开「快捷方式」目录，把里面的图标拖到桌面；
或者直接从开始菜单拖（安装版才有开始菜单项）。

第一次打开如果提示缺东西
------------------------
主界面「设置」页最下面有「运行环境」，缺什么会列出来并给一个按钮：
  * 缺 VC++ 运行库 → 点一下自动下载官方安装包，装完回到软件就好；
  * 缺 TUN 驱动     → 点一下自动补上（只影响虚拟网卡模式，普通用法不受影响）。
命令行也能查：AppProxy.exe doctor

删掉它
------
直接删这个文件夹。数据就在 AppProxyData 里，一起删干净。

这个文件夹里有什么
------------------
AppProxy.exe         主程序（双击它）
_internal\\           运行时、界面资源、sing-box 内核，别删
portable.txt         绿色版标记，决定数据写在旁边，别删
使用说明.txt         本文件
LICENSE.txt          本软件的 MIT 许可证

许可
----
本软件：MIT（见同目录 LICENSE.txt）—— 可自由使用、修改、再分发。
内核来源：https://github.com/SagerNet/sing-box（GPL-3.0-or-later，许可见包内 _internal 下的 LICENSE）
虚拟网卡驱动：Wintun（https://www.wintun.net/）
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
    say("[1/5] 内核就位：%s（%.1f 兆字节）" % (found[-1].name, size))
    if not (found[-1].parent / "wintun.dll").is_file():
        say("      [!] 内核旁边没有 wintun.dll，TUN 模式会不可用")
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


def find_iscc() -> str | None:
    """找 Inno Setup 的命令行编译器。找不到返回 None（只影响安装版）。"""
    import os
    import shutil

    env = os.environ.get("ISCC")
    if env and Path(env).is_file():
        return env
    which = shutil.which("iscc")
    if which:
        return which
    for c in (rf"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
              rf"C:\Program Files\Inno Setup 6\ISCC.exe",
              rf"{os.environ.get('LOCALAPPDATA', '')}\Programs\Inno Setup 6\ISCC.exe"):
        if c and Path(c).is_file():
            return c
    return None


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
    say("[2/5] 打包（约 30 到 60 秒）")
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
    say("[3/5] 冒烟测试")
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
            % getattr(r, "returncode", "超时"))
        sys.exit(1)

    # 内核必须真的躺在包里，不然用户点了启动会报「找不到内核」
    inner = list((APP / "_internal" / "third_party" / "sing-box").glob("*/sing-box.exe"))
    if not inner:
        say("[x] 包里的内核丢了")
        sys.exit(1)
    say("      exe 能跑，内核在包内：%s" % inner[0].relative_to(APP))
    if not (inner[0].parent / "wintun.dll").is_file():
        say("      [!] 包里的 wintun.dll 丢了，TUN 会用不了")
    static = APP / "_internal" / "appproxy" / "webui" / "static" / "index.html"
    if not static.is_file():
        say("[x] 包里的界面文件丢了")
        sys.exit(1)
    say("      界面文件在包内：%s" % static.relative_to(APP))


def _write_flags_and_readme(ver: str) -> None:
    (APP / PORTABLE_FLAG).write_text(PORTABLE_TXT, encoding="utf-8")
    (APP / "使用说明.txt").write_text(README_TXT.format(ver=ver), encoding="utf-8")
    # 许可要能被用户看见，不能只躺在 _internal 里
    lic = ROOT / "LICENSE"
    if lic.is_file():
        (APP / "LICENSE.txt").write_text(lic.read_text(encoding="utf-8"), encoding="utf-8")


def pack_zip(ver: str) -> Path:
    """绿色版：带顶层文件夹的 zip，解压出来是一整个目录，不会被摊一地。"""
    say("[4/5] 压绿色版")
    _write_flags_and_readme(ver)

    top = "AppProxy"                      # zip 里的顶层目录名
    out = ROOT / "dist" / ("AppProxy-v%s-win64.zip" % ver)
    if out.exists():
        out.unlink()
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(APP.rglob("*")):
            if p.is_file():
                zf.write(p, "%s/%s" % (top, p.relative_to(APP).as_posix()))
                count += 1

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.with_suffix(".zip.sha256")).write_text(
        "%s  %s\n" % (digest, out.name), encoding="utf-8")

    raw = sum(p.stat().st_size for p in APP.rglob("*") if p.is_file())
    say("")
    say("=" * 60)
    say("  绿色版：%s" % out.name)
    say("  压缩前 %.1f MB / 压缩后 %.1f MB / %d 个文件"
        % (raw / 1048576, out.stat().st_size / 1048576, count))
    say("  校验值：%s" % digest[:32])
    say("=" * 60)
    return out


def sync_installer_assets(ver: str) -> None:
    """把版本号写进安装包的输入文件 —— 单一口径就是 `appproxy.__version__`。

    为什么要有这一步：`installer.iss` 里的 `#define AppVersion` 和
    `readme-installer.txt` 第一行都是硬编码的。忘了改的表现是：装完显示的版本
    还是上一版，产物名也对不上（实测 v0.6.5 编出 `AppProxy-v0.6.4-win64-setup.exe`），
    构建脚本只能报「没看到安装包产物」。手改两处迟早再忘，这里统一刷。
    """
    try:
        text = ISS.read_text(encoding="utf-8-sig")
        new = re.sub(r'(#define\s+AppVersion\s+")[^"]*(")',
                     lambda m: m.group(1) + ver + m.group(2), text)
        if new != text:
            ISS.write_text(new, encoding="utf-8-sig")   # 保留 BOM，Inno 才认中文
    except OSError as e:
        say("      [!] 没写上 installer.iss 的版本：%s" % e)

    readme = ISS.parent / "readme-installer.txt"
    try:
        lines = readme.read_text(encoding="utf-8").splitlines()
        if lines:
            lines[0] = re.sub(r"v[\d.]+", "v" + ver, lines[0], count=1)
            readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        say("      [!] 没写上 readme-installer.txt 的版本：%s" % e)


def pack_installer(ver: str) -> Path | None:
    """安装版：交给 Inno Setup 编译。没装 Inno 就跳过，不影响绿色版。"""
    say("")
    say("[5/5] 编安装版")
    iscc = find_iscc()
    if iscc is None:
        say("      [!] 没找到 Inno Setup，跳过安装版。绿色版不受影响。")
        say("          想生成安装包就装一次：winget install JRSoftware.InnoSetup")
        return None
    if not ISS.is_file():
        say("      [!] 缺 packaging\\installer.iss")
        return None

    sync_installer_assets(ver)
    r = subprocess.run([iscc, str(ISS)], cwd=str(ROOT))
    if r.returncode != 0:
        say("      [x] 安装包编译失败（看上面 ISCC 的输出）")
        return None

    out = ROOT / "dist" / ("AppProxy-v%s-win64-setup.exe" % ver)
    if not out.is_file():
        say("      [x] 没看到安装包产物 %s" % out.name)
        return None
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.with_suffix(".exe.sha256")).write_text(
        "%s  %s\n" % (digest, out.name), encoding="utf-8")
    say("")
    say("=" * 60)
    say("  安装版：%s" % out.name)
    say("  %.1f MB   校验值：%s"
        % (out.stat().st_size / 1048576, digest[:32]))
    say("=" * 60)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="打发布包")
    ap.add_argument("--skip-pack", action="store_true", help="不重新打包，只压缩")
    ap.add_argument("--skip-smoke", action="store_true", help="跳过冒烟测试")
    ap.add_argument("--zip", action="store_true", help="只做绿色版 zip")
    ap.add_argument("--installer", action="store_true", help="只做安装版 setup.exe")
    a = ap.parse_args()

    want_zip = a.zip or not a.installer
    want_setup = a.installer or not a.zip

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

    if want_zip:
        pack_zip(ver)
    if want_setup:
        pack_installer(ver)

    say("")
    say("  上传到 GitHub Releases 就能直接发给别人：")
    say("  https://github.com/phoenixonworld/APPVPN/releases")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""升级 / 修复 sing-box 内核 —— **不是必跑步骤**。

内核已经跟着仓库一起下发（`third_party/sing-box/`），clone 下来就能直接跑，
发布用的 zip 也把内核打在里面。这个脚本只在两种情况下用：

    1. 想升级到新版内核；
    2. 内核文件被人删了、或者 clone 时用了 `--depth` 之类把文件漏掉了。

    python scripts\\fetch_engine.py --check        # 只检查本地有没有
    python scripts\\fetch_engine.py                # 补齐 1.14.4 Windows x64（默认）
    python scripts\\fetch_engine.py --version 1.14.4

上游：https://github.com/SagerNet/sing-box
许可：GPL-3.0-or-later（LICENSE 会跟着一起解压出来，分发时必须保留）
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "third_party" / "sing-box"
DEFAULT_VERSION = "1.14.4"
ASSET = "sing-box-{v}-windows-amd64.zip"
URL = "https://github.com/SagerNet/sing-box/releases/download/v{v}/{asset}"


def local_binary(version: str | None = None) -> Path | None:
    base = DEST / ("sing-box-%s-windows-amd64" % version) if version else None
    if base and (base / "sing-box.exe").is_file():
        return base / "sing-box.exe"
    for p in sorted(DEST.glob("*/sing-box.exe"), reverse=True):
        return p
    return DEST / "sing-box.exe" if (DEST / "sing-box.exe").is_file() else None


def download(url: str, out: Path) -> None:
    print("下载 %s" % url)
    with urllib.request.urlopen(url, timeout=60) as resp:   # noqa: S310
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(out, "wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    sys.stdout.write("\r  %d%%  %.1f / %.1f 兆字节"
                                     % (pct, done / 1048576, total / 1048576))
                    sys.stdout.flush()
    print("")


def main() -> int:
    ap = argparse.ArgumentParser(description="拉取 sing-box 内核")
    ap.add_argument("--version", default=DEFAULT_VERSION)
    ap.add_argument("--check", action="store_true", help="只检查，不下载")
    a = ap.parse_args()

    found = local_binary(a.version)
    if a.check:
        print("本地内核：%s" % (found or "没有，跑一次 fetch_engine.py"))
        return 0 if found else 1
    if found:
        print("已经有了：%s（%.1f 兆字节）"
              % (found, found.stat().st_size / 1048576))
        return 0

    asset = ASSET.format(v=a.version)
    url = URL.format(v=a.version, asset=asset)
    DEST.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / asset
        try:
            download(url, zip_path)
        except Exception as e:  # noqa: BLE001  网络问题给一句人话
            print("下载失败：%s" % e)
            print("手动下载地址：%s" % url)
            print("解压后把 sing-box.exe 放到 %s\\sing-box-%s-windows-amd64\\"
                  % (DEST, a.version))
            return 1

        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                name = Path(member).name
                if not name or member.endswith("/"):
                    continue
                if name.lower() not in ("sing-box.exe", "license", "libcronet.dll"):
                    continue
                target_dir = DEST / asset[:-4]
                target_dir.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target_dir / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                print("  解出 %s" % (target_dir / name))

    found = local_binary(a.version)
    if not found:
        print("解压完了但没找到 sing-box.exe，检查压缩包结构")
        return 1
    print("完成：%s（%.1f 兆字节）" % (found, found.stat().st_size / 1048576))
    print("试一下：\"%s\" version" % found)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""把本仓库推到 GitHub（加远程 + 推送 + 失败给人话）。

    python scripts/push_to_github.py --url https://github.com/<用户名>/appproxy.git
    python scripts/push_to_github.py --url <地址> --branch main --dry

前提：本机已经能通过 HTTPS 认证 GitHub（PAT / GitHub Desktop / VS Code 登录都行）。
没有凭据时推送会卡在认证上 —— 脚本会提前提醒，不会干等。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(ROOT), text=True, capture_output=True)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="推送到 GitHub")
    ap.add_argument("--url", required=True, help="仓库地址，如 https://github.com/me/appproxy.git")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dry", action="store_true", help="只检查，不真的推送")
    a = ap.parse_args()

    rc, out = git("rev-parse", "--is-inside-work-tree")
    if rc != 0:
        print("这个目录还不是 git 仓库。先在本目录跑一次 git init && git add -A && git commit")
        return 1

    rc, out = git("rev-parse", "--verify", "HEAD")
    if rc != 0:
        print("还没有任何 commit，先提交一次再推。")
        return 1

    rc, dirty = git("status", "--porcelain")
    if rc == 0 and dirty:
        print("提醒：工作区有未提交的改动（这些不会进这次推送）：")
        for line in dirty.splitlines()[:10]:
            print("   ", line)

    print("[1/3] 分支改成 %s" % a.branch)
    rc, out = git("branch", "-M", a.branch)
    if rc != 0:
        print("   失败：", out)
        return 1

    rc, cur = git("remote", "get-url", "origin")
    if rc == 0 and cur:
        if cur != a.url:
            print("[2/3] origin 已存在（%s），改成新地址" % cur)
            rc, out = git("remote", "set-url", "origin", a.url)
        else:
            print("[2/3] origin 已是目标地址")
            rc = 0
    else:
        print("[2/3] 添加 origin")
        rc, out = git("remote", "add", "origin", a.url)
    if rc != 0:
        print("   失败：", out)
        return 1

    if a.dry:
        print("[3/3] 干跑模式，不推送。要推就再来一次去掉 --dry。")
        return 0

    print("[3/3] 推送 %s → %s" % (a.branch, a.url))
    p = subprocess.run(["git", "push", "-u", "origin", a.branch],
                       cwd=str(ROOT), text=True)
    if p.returncode == 0:
        web = a.url[:-4] if a.url.endswith(".git") else a.url
        print("\n完成。打开 %s 看看。" % web)
        return 0

    print("\n推送失败。按提示对号入座：")
    print("  · 提示 Authentication failed / 403")
    print("      → 密码推送早就停用了，要 PAT（个人访问令牌）：")
    print("        https://github.com/settings/tokens → Generate new token → 勾 repo")
    print("        推送时用户名填 GitHub 用户名，密码位置粘 token")
    print("  · 提示 remote already exists")
    print("      → git remote set-url origin <地址>")
    print("  · 提示 repository not found")
    print("      → 仓库还没在网页上建好，或地址里的用户名写错了")
    print("  · 提示 non-fast-forward / rejected")
    print("      → 远程已有内容（建仓库时勾了 Add README），先 git pull --rebase origin %s"
          % a.branch)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())

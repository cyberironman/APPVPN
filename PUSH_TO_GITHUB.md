# GitHub 仓库

**已经推上去了**，这里是后续维护说明。

| | |
|---|---|
| 仓库 | https://github.com/phoenixonworld/APPVPN |
| 当前状态 | **私有（Private）** |
| 默认分支 | `main` |
| 下载页 | https://github.com/phoenixonworld/APPVPN/releases/tag/v0.5.0 |
| 认证方式 | Git Credential Manager（已登录 `phoenixonworld`，以后不用再输密码） |

> 仓库里**包含 sing-box 内核**（`sing-box.exe` 78 MB + `libcronet.dll` 9 MB）。
> 推送时 GitHub 会打一条 `larger than GitHub's recommended maximum file size of 50.00 MB`
> 的警告 —— **这是警告不是错误**，推送照常完成。硬限制在 100 MiB。

---

## 以后改了代码怎么推

```bat
cd /d "<本仓库目录>"
git add -A
git commit -m "说明这次改了什么"
git push
```

凭据存在 Windows 凭据管理器里，不会再问密码。要换账号：

```bat
git-credential-manager github logout phoenixonworld
git-credential-manager github login
```

---

## 发新版本（Release）

普通用户拿到的就是这个包，**不是仓库源码**。

```bat
python scripts\build_release.py
```

产物 `dist\AppProxy-v<版本>-win64.zip`（约 60 MB）。然后：

**方式 A：网页**
仓库页右侧 **Releases → Draft a new release** →
Tag 填 `v0.5.1`（新建）→ 标题 → 把 zip 拖进附件框 → Publish release。

**方式 B：命令行（有 gh 时）**

```bat
gh release create v0.5.1 "dist\AppProxy-v0.5.1-win64.zip" --title "v0.5.1"
```

发完记得同步修改 `README.md` 里写死的版本号（现在写的是 `v0.5.0`）。

---

## 关于公开还是私有

现在是**私有**。这意味着：

- 别人打不开仓库页面，也**下不了 Release 里的 zip**（会 404）
- 要发给别人用，两条路：
  1. 转公开：仓库 **Settings → 最下方 Danger Zone → Change visibility → Public**
  2. 保持私有，直接把这个 zip 文件发给对方

> **专利提醒**：本项目的技术方案正在准备发明专利申请。专利法要求「新颖性」——
> 在申请日之前公开技术内容，自己公开的也会成为现有技术。
> 申请递上去之前，建议保持 Private。

---

## 推送之前一定检查

```bat
:: 运行数据绝不能进仓库（订阅地址里带 token）
git ls-files | findstr /I "lines.json profiles.json nodes- .log .lnk"

:: 期望：没有任何输出
```

数据目录是 `D:\AppProxy\`，物理上和代码分开；`.gitignore` 里 `/data/`、`/logs/`、
`/runtime/`、`/apps/`、`/icons/`、`/shortcuts/` 都挡着。注意这些必须带开头的斜杠，
否则会把 `appproxy/data/`（内置清单，需要进仓库）也一起忽略掉。

---

## 常见问题

**Q：commit 作者显示不对？**

本仓库单独的 `user.name` / `user.email` 已经设成 `phoenixonworld`：

```bat
git config user.name
git config user.email
```

要改成别的，改完提交即可；已经推上去的老 commit 需要 `git commit --amend --reset-author`
（只影响最新一条）或 `git rebase -i` 重写。

**Q：OneDrive 同步会不会把 `.git` 弄坏？**

正常使用没问题（都是普通文件）。但如果两台机器同时改同一分支，会产生冲突。
建议只在一台机器上提交，另一台只 `git pull`。

**Q：内核升级后仓库会不会越来越大？**

会。旧版本内核留在历史里。真要瘦身得用 `git filter-repo` 重写历史，
一般没必要 —— 一次内核升级大约多 35 MB。

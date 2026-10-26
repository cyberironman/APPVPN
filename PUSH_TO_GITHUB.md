# 推送到 GitHub

仓库已经**初始化好并提交了第一个 commit**（73 个文件），只差一个远程地址。

```
<本仓库目录>   ← 已是 git 仓库，第一个 commit 已建好（分支 main）
```

---

## 一、先决定：公开还是私有

| | 公开（Public） | 私有（Private） |
|---|---|---|
| 谁能看 | 所有人 | 只有你和受邀协作者 |
| 搜索收录 | 会被搜索引擎和 AI 训练集收录 | 不会 |
| 适合 | 想让人看到 / 招人 / 攒 star | 还没准备对外，或**专利申请尚未提交** |

> **提醒**：本项目的技术方案正在准备发明专利申请。专利法要求「新颖性」——
> 在申请日之前公开技术内容，自己公开的也会成为现有技术。
> 如果申请还没递上去，建议先选 **Private**，等受理后再转 Public（转公开是一条命令的事）。

---

## 二、建仓库（网页上操作，1 分钟）

1. 打开 https://github.com/new
2. **Repository name**：`appproxy` 或 `per-app-proxy`（英文名更通用，中文名也可以）
3. 选 **Public** 或 **Private**
4. **不要**勾选 "Add a README file"、".gitignore"、"license" —— 本地已经有了，勾了会冲突
5. 点 **Create repository**

建好后页面上会显示仓库地址，形如：

```
https://github.com/<你的用户名>/appproxy.git
```

---

## 三、推送（二选一）

### 方式 A：用本仓库自带的脚本

```bat
cd /d "<本仓库目录>"
python scripts\push_to_github.py --url https://github.com/<你的用户名>/appproxy.git
```

脚本会：设分支为 `main` → 加 `origin` → 推送 → 失败时给出具体原因。

### 方式 B：自己敲三条命令

```bat
cd /d "<本仓库目录>"
git branch -M main
git remote add origin https://github.com/<你的用户名>/appproxy.git
git push -u origin main
```

**关于认证**：密码推送早已停用，需要用 **PAT（个人访问令牌）**：

1. https://github.com/settings/tokens → Generate new token (classic)
2. 勾 `repo` 权限，有效期选 90 天
3. 推送时用户名填 GitHub 用户名，密码位置**粘贴这个 token**

或者用 GitHub Desktop / VS Code 的 GitHub 登录，它们会自动接管凭据。

---

## 四、推送之后建议做的三件事

1. **填 About**（仓库首页右上角齿轮）：一句简介 + 关键词
   - 简介建议：`把「全机代理」拆成「按程序代理」的 Windows 桌面工具（Python + sing-box）`
   - Topics 建议：`proxy` `sing-box` `windows` `pyside` `per-app-proxy` `tray` `vless`
2. **确认没有敏感文件被推上去**：
   ```bat
   git ls-files | findstr /I "lines.json profiles.json nodes- .log .lnk"
   :: 期望：没有任何输出
   ```
3. **（可选）把 sing-box 内核挂到 Releases**：仓库里没有 79 兆字节的 `sing-box.exe`（太大），
   读者用 `python scripts\fetch_engine.py` 自己拉。如果你想让别人开箱即用，
   可以发一个 Release，把 `sing-box-1.14.4-windows-amd64.zip` 作为附件传上去，
   再在 README 里加一行下载链接。

---

## 五、常见问题

**Q：提交者信息不对，想改成自己的 GitHub 邮箱。**

还没推送之前随便改：

```bat
git config user.name "你的名字"
git config user.email "你的邮箱"
git commit --amend --reset-author --no-edit
```

推送之后也可以改，但需要 force push（`git push -f`），一个人用的仓库没风险。

**Q：想把 `D:\AppProxy` 也传上去吗？**

**绝对不要**。那里是你的订阅地址、节点密码、token。`.gitignore` 已经把相关文件名都挡住了，
但最稳的做法是记住一句：这个仓库只放 `appproxy\` + `scripts\` + `tests\` + `tools\` + `docs\` + `third_party\`。

**Q：以后改了代码怎么再推？**

```bat
git add -A
git commit -m "说明这次改了什么"
git push
```

**Q：换电脑 / OneDrive 同步会不会把 .git 弄坏？**

OneDrive 同步 `.git` 目录在正常使用下没问题（都是普通文件）。但如果两边同时改，
可能产生冲突。建议：**只在一台机器上提交**，另一台只拉取。

# 推送到 GitHub

仓库已经**初始化好并提交了**（含 sing-box 内核，约 130 个文件），只差一个远程地址。

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
3. **发一个 Release，把打包好的 zip 传上去**（这才是给普通用户的入口）：

   仓库里已经带了完整的打包脚本，一条命令出发布包：

   ```bat
   python scripts\build_release.py
   ```

   产出 `dist\AppProxy-v0.5.0-win64.zip`（59.9 兆字节，内含 exe、Python 运行时、sing-box 内核）。
   然后到仓库页面右侧 **Releases → Draft a new release**：

   | 字段 | 填什么 |
   |---|---|
   | Choose a tag | `v0.5.0`（新建） |
   | Release title | `分应用代理管家 v0.5.0` |
   | 描述 | `下载下方 zip → 解压 → 双击 AppProxy.exe。不需要装 Python。` |
   | Attach binaries | 把 `AppProxy-v0.5.0-win64.zip` 拖进去 |

   发布后 README 里那个 `../../releases` 链接就会指向下载页。

---

## 四之一、关于仓库体积（推之前先知道）

仓库里**包含了 sing-box 内核**（`sing-box.exe` 79 MB + `libcronet.dll` 9 MB），
这是刻意的：clone 下来就能直接跑，不需要任何额外步骤。

- push 时 GitHub 会打一条 `warning: File ... is 78.30 MB; this is larger than GitHub's
  recommended maximum file size of 50.00 MB`。**这是警告，不是错误**，推送会正常完成。
- GitHub 真正的硬限制是单文件 **100 MiB**，79 MB 在允许范围内。
- 提交进 git 之后对象库压缩到约 35 MB（exe 本身压缩率高），一次 clone 多花十几秒。
- 内核将来升级时，旧的那份仍留在历史里。真要彻底瘦身，得重写历史
  （`git filter-repo`），一般没必要。

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

# 更新日志

版本号对应开发日志里的「步骤」，一个步骤一个可用版本。

---

## v0.5.0 补充：改成真正开箱即用的分发方式

**之前的问题**：内核 79 MB 被排除在仓库外，README 让用户 clone 完再跑
`python scripts\fetch_engine.py`。这不叫软件 —— 一个要让用户自己跑脚本才用得起来的东西，
等于把安装工作转嫁给用户。

**改**

- **sing-box 内核进仓库**。GitHub 对单文件只是 50 MB 警告、100 MiB 才硬拒，
  79 MB 完全推得上去；对象库压缩后约 35 MB。`clone` 下来就有内核，不需要额外步骤。
  `scripts/fetch_engine.py` 降级为「升级 / 修复内核」用，不再是必跑。
- **打包成 exe 分发**：`packaging/AppProxy.spec` + `packaging/entry.py` +
  `scripts/build_release.py`，一条命令出 `AppProxy-v0.5.0-win64.zip`（59.9 MB）。
  用户下载 → 解压 → 双击 `AppProxy.exe`，**不装 Python、不装依赖、不跑脚本**。
  `onedir` 而不是 `onefile`：不用每次启动把 88 MB 内核解压到临时目录，秒开。
- **`appproxy/selfrun.py`**：统一「把本程序以某个子命令再拉起一次」。
  打包后没有 `pythonw.exe` 和 `.py` 文件了，原来写死解释器路径的三处（快捷方式、
  提权子进程、托盘切界面）全都会断。命令行新增内部子命令 `elevate`。
- **数据目录回退**：`home()` 原来写死 `D:\AppProxy`，搬到没有 D 盘的机器上会直接失败。
  改为 `APPPROXY_HOME` > `D:\AppProxy`（有 D 盘就用）> 程序旁边的 `AppProxyData`。
- 「我们自己」的进程名从写死的 `python.exe` 改为动态取（`selfrun.self_process_names()`），
  否则打包后 `AppProxy.exe` 会被当成「某个档案在运行」，内核永远不回收。

**测试**

- 新增 `tests/test_selfrun.py`（4 项）、`tests/test_paths.py`（4 项），单测总数 **111 项**。
- 端到端：把 zip 解压到干净目录后直接 `AppProxy.exe` 启动 —— 内核识别正常
  （`sing-box version 1.14.4`）、界面服务 200、`needs_setup=true`、首页 47 KB。

---

## v0.5.0 — 步骤 5：TUN 兜底 + 需代理清单 + 新用户接线

**新增**

- **TUN 虚拟网卡模式**（高级选项，默认关）：`tunconf.py` / `tunmgr.py` / `elevate.py`。
  按进程名分流，未登记的程序一律直连；开 TUN 自动关系统代理，关时还原；不做 kill-switch。
  提权用「无触发器的最高权限计划任务」，只弹一次 UAC。
- **「明显需要代理的软件」清单**：`appproxy/data/apps_need_proxy.json`，32 条（24 条可按进程名识别：
  Chrome、Telegram、Discord、ChatGPT、Codex CLI、Claude、Cursor、Notion、Spotify、Steam…）。
  只按进程名匹配，匹配不上不猜；可被数据目录里的同名文件整份覆盖。
- **新用户接线页**：一条线路都没有时弹引导，三条路（订阅链接 / 节点分享链接 / 本地端口）
  最终都产出同一条 `Line`，不写特例。
- **一键 TUN 自检**：`tools/verify_tun.py`，读线路 → 生成配置 → `sing-box check` → 装任务 →
  起内核 → 查网卡 → 出报告（`D:\AppProxy\logs\tun-verify.txt`）。
- **发布前清理**：`python -m appproxy.cli cleanup [--yes]`，先备份再清空线路/节点/配置/快捷方式，
  档案本身保留（线路绑定改成直连）。配套 `docs/发布前检查清单.md`。
- 命令行新增 `tun` / `appdb` / `cleanup`。

**改**

- `Node` 支持 `socks` / `http` 出站 —— 「本地已有代理客户端」不必写特例。
- `/api/tun/*` 用独立的锁：等 UAC 可能几十秒，不能连带卡住「启动 / 关闭软件」。
- 提示语全面翻成人话（拒绝访问 → 说明怎么给权限，不抛 traceback）。

---

## v0.4.1 — 步骤 4.5 / 4.6：托盘收敛 + 双渠道进设置

**改**

- 托盘顶层砍到 6 项（打开主界面 / 设置… / 启动所有软件 / 关闭所有软件 / 系统代理 / 退出）。
  砍掉的「线路 / 应用 / 开机自启 / 打开目录」不是删功能，是**退回主界面**。
- 设置两个正门：托盘「设置…」与主界面左下角整行「设置」（+ 右上角齿轮、`#settings` 深链）。
- 界面已开着时，托盘「设置…」不再开新窗口，而是**切到设置页 + 任务栏闪 5 下**，
  菜单文案也随状态变（「设置（切到界面并闪烁提示）」）。
- 补齐「新档案默认线路」设置入口（此前后端没返回该字段，界面上存不下来）。

---

## v0.4.0 — 步骤 4：主界面 + 内核按需化

**新增**

- **WeChat 式主界面**：FastAPI + 单页 HTML，左侧软件列表、右侧详情，设置也在主界面。
  窗口外壳用 Chrome/Edge `--app` 模式（`--native` 保留 pywebview 通道）。
- **内核按需池**：`pool.py`（登记表 + 60 秒宽限 + 5 秒 GC）、`procwatch.py`（进程探活）。
- **开关对象从「线路」改为「软件」**：点启动只拉它那条线路（约 1 秒，不测速）；
  关程序后 5 秒内自动回收；手动起的内核不受影响。
- 头像 / 图标、搜索、程序库（扫描已安装程序）、任务进度。

**修**

- WebView2 白屏：系统代理没跳过 localhost → 外壳 `--no-proxy-server`，`sysproxy` 写 `ProxyOverride`。
- `--user-data-dir` 参数带空格时整个参数被加引号 → 正则改为「引号内 / 裸值」二选一。
- 档案 id 从中文名 slug 会全部撞车 → 改用 exe 文件名做 id。

---

## v0.3.0 — 步骤 3：托盘 + 系统级开关

**新增**

- 托盘（`pystray`）：单实例锁（占端口 10896）+ 控制通道、后台线程池、图标四色状态。
- 系统代理接管（`HKCU` + `InternetSetOption` 广播，带备份还原）、开机自启（只动「启动」文件夹）。
- 已安装程序扫描（`appscan`，181 个程序约 1.9 秒）、目录快捷入口。

---

## v0.2.0 — 步骤 2：应用档案 + 多图标

**新增**

- 档案模型与存储（`profiles.py`）、启动器（`launcher.py`）。
- 浏览器走 `--user-data-dir` + `--proxy-server`，其余程序注入 `HTTP_PROXY` / `HTTPS_PROXY`。
- 多尺寸 `.ico` 生成（`icons.py`）、快捷方式（`shortcut.py`，`win32com` 优先 `pylnk3` 兜底）。

---

## v0.1.0 — 步骤 1：核心层

**新增**

- 订阅拉取与解析（vless / vmess / trojan / ss）、节点模型、`urltest` 自动择优。
- sing-box / xray 两套配置生成与校验、内核起停、批量测速、连通性探测。
- 线路注册表与节点快照（`lines.json` / `nodes-*.json`）。

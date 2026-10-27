# -*- coding: utf-8 -*-
"""分应用代理管家（核心层 + 应用档案层）。

步骤 1（v2ray 那套基础代理能力）：
    订阅   subscription.load(url)          -> 节点列表
    配置   singconf/xrayconf.build(...)    -> 内核配置字典
    内核   coreproc.start/stop/status      -> 进程与端口
    测速   latency.measure(nodes, binary)  -> 每节点毫秒

步骤 2（同软件多代理多图标）：
    档案   profiles.AppProfile / ProfileStore  -> 程序 + 线路 + 独立数据目录
    启动   launcher.main(["--profile", id])    -> 缺内核先拉起，再带参数启动程序
    图标   icons.render(profile)               -> 底色=软件、角标=线路的多尺寸 ico
    快捷   shortcut.install(profile)           -> 桌面 .lnk，双击即用

步骤 3（托盘与系统级开关）：
    托盘   tray.main([])                       -> 右下角图标，只留日常 6 项
    系统代理 sysproxy.enable/disable/status     -> 改注册表 + 广播，带备份还原
    自启   autostart.enable/disable/status     -> 只动「启动」文件夹
    扫描   appscan.scan()                      -> 开始菜单里能启动的程序

步骤 4（主界面 + 内核按需化）：
    界面   gui.main([])                        -> 本地 HTTP 服务 + Chrome --app 独立窗口
    探活   procwatch.snapshot()                -> 每个档案的程序还开着吗
    内核池 pool.ensure_core / gc / release     -> 有软件用才起，没人用就自动退场
    开关语义：对象是**软件**，不是线路。
             点启动 = 只拉它那条线路（约 1 秒，不测速）；
             关掉程序 = 后台 5 秒内发现没人用，内核自己退出。

步骤 4.5（入口收敛）：
    两个门  gui.open_view("profile" / "settings")
            -> 窗口已开：发一条指令让它切页 + 抢到最前；
            -> 没开：新起进程，URL 带 #settings 直接落地。
    定位    gui.port 路标 + /api/ping 的 app 标记，托盘点「设置…」才找得到界面。

步骤 5（TUN + 需代理清单 + 新用户接线）：
    清单   appdb.find("chrome.exe")            -> 这程序是不是「明显需要代理」
    TUN    tunconf.build(groups, app_rules)     -> 虚拟网卡配置（未登记的进程一律直连）
           tunmgr.start / stop / status        -> 起停 + 系统代理交接（开 TUN 先关系统代理）
           elevate.install / start / end       -> 计划任务最高权限，只弹一次 UAC
    接线   setup.from_subscription / uri / local -> 用户自己的代理接进来（三种方式同一条路）
    清理   cli cleanup --yes                    -> 上线前删私人代理（先备份，再清空）

上层只调用这些，不直接碰内核。
"""

__version__ = "0.6.1"

from .models import Line, Node  # noqa: F401
from .profiles import AppProfile, LaunchPlan, ProfileStore  # noqa: F401
from .registry import Registry  # noqa: F401

; 分应用代理管家 —— Windows 安装包脚本（Inno Setup 6）
;
; 编译：
;     "C:\Users\<你>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" packaging\installer.iss
; 或者直接跑：
;     python scripts\build_release.py --installer
;
; 设计取舍
;   * PrivilegesRequired=lowest —— 默认装到「当前用户」目录，全程不弹 UAC。
;     普通用户、公司电脑（没有管理员）都能装。想装到 Program Files 的用户
;     可以在向导第一页选「为所有用户安装」，那时才需要管理员。
;   * DisableDirPage=no —— 安装目录必须让用户自己选（用户明确要求）。
;   * 数据就放安装目录下的 AppProxyData，**不额外问一遍**。装在哪个目录，
;     数据就在哪，整个文件夹搬走数据跟着走；想换盘在软件「设置」里改。
;   * 卸载时问一句要不要删数据 —— 默认「不删」，重装不会丢订阅。

#define AppName        "分应用代理管家"
#define AppShortName   "AppProxy"
#define AppVersion     "0.6.5"
#define AppExe         "AppProxy.exe"
#define AppPublisher   "phoenixonworld"
#define AppUrl         "https://github.com/phoenixonworld/APPVPN"

[Setup]
; AppId 决定「升级同一个软件」还是「装两个」—— 一旦发布就不要再改
AppId={{8F3C2A16-5B7D-4E29-9C41-6D2B8E5A7F03}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppVersion}

DefaultDirName={autopf}\{#AppShortName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 让用户自己选安装位置
DisableDirPage=no
; 默认免管理员；用户可在向导里改成「为所有用户安装」
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

OutputDir=..\dist
OutputBaseFilename={#AppShortName}-v{#AppVersion}-win64-setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 安装时如果程序正在跑，先关掉再装（升级场景）
CloseApplications=yes

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Win10 1809 之后才有的接口（TUN 虚拟网卡要用）
; 不要把这条写得比实际需要更高，否则 Win10 早期版本装不上

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面创建快捷方式"; GroupDescription: "额外选项："; Flags: checkedonce
Name: "autostart";   Description: "开机自动启动（只放托盘，不弹窗口）"; GroupDescription: "额外选项："; Flags: unchecked

[Files]
; 整份 dist\AppProxy 打进去：exe + Python 运行时 + 界面 + sing-box 内核 + wintun 驱动
; 排除两样：portable.txt（绿色版标记，带上就不是安装版了）、
; 使用说明.txt（那是绿色版的文案，安装版另有一份）
Source: "..\dist\AppProxy\*"; DestDir: "{app}"; \
    Excludes: "portable.txt,使用说明.txt"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; 安装版自己的说明
Source: "readme-installer.txt"; DestDir: "{app}"; DestName: "使用说明.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; 程序照着这个值决定数据放哪（见 appproxy/settings.py 的 home()）。
; 默认就是「安装目录\AppProxyData」—— 装在哪个目录，数据就在哪。
; createvalueifdoesntexist：升级安装时不要覆盖用户已经改过的选择。
Root: HKCU; Subkey: "Software\AppProxy"; ValueType: string; ValueName: "InstallDir"; \
    ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\AppProxy"; ValueType: string; ValueName: "DataDir"; \
    ValueData: "{app}\AppProxyData"; Flags: uninsdeletekey createvalueifdoesntexist

[Dirs]
; 装到 Program Files 时（选「为所有用户安装」）这个子目录要给普通用户写权限，
; 否则程序写不进订阅和日志
Name: "{app}\AppProxyData"; Permissions: users-modify

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[Code]
{ 卸载时问一句要不要连配置一起删。
  默认「否」：重装能接着用；选「是」才真删。 }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if not RegQueryStringValue(HKEY_CURRENT_USER, 'Software\AppProxy', 'DataDir', Dir) then
      Dir := ExpandConstant('{app}\AppProxyData');
    if DirExists(Dir) then
    begin
      if MsgBox('是否同时删除配置和日志？' + #13#10 + #13#10 +
                Dir + #13#10 + #13#10 +
                '选「否」会保留这些数据，以后重装能接着用。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Dir, True, True, True);
    end;
  end;
end;

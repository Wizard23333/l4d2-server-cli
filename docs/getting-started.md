# 第一次和朋友开服

本教程适用于 `v0.1.0-alpha.3`。准备一台可通过 SSH 登录、具有 sudo 权限的 Linux x86_64 主机，运行 glibc 和 systemd；首次安装至少预留 20 GiB，三方图和备份需要额外空间。系统范围见 [Linux 支持说明](linux-support.md)。你和朋友需要各自的正版 L4D2 客户端。

Windows 玩家可先用终端 `ssh YOUR_SSH_HOST` 登录自己的 Linux 主机，后续命令在 Linux 上执行。本版不在 Windows 上创建游戏服务，也不代为购买云主机或修改云安全组。

## 1. 安装 CLI

在 [alpha.3 下载页](https://github.com/Wizard23333/l4d2-server-cli/releases/tag/v0.1.0-alpha.3) 下载 Linux 包和同名校验文件，按照 [README 安装步骤](../README.md#可执行包) 校验、解压。在解压目录执行：

```bash
sha256sum -c FILES.sha256
sudo bash ./install.sh
l4d2 --version
l4d2 setup --help
```

版本应为 `0.1.0a3`。内置 Python，无需自行安装 pip；整个程序目录需保留，不能只复制可执行文件。

## 2. 启动中文引导

```bash
sudo l4d2 setup
```

新建房间默认采用官方四人合作、死亡中心、普通难度、公开直连，默认不开机启动。回车使用默认值；可以输入战役编号选择其他官方战役。当前房名仅支持英文字母/数字等 ASCII 字符。

加入方式可选公开或 Steam 组大厅。**公开房间可被其他玩家加入，Steam 组优先也不是严格好友白名单。** 本版不承诺密码私服；认证账号密码和 RCON 密码是不同用途。

安装来源有三种常用选择：官方在线、已准备的完整 SteamCMD 工具目录、由 `l4d2 download server` 制作的完整游戏包。普通 VPK、客户端游戏目录和单独 SteamCMD 压缩包都不是完整服务端游戏包。高级来源参数仍可通过 `setup --help` 查看。

引导先检查目标、依赖、空间和必要下载网络，列出将应用的计划；确认后才安装、保存配置，并按你的选择启动。游戏下载可能较长，进度会持续显示；不要把一次网络预检通过当作完整下载保证。

仅预览或只安装不启动：

```bash
sudo l4d2 setup --name "Friday Team" --port 27015 --dry-run
sudo l4d2 setup --no-start
```

自动化可显式填写设置并确认执行；`--json` 不会读取交互答案：

```bash
sudo l4d2 setup --name "Friday Team" --port 27015 --map c1m1_hotel \
  --difficulty normal --visibility public --dry-run --json
sudo l4d2 setup --name "Friday Team" --port 27015 --map c1m1_hotel \
  --difficulty normal --visibility public --yes --json
```

`--timeout` 默认 3600 秒；必要网络预检有自己的短超时，确认后的安装/启动共用该执行预算，启动就绪检查最多 120 秒。安装失败可用相同选项重试，已完成内容会按原有安装恢复规则复用。中途取消或失败不会自动删除文件或回滚已完成步骤。

## 3. 让朋友加入

公开模式完成后，状态会显示类似：

```text
connect YOUR_SERVER_IP:27015
```

把 `YOUR_SERVER_IP` 换成实际公网地址，端口使用实际配置值。主机防火墙和云安全组需放行该游戏 UDP 端口；家庭路由器后需要端口转发，运营商 NAT 可能使外部直连不可用。CLI 不会替你改变这些设置。

你和朋友启动游戏，在游戏设置中启用开发者控制台，输入状态中给出的连接命令。Steam 组模式按引导给出的组服务器/大厅方式加入，使用正确的短数字组 ID。

本机服务就绪不代表外网玩家已经能进。至少请一位外网朋友实际连接，确认能进入地图、过关和重连。如果使用 `--no-start`，先运行 `sudo l4d2 server start`。

## 4. 失败后继续

| 现象 | 下一步 |
| --- | --- |
| 环境不支持 | 在支持的 Linux 主机运行，检查 systemd、架构及依赖；使用 `l4d2 doctor` |
| 网络预检/下载失败 | 查看失败阶段；使用 `l4d2 network check`，排除网络后用原选项重试；可以显式选择本地来源 |
| `No subscription` / 账号认证要求 | 以普通用户完成下方认证，再用同一账号与缓存继续；不要在聊天或普通参数里填写密码 |
| 已有非空目录或同名服务 | 新服使用空目录；已有服务器用 `room scan` / `room adopt`；不删除原服来绕过保护 |
| main 已在运行 | 引导会停止；用 `server status` 查看加入方式，配置变化使用 `server configure`，重启另行决定 |
| 下载完成后启动失败 | 配置和游戏保留；检查 `server status`、`server logs --lines 50`，修复后 `server start` |
| 本机就绪但朋友连不上 | 检查实际公网地址、游戏 UDP 端口、云安全组、本机防火墙及 NAT；不要重复安装游戏 |

默认尝试匿名下载。只有 Steam 明确要求账号授权时，先退出 sudo，以普通用户准备独立缓存：

```bash
l4d2 auth login --fresh --cache "$HOME/.local/share/l4d2-cli/setup-login"
```

按原生提示完成账号、密码和 Steam Guard。随后用已验证的同一账号与实际缓存绝对路径重跑引导，并保留此前房间设置：

```bash
sudo l4d2 setup --steam-user YOUR_STEAM_LOGIN_NAME \
  --steamcmd-path /home/YOUR_USER/.local/share/l4d2-cli/setup-login/steamcmd/steamcmd.sh
```

`--fresh` 只接受新目录或空目录，已有缓存不要覆盖。账号认证成功不保证游戏下载权限或网络；详细说明见 [Steam 登录](steam-auth.md)。

## 5. 从 alpha.1 升级

下载并校验 alpha.3 完整目录包，在新包目录运行 `sudo bash ./install.sh`。安装器检查目标后切换 CLI 全局入口，保留旧 CLI 安装；遇到同名无关命令会拒绝覆盖。

CLI 升级不更新游戏、不重启房间，也不要求重新执行 `setup`。先检查 `l4d2 --version`、`l4d2 capabilities --json`、`sudo l4d2 server status`。旧版仅便携运行时，也可直接用新包的 `./l4d2` 检查现有状态。已有可选 Web 安装升级后，按 [面板说明](web-panel.md) 更新其服务入口。

好友邀请、连接故障、地图分享、名单和定时维护见 [好友开黑与日常维护](players-and-maintenance.md)。其他操作见 [运维指南](operations.md)。安装 Web 或 Agent Skill 都是可选步骤。

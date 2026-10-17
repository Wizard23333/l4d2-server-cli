# L4D2 Server CLI

在 Linux 上安装和管理 Left 4 Dead 2 专用服务器的命令行工具。从首次部署到多房间、地图、插件和备份，使用同一套 CLI 完成日常运维。

所有运维能力均可独立运行，无需部署 Web 面板；支持操作预览、后台任务和 JSON 输出，便于手动使用或接入自动化工具。

[功能概览](#功能概览) · [安装](#安装) · [快速上手](#快速上手) · [日常运维](#日常运维) · [文档](#文档)

## 功能概览

| 能力 | 功能 | 命令 |
| --- | --- | --- |
| 游戏部署 | 官方服务端下载安装、Steam 认证、本地工具与完整游戏包导入 | `server`、`auth`、`download` |
| 房间管理 | 接管已有服务器，创建多实例，配置公开/Steam 组模式，逐房间启停和查询 | `room` |
| 地图战役 | Workshop 搜索与合集/多 Part 解析、GameMaps 安装、VPK 导入与校验提取、默认地图与在线切图、卸载重装 | `map` |
| VPK Mod | 搜索、下载安装、本地导入、启用、禁用和卸载重装 | `addon` |
| 服务器插件 | 安装 MetaMod、SourceMod、Left4DHooks，管理 `.smx` 插件，编译和部署 SourcePawn 源码 | `plugin` |
| 管理员与控制台 | 管理 SourceMod、Left4Bots、Admin System 管理员记录，执行 RCON 指令 | `admin`、`rcon` |
| 后台任务 | 提交长时间操作，查看状态、下载进度和日志，取消、重试及删除任务 | `task` |
| 资源传输与备份 | 来源 Manifest、离线 VPK ZIP、旧面板来源记录导入，完整状态与插件栈备份恢复 | `transfer`、`backup` |
| 诊断与维护 | 主机和房间检查、日志、磁盘占用、缓存清理、mission BOM 与权限修复、事务恢复 | `doctor`、`storage`、`repair` |
| 网络与自动化 | 网络预检、内容下载代理、Steam API key、CDN hosts 和 IPv4 优先配置，输出运维能力清单 | `network`、`capabilities` |

每个命令均可通过 `--help` 查看参数，例如 `l4d2 room create --help`。完整操作指南见 [CLI 运维说明](docs/operations.md)。

## 安装

### 环境要求

- Linux x86_64、glibc，以及运行中的 systemd。可执行包要求 glibc 2.31+；源码或 wheel 需要 Python 3.8+。
- 安装游戏需准备 SteamCMD/游戏的 32 位运行库；首次安装按至少 20 GiB 空闲空间预检，第三方地图和备份需额外空间。
- 安装、配置及服务管理使用 `sudo`；游戏以普通系统用户运行。Steam 账号登录使用普通用户执行。

Ubuntu 20.04 已完成核心流程实测；其他发行版适配、依赖及验证范围见 [Linux 支持说明](docs/linux-support.md)。

### 可执行包

可执行包内置 Python，无需系统 Python、venv 或 pip。将完整目录包解压，进入包含 `l4d2`、`_internal/`、`FILES.sha256` 和 `install.sh` 的目录：

```bash
sha256sum -c FILES.sha256
sudo bash ./install.sh
l4d2 --version
l4d2 capabilities --json
```

安装器将完整程序复制到 `/usr/local/lib/l4d2-server-cli/`，注册 `/usr/local/bin/l4d2`。随后可在任意目录使用 `l4d2` 或 `sudo l4d2`；升级时验证新目录后切换入口，保留旧安装。安装 CLI 不修改游戏配置或启停房间。

也可保留整个目录，用 `./l4d2` 便携运行。构建方式见 [Linux 打包说明](scripts/packaging/README.md)。

### 源码或 wheel

在项目根目录创建虚拟环境并安装：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/l4d2 --help
```

已有本地 wheel 时，将安装命令替换为：

```bash
.venv/bin/python -m pip install --no-index --no-deps /path/to/l4d2_server_cli-VERSION-py3-none-any.whl
```

`VERSION` 为实际文件版本号。源码安装的构建依赖可能需要联网；直接运行 `python3 bin/l4d2` 无需下载 Python 运行依赖。

下文以全局命令 `l4d2` 为例。便携运行使用 `./l4d2`，源码和 wheel 使用虚拟环境中入口的绝对路径；需要管理员权限时在该入口前加 `sudo`。

## 快速上手

### 新建服务器

检查下载网络，安装游戏，保存配置后启动默认房间：

```bash
l4d2 network check
sudo l4d2 server install --dir /opt/l4d2
sudo l4d2 server configure --name "My L4D2 Server" --port 27015 \
  --map c1m1_hotel --mode coop --difficulty normal --visibility public
sudo l4d2 server start
sudo l4d2 server status
```

CLI 安装的默认房间 ID 为 `main`，服务为 `l4d2-main.service`。安装完成后保持停止、默认不开机启动；`server configure` 不带设置参数时可进入交互向导。

默认尝试匿名下载。若 Steam 返回 `No subscription` 等授权错误，需先以普通用户完成账号认证，再使用同一账号和缓存安装，见 [Steam 登录说明](docs/steam-auth.md)。账号登录不改变房间的公开/Steam 组设置。

放行主机防火墙、云安全组及必要的 NAT 端口转发后，公开房间可从游戏控制台连接：

```text
connect YOUR_SERVER_IP:27015
```

本机显示就绪表示游戏查询和端口归属已确认；公网连通及实际加入需要从外部客户端验证。

### 接管已有服务器

已有游戏目录使用 `room scan` 和 `room adopt` 登记。按实际情况替换路径、服务名和运行用户，先预览接管计划：

```bash
sudo l4d2 room scan --dir /opt/l4d2 --json
sudo l4d2 room adopt --id room1 --dir /opt/l4d2 \
  --service l4d2 --script /opt/l4d2/start_l4d2.sh \
  --cfg /opt/l4d2/left4dead2/cfg/server.cfg --user steam --dry-run
```

核对后将 `--dry-run` 替换为 `--yes`。接管只登记状态，不重新下载或重启游戏；启动脚本与服务的接管条件见 [房间登记与旧服接管](docs/operations.md#房间登记与旧服接管)。

## 日常运维

除首次安装外，房间操作通过 `--room ID` 选择目标，默认 `main`。`server` 命令管理默认房间，`room` 命令用于统一管理所有已登记房间。

以下变更示例使用 `--dry-run` 查看计划；确认目标和影响后，替换为 `--yes` 执行。配置、地图和插件保存后不会自动重启，需单独选择房间启动或重启。新建房间保持停止且不开机启动。

### 房间与可见性

```bash
sudo l4d2 room list --json
sudo l4d2 room status --room main --json
sudo l4d2 room logs --room main --lines 100
sudo l4d2 room create --id room2 --from-room main --port 27016 \
  --name "Room 2" --dry-run
sudo l4d2 room configure --room main --visibility group \
  --steam-group-id YOUR_SHORT_GROUP_ID --dry-run
sudo l4d2 room restart --room main --dry-run
```

公开模式使用 `--visibility public`；Steam 组模式需要短数字组 ID，通过组服务器列表/大厅加入。组模式采用原版组优先规则，不是严格成员白名单，详见 [房间配置](docs/configuration.md#公开与-steam-组模式)。

同一游戏目录下的房间共享地图、Mod 和插件，端口、配置与服务各自独立。变更计划会列出受影响房间。

### 地图、Mod 与插件

```bash
sudo l4d2 map search "Run to the Hills" --json
sudo l4d2 map install 2232584588 --room main --dry-run
sudo l4d2 map list --room main --json
sudo l4d2 map change YOUR_INSTALLED_MAP --room main --dry-run
sudo l4d2 addon import --room main --file /path/to/mod.vpk --dry-run
sudo l4d2 plugin stack --room main --dry-run
sudo l4d2 plugin list --room main --json
```

`YOUR_INSTALLED_MAP` 使用地图清单中的实际名称。在线切图需要为目标房间配置 RCON；密码通过权限为 `0600` 的私有文件输入。VPK 校验与分卷提取、SourcePawn 编译、管理员及 RCON 指令示例见 [CLI 运维说明](docs/operations.md)。

### 后台任务、传输与备份

```bash
sudo l4d2 task submit --dry-run -- map install 2232584588 --room main --timeout 1800
sudo l4d2 task list --json
sudo l4d2 task show YOUR_TASK_ID --json
sudo l4d2 transfer import --room main --format zip --file /path/to/content.zip --dry-run
sudo l4d2 backup create --room main --scope full --dry-run
```

任务提交后可关闭终端，通过返回的任务 ID 查看状态、当前文件下载进度和日志，或取消、重试操作。

资源传输支持来源 Manifest 和离线 VPK ZIP。完整状态备份保存配置、服务、插件栈、源码、管理员及 Mod 启停态；地图保存来源记录以便重新下载。备份可能包含秘密，需私有保存；恢复前必须停止所有共享游戏目录的房间，恢复后显式启动。详见 [资源传输与备份恢复](docs/operations.md#资源传输和完整备份恢复)。

### 诊断与网络

```bash
sudo l4d2 doctor
l4d2 network check
sudo l4d2 room doctor --room main
sudo l4d2 storage usage --room main --json
sudo l4d2 network status --json
```

支持结构化输出的命令可加 `--json`，进度写入 stderr。状态命令退出成功表示取得报告，自动化应继续检查 `game_ready` 和服务状态。

网络预检检查所选安装来源需要的网络入口。HTTP 代理可用于内容下载，但不等于 SteamCMD 全协议代理；网络预检通过也不保证后续 Steam 认证和完整游戏下载成功。网络配置、缓存清理及受控修复见 [诊断、网络与清理](docs/operations.md#诊断网络与清理)。

## 使用范围

- 房间名称使用 ASCII。CLI 不支持非空入场密码 `sv_password`；RCON 密码与 Steam 账号认证正常支持。
- 已验证双实例、真实 Workshop 战役下载与切图、插件加载与编译、后台任务、离线 ZIP 和同机状态恢复。跨机器恢复、GameMaps/多 Part 在线下载及完整多人对局场景仍需进一步验证。
- 二维码登录需可选后端，目前属于实验能力，见 [二维码登录](docs/qr-login.md)；完整游戏包的严格断网全链路仍待验证。
- Web 管理面板、统一 Agent Skill 安装和游戏自动更新暂未提供；后续 Web 面板按可选组件设计，CLI 独立提供底层运维能力。

## 文档

| 文档 | 内容 |
| --- | --- |
| [CLI 运维指南](docs/operations.md) | 多房间、地图、Mod、插件、管理员、RCON、任务、传输、备份和修复 |
| [配置与权限](docs/configuration.md) | 默认房间配置、公开/Steam 组模式、秘密文件与权限 |
| [Steam 登录](docs/steam-auth.md) / [二维码登录](docs/qr-login.md) | 首次认证、私有缓存及可选登录后端 |
| [完整游戏包与离线导入](docs/bundle-format-v1.md) | 制作安装包、本地来源和离线安装 |
| [Linux 支持范围](docs/linux-support.md) | 运行环境、发行版依赖和验证边界 |
| [安装包说明](docs/executable-package.md) / [构建说明](scripts/packaging/README.md) | 可执行目录包、安装器及 Linux 构建流程 |
| [贡献指南](CONTRIBUTING.md) / [安全报告](SECURITY.md) / [更新记录](CHANGELOG.md) | 开发验证、问题反馈和变更记录 |

源码采用 [MIT 许可证](LICENSE)。游戏文件、SteamCMD、登录缓存及服务器秘密不进入仓库或程序发布包；反馈问题时请提供环境、命令和脱敏后的错误信息。

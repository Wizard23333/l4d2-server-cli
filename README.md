# L4D2 Server CLI

当前预览版：**v0.1.0-alpha.3**（Python 版本 `0.1.0a3`）。[下载安装包](https://github.com/Wizard23333/l4d2-server-cli/releases/tag/v0.1.0-alpha.3) · [新手开服教程](docs/getting-started.md) · [本版变化与限制](docs/releases/v0.1.0-alpha.3.md)

在 Linux 上安装和管理 Left 4 Dead 2 专用服务器的命令行工具。从首次部署到多房间、地图、插件和备份，使用同一套 CLI 完成日常运维。

所有运维能力均可独立运行，无需部署 Web 面板；支持操作预览、后台任务和 JSON 输出，便于手动使用或接入自动化工具。

[功能概览](#功能概览) · [安装](#安装) · [快速上手](#快速上手) · [日常运维](#日常运维) · [文档](#文档)

## 功能概览

| 能力 | 功能 | 命令 |
| --- | --- | --- |
| 首次开服引导 | 中文选择房间设置和安装来源，预检、安装、配置、启动及加入说明 | `setup` |
| 好友加入与诊断 | 复制连接说明、检查运行端口、让好友从自己的电脑查询游戏 | `room invite/connect-check`、`network probe` |
| 地图分享清单 | Workshop 合集、分包与声明依赖递归展开，标明未验证项 | `map share` |
| 好友名单房间 | 按 SteamID64 配置 SourceMod 名单，核对策略与插件运行回执 | `access` |
| 日常维护 | 停服更新官方游戏、定时私有备份、定时只读健康检查 | `room update/health`、`schedule` |
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
| 可选 Web 面板 | 优化版侧栏和双主题界面，复用 CLI 运维能力，支持安装、配置、服务维护和只读自检 | `web` |
| 可选 Agent Skill | 单文件运维 Skill，提供常用 Agent 的本地安装、状态检查、更新、导出与卸载 | `skill` |

每个命令均可通过 `--help` 查看参数，例如 `l4d2 room create --help`。完整操作指南见 [CLI 运维说明](docs/operations.md)。

## 安装

### 环境要求

- Linux x86_64、glibc，以及运行中的 systemd。可执行包要求 glibc 2.31+；源码或 wheel 需要 Python 3.8+。
- 安装游戏需准备 SteamCMD/游戏的 32 位运行库；首次安装按至少 20 GiB 空闲空间预检，第三方地图和备份需额外空间。
- 安装、配置及服务管理使用 `sudo`；游戏以普通系统用户运行。Steam 账号登录使用普通用户执行。

Ubuntu 20.04 已完成核心流程实测；其他发行版适配、依赖及验证范围见 [Linux 支持说明](docs/linux-support.md)。

### 可执行包

从上方预览版页面下载同版本的 `.tar.gz` 和 `.sha256`，校验外层包后解压：

```bash
curl -fLO https://github.com/Wizard23333/l4d2-server-cli/releases/download/v0.1.0-alpha.3/l4d2-server-cli-v0.1.0-alpha.3-linux-x86_64.tar.gz
curl -fLO https://github.com/Wizard23333/l4d2-server-cli/releases/download/v0.1.0-alpha.3/l4d2-server-cli-v0.1.0-alpha.3-linux-x86_64.sha256
sha256sum -c l4d2-server-cli-v0.1.0-alpha.3-linux-x86_64.sha256
tar -xzf l4d2-server-cli-v0.1.0-alpha.3-linux-x86_64.tar.gz
cd l4d2-server-cli-v0.1.0-alpha.3-linux-x86_64
```

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

推荐在目标 Linux 主机运行中文引导：

```bash
sudo l4d2 setup
```

按提示选择房间名、端口、官方战役、难度和安装来源，审阅计划后执行。引导复用已有安装记录，失败后保留已完成部分；不会重配或重启运行中的 main 房间。可以用 `sudo l4d2 setup --dry-run` 只预览，或用 `--no-start` 在安装配置后保持停止。游戏本地就绪后会显示加入方式，公网连接仍需由朋友实测。完整步骤和认证/失败恢复见 [新手教程](docs/getting-started.md)。

也可分别执行以下命令：

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

`doctor`、`network check`、房间状态/清单、地图/插件清单及新维护命令默认显示中文结果；支持结构化输出的命令可加 `--json`，进度写入 stderr。状态命令退出成功表示取得报告，自动化应继续检查 `game_ready` 和服务状态。`room health`、`room connect-check` 和 `network probe` 在检查异常时返回非零退出码。

和朋友开黑的常用入口（地址替换为实际公网地址）：

```bash
sudo l4d2 room invite --room main --address YOUR_SERVER_IP
sudo l4d2 room connect-check --room main --address YOUR_SERVER_IP
sudo l4d2 map share --room main
sudo l4d2 room health --room main
```

复制邀请、核对地图依赖、好友名单、游戏更新及定时维护，见 [好友开黑与日常维护](docs/players-and-maintenance.md)。

网络预检会展示检查目标、失败原因和建议，SteamCMD 工具下载支持可信 Steam CDN 回退。HTTP 代理可用于内容下载，但不等于 SteamCMD 全协议代理；网络预检通过也不保证后续 Steam 认证和完整游戏下载成功。网络配置、缓存清理及受控修复见 [诊断、网络与清理](docs/operations.md#诊断网络与清理)。

## 使用范围

- 房间名称使用 ASCII。CLI 不支持非空入场密码 `sv_password`；RCON 密码与 Steam 账号认证正常支持。
- 已验证双实例、真实 Workshop 战役下载与切图、插件加载与编译、后台任务、离线 ZIP 和同机状态恢复。跨机器恢复、GameMaps/多 Part 在线下载及完整多人对局场景仍需进一步验证。
- 二维码登录需可选后端，目前属于实验能力，见 [二维码登录](docs/qr-login.md)；完整游戏包的严格断网全链路仍待验证。
- 好友名单由 SourceMod 插件执行，支持文件/插件回执检查；真实允许、拒绝、重连和换图验收仍待测试主机及玩家参与。框架故障时不能保证访问限制，不把 Steam 组或 RCON 密码当作白名单。
- `room update` 提供显式停服更新；定时任务支持备份和健康检查。更新前备份不含旧游戏二进制；不提供有人游玩时自动停服更新。
- Web 管理面板为可选组件，使用 `web install` 安装、`web check` 自检，见 [面板说明](docs/web-panel.md)。新增操作也可从面板通用命令表使用。

## 可选 Agent Skill

在 Agent 所在机器安装一个统一的 `l4d2-manager` Skill，支持 Codex、Claude Code、Cursor、Windsurf、Cline、Copilot、Continue 及通用目录：

```bash
l4d2 skill list --json
l4d2 skill install --agent codex --dry-run
l4d2 skill install --agent codex --yes
l4d2 skill install --agent claude-code --project /path/to/project --yes
```

`skill` 命令可通过 Python 源码/wheel 在 Windows、Linux 和 macOS 本地执行，不需要安装游戏、sudo 或 Web。提供查看、导出、状态、更新和卸载；默认保护已有不同内容。Continue 使用项目规则，其他适配器支持用户和项目范围。详见 [Agent Skill 安装说明](docs/agent-skill.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [新手开服教程](docs/getting-started.md) | 下载 CLI、首次引导、好友加入、失败恢复及上一版升级 |
| [CLI 运维指南](docs/operations.md) | 多房间、地图、Mod、插件、管理员、RCON、任务、传输、备份和修复 |
| [可选 Web 面板](docs/web-panel.md) | 安装、配置、自检、页面能力、服务维护及验收 |
| [可选 Agent Skill](docs/agent-skill.md) | 单文件 Skill、工具适配、用户/项目安装、更新、冲突保护和卸载 |
| [配置与权限](docs/configuration.md) | 默认房间配置、公开/Steam 组模式、秘密文件与权限 |
| [Steam 登录](docs/steam-auth.md) / [二维码登录](docs/qr-login.md) | 首次认证、私有缓存及可选登录后端 |
| [完整游戏包与离线导入](docs/bundle-format-v1.md) | 制作安装包、本地来源和离线安装 |
| [Linux 支持范围](docs/linux-support.md) | 运行环境、发行版依赖和验证边界 |
| [安装包说明](docs/executable-package.md) / [构建说明](scripts/packaging/README.md) | 可执行目录包、安装器及 Linux 构建流程 |
| [贡献指南](CONTRIBUTING.md) / [安全报告](SECURITY.md) / [更新记录](CHANGELOG.md) | 开发验证、问题反馈和变更记录 |

源码采用 [MIT 许可证](LICENSE)。游戏文件、SteamCMD、登录缓存及服务器秘密不进入仓库或程序发布包；反馈问题时请提供环境、命令和脱敏后的错误信息。

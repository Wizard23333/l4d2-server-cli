# L4D2 Server CLI

用于安装、配置和管理原版 Left 4 Dead 2 专用服务器的命令行工具。首版管理一个实例 `main`，使用官方地图。

版本：`0.1.0a1`（`v0.1.0-alpha.1`），首个alpha预览版。支持官方服务端安装、基础配置、公开/Steam组房间切换、启停和诊断。

## 环境要求

- Linux x86_64、glibc、运行中的systemd。可执行包要求glibc2.31+并内置Python；源码/wheel方式需要Python3.8+。
- 已实现 Ubuntu/Debian、Fedora/RHEL/Rocky/AlmaLinux、openSUSE 和 Arch 家族适配；Ubuntu 20.04 已实机验证，其他发行版完整流程待测。
- 首次游戏安装采用 20 GiB 空闲空间预算；需要 SteamCMD/游戏的 32 位运行库。
- 安装、配置和启停使用 sudo；游戏以独立普通用户运行。

CLI 在目标 Linux 主机执行。Windows 可编辑源码和查看帮助；详细前提见 [Linux 支持说明](docs/linux-support.md)。

## 安装 CLI

### Linux x86_64 可执行包（推荐）

不需要系统Python、venv或pip。从 [v0.1.0-alpha.1 预览版 Release](https://github.com/Wizard23333/l4d2-server-cli/releases/tag/v0.1.0-alpha.1) 下载两个同版本附件，放在同一目录：

- [Linux x86_64可执行包（tar.gz）](https://github.com/Wizard23333/l4d2-server-cli/releases/download/v0.1.0-alpha.1/l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.tar.gz)
- [SHA256校验文件](https://github.com/Wizard23333/l4d2-server-cli/releases/download/v0.1.0-alpha.1/l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.sha256)

也可以直接下载并验证：

```bash
curl -fLO https://github.com/Wizard23333/l4d2-server-cli/releases/download/v0.1.0-alpha.1/l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.tar.gz
curl -fLO https://github.com/Wizard23333/l4d2-server-cli/releases/download/v0.1.0-alpha.1/l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.sha256
sha256sum -c l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.sha256
tar -xzf l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.tar.gz
cd l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64
# alpha.1 的旧附件不含安装器，先获取仓库提供的独立安装脚本
curl -fLo install.sh https://raw.githubusercontent.com/Wizard23333/l4d2-server-cli/main/scripts/packaging/install.sh
sudo bash ./install.sh
l4d2 --version
l4d2 --help
```

安装器默认把完整CLI复制到 `/usr/local/lib/l4d2-server-cli/`，并注册 `/usr/local/bin/l4d2`。安装后在任意目录都能执行 `l4d2` 和 `sudo l4d2`，原解压目录可移除。后续发布包会直接包含 `install.sh`，无需单独获取。已有其他程序的同名命令会报冲突，不自动覆盖。

也可保留整个解压目录（包括 `_internal/`），用 `./l4d2` 便携运行；不要只复制可执行文件。内置运行时只解决CLI自身依赖；游戏仍需systemd、32位运行库及可用下载来源。详细说明见 [可执行包](docs/executable-package.md)。

### Python wheel 或源码

已有Python3.8+、venv/pip时，可以离线安装本地wheel：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --no-deps /path/to/l4d2_server_cli-0.1.0a1-py3-none-any.whl
.venv/bin/l4d2 --version
```

也可在项目源码目录执行 `python3 bin/l4d2 --help`，无需下载Python运行依赖。安装源码到venv使用 `.venv/bin/python -m pip install .`，构建依赖可能需要联网。

以下示例使用全局安装后的 `l4d2`；便携运行使用解压目录中的 `./l4d2`，wheel安装使用 `.venv/bin/l4d2`，源码方式使用 `python3 bin/l4d2`。

## 已实现的命令

表中的 `l4d2` 指上述 CLI 入口；任意子命令加 `--help` 可查看完整参数。

| 命令 | 功能 |
| --- | --- |
| `l4d2 --help / --version` | 查看帮助或版本 |
| `l4d2 doctor` | 检查主机条件、网络、安装及本地服务健康情况 |
| `l4d2 network check` | 按安装来源执行独立网络预检 |
| `l4d2 download steamcmd --output FILE` | 下载官方 Linux SteamCMD 工具包 |
| `l4d2 download server --output FILE` | 下载全新官方 Linux 游戏内容并制作可导入的完整包；普通用户执行 |
| `l4d2 auth login` | 密码/扫码登录并验证私有缓存；扫码需可选后端；普通用户执行 |
| `l4d2 server install` | 安装官方服务器，登记目录并生成 systemd 服务，安装后保持停止 |
| `l4d2 server configure` | 配置 ASCII 名称、IPv4、端口、默认官方地图、coop/versus、合作难度、公开/Steam 组模式、RCON 密码及开机启动 |
| `l4d2 server start` | 启动服务并检查本地就绪情况 |
| `l4d2 server stop` | 停止服务 |
| `l4d2 server restart` | 重启服务并检查本地就绪情况 |
| `l4d2 server status` | 查看安装、服务和本地查询状态 |
| `l4d2 server logs` | 查看脱敏日志，支持 `--lines`、`--since`、`--follow`；需 sudo |

原版限制：房间名称使用 ASCII。非空入场密码 `sv_password` 会妨碍 Steam 查询/连接，首版拒绝设置；旧配置可用 `server configure --clear-join-password` 清除，再重启。RCON 密码及 Steam 账号密码登录保持支持。

安装和配置支持交互确认；不带设置参数的 `configure` 会打开配置向导。配置保存不会自动重启，默认不开机启动。

`install/configure/start/stop/restart` 支持 `--dry-run` 查看计划；需要确认的操作可用 `--yes`。除交互登录和日志外，命令支持 `--json`；网络、下载、登录、安装和启停支持 `--timeout`。具体以各命令帮助为准。

## 安装游戏并启动

```bash
l4d2 network check
l4d2 server install --dir /opt/l4d2 --dry-run
sudo l4d2 server install --dir /opt/l4d2
sudo l4d2 server configure --name "My L4D2 Server" --port 27015 \
  --map c1m1_hotel --mode coop --difficulty normal --visibility public
sudo l4d2 server start
sudo l4d2 server status --json
```

默认安装尝试匿名下载；若 Steam 拒绝匿名授权，先按下方“首次账号登录”步骤认证，再用同一账号与缓存安装。

CLI 不自动修改防火墙或云安全组。放行游戏 UDP 端口后，在外部原版客户端执行 `connect YOUR_SERVER_IP:27015`；NAT 环境还需端口转发。本地就绪不代表外网玩家已经能加入。

## 公开房间与 Steam 组房间

同一个 `main` 房间可切换两种模式，保存后单独重启生效：

```bash
# 公开房间：清除组 ID，允许直连
sudo l4d2 server configure --visibility public
sudo l4d2 server restart

# Steam 组房间：填写组管理页上的短数字 ID
sudo l4d2 server configure --visibility group --steam-group-id YOUR_SHORT_GROUP_ID
sudo l4d2 server restart
```

只传 `--steam-group-id` 也会选择组模式。组模式使用原版组优先规则，并要求通过大厅加入；使用组成员账号在 L4D2 的 Steam 组服务器列表/大厅中验证。空房优先由组员进入，已有玩家时非组员可能加入，因此不是严格白名单。公开模式可用 `connect YOUR_SERVER_IP:27015`。

配置向导同样可选 `public/group`。短 ID、长 `groupID64`、组页面 URL 不通用；CLI 会拒绝后两者。旧 CLI 配置缺少新增字段时默认仍为公开。详情与参数对应表见 [配置说明](docs/configuration.md)。

## 网络失败与备用安装

支持在线下载、本地 SteamCMD 包、已有完整 Linux SteamCMD 目录和完整 Linux 游戏包导入；安装前会自动预检必要网络。HTTP 代理预检通过不代表 Steam 内容连接可用。

```bash
# 用本地 SteamCMD 包替代工具下载
sudo l4d2 server install --steamcmd-archive ./steamcmd_linux.tar.gz

# 或克隆已有完整工具目录及其私有登录缓存
sudo l4d2 server install --steamcmd-path /path/to/steamcmd.sh

# 在连通的 Linux 主机以普通用户制作完整游戏包
l4d2 download server --output ./server-linux.tar.gz

# 传到目标主机后离线导入；系统依赖需提前备齐
sudo l4d2 server install --server-archive ./server-linux.tar.gz --offline
```

获取工具包时可以对单次命令指定已有HTTP代理；这不会为SteamCMD的全部协议配置代理：

```bash
HTTPS_PROXY=http://127.0.0.1:YOUR_PROXY_PORT l4d2 download steamcmd --output ./steamcmd_linux.tar.gz
```

SteamCMD 包只替代工具获取，工具更新和游戏下载仍可能联网。完整游戏包须由 `download server` 制作；可用 `--sha256 HASH` 校验本地包。`--offline` 约束安装过程，不保证游戏运行能够断网。

### 首次账号登录

服务端安装文件由Steam提供。CLI默认以匿名身份（`anonymous`）下载；匿名下载可用时，无需先登录账号。如果Steam明确拒绝匿名下载授权（例如返回 `No subscription`），可以改用Steam账号请求下载，实际能否下载取决于Steam授予该账号的内容访问权限。

`auth login`用于完成Steam身份验证，并生成后续 `server install` / `download server` 可复用的本地私有登录缓存。登录成功只证明账号认证和缓存复用通过，是否有下载权限还要由实际下载结果确认。账号登录不影响房间的公开/Steam组设置；已安装房间的日常启停也无需重新执行此登录步骤。

连接超时、DNS或代理故障仍需处理网络问题，账号登录不会自动解决。如果使用已准备好的完整游戏包离线导入，目标服务器无需执行账号登录。

需要账号下载且首次没有登录缓存时，以普通用户完成密码/Steam Guard认证，再使用新生成的缓存安装；不要对 `auth login` 使用sudo：

```bash
AUTH_CACHE="$HOME/.local/share/l4d2-cli/first-login"
l4d2 auth login --fresh --cache "$AUTH_CACHE"
# 按提示输入账号名、密码并完成 Steam Guard；登录核验成功后再安装
sudo l4d2 server install --steam-user YOUR_STEAM_LOGIN_NAME \
  --steamcmd-path "$AUTH_CACHE/steamcmd/steamcmd.sh"
```

`--fresh` 要求新建或空目录，并排除已有工具的登录配置；遇到非空缓存会拒绝覆盖。密码/Steam Guard 交给原生提示，CLI 随后验证免密码复用。安装时填写刚登录的同一账号名。详见 [首次登录说明](docs/steam-auth.md)。

### 可选：二维码登录

密码方式保持默认，也可显式指定 `--method password`。二维码方式需要可选的 [DepotDownloader 后端](docs/qr-login.md)：

```bash
QR_CACHE="$HOME/.local/share/l4d2-cli/qr-first-login"
l4d2 auth login --method qr --fresh --cache "$QR_CACHE" \
  --depotdownloader-path /path/to/DepotDownloader
l4d2 download server --qr-cache "$QR_CACHE" --output ./qr-server-linux.tar.gz
sudo l4d2 server install --server-archive ./qr-server-linux.tar.gz --offline
```

按提示输入登录账号名，再使用 Steam 手机 App 扫码确认。扫码缓存由该后端使用，生成完整游戏包后导入；不能把扫码缓存传给 SteamCMD。二维码方式为实验能力，批准登录、缓存复用及完整下载链路尚未完成实机验证。详见 [扫码说明](docs/qr-login.md)。

失败安装保留半成品供相同登记目标重试；不自动接管非空目录或同名服务。配置支持私有密码文件、变更检查和事务恢复，详见 [配置说明](docs/configuration.md)。

## 支持范围与已知限制

- 已有Ubuntu20.04实机使用记录；其他发行版具有依赖适配，完整流程仍需分别验证，见 [Linux支持范围](docs/linux-support.md)。
- 当前只管理一个 `main` 实例和官方地图；不包含三方图、Workshop、MOD/插件、在线切图、多实例、Web面板或自动更新。
- Steam账号密码/Guard认证与非匿名下载安装可用；二维码后端及完整游戏包严格断网全链路仍属实验能力。
- 房间名仅ASCII，不支持非空入场密码；Steam组模式使用组优先规则，不是严格成员白名单。
- 本机就绪检查不代表公网玩家已经可以加入；需自行配置防火墙/安全组并从客户端验证。
- versus、长时间/多玩家及不同组成员状态的完整行为尚未全部验证。

## 文档与反馈

- [配置与权限](docs/configuration.md)
- [Steam密码登录](docs/steam-auth.md) / [可选二维码登录](docs/qr-login.md)
- [完整游戏包与离线导入](docs/bundle-format-v1.md)
- [贡献指南](CONTRIBUTING.md) / [安全报告](SECURITY.md)

源码采用 [MIT许可证](LICENSE)。游戏文件、SteamCMD包、登录缓存、服务器秘密及本地开发/测试记录不进入公开仓库或程序发布包。

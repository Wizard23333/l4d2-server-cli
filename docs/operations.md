# CLI 运维命令

本文描述 alpha.3 的运维能力，包含在同版本 CLI 包中；旧版本先检查实际帮助和 capabilities。好友加入、地图分享、好友名单、游戏更新和定时维护见 [好友开黑与日常维护](players-and-maintenance.md)。原有 `server`、`auth`、`download`、`network check` 继续可用。

全部运维能力独立于 Web 面板。源码不启动 HTTP 服务，也不需要旧面板的 helper、环境文件或进程。只有显式导入旧来源记录、或选择备份/还原面板状态时才读取旧面板数据。

## 执行约定

- 服务器运维在 Linux x86_64/glibc/systemd 主机执行；Windows 可查看帮助与能力清单。
- 修改操作使用 sudo，支持 `--dry-run` 和 `--yes`。JSON 模式不会打开确认提示，需要确认时返回退出码 5。输出沿用统一 JSON 信封和错误码，进度写 stderr。
- 修改游戏文件使用跨进程操作锁与文件回滚记录。若进程被强制终止，可先执行 `repair transaction --dry-run`，再确认恢复。
- 安装地图、Mod、插件、保存配置和恢复备份均不自动重启房间。通过 `room restart --room ID` 明确选择生效房间。
- 地图、Mod、SourceMod 等资源按游戏安装目录共享。变更计划显示受影响房间；每个房间的端口、配置、服务独立。
- RCON、代理、Steam API key 使用私有文件输入；不接收普通命令行密码参数。真实秘密不应出现在终端、提交或日志中。
- `room status` 返回成功表示取得报告，需读取 `game_ready` 判断本机游戏就绪；公网可达与真实玩家加入仍需外部验证。

以下示例使用源码入口。安装新版 CLI 后可将 `python3 bin/l4d2` 替换为 `l4d2`。

## 命令清单

| 命令组 | 操作 |
| --- | --- |
| `capabilities` | 返回能力版本、命令组、执行约定；不要求已安装游戏 |
| `room` | `list / scan / adopt / create / configure / status / logs / doctor / start / stop / restart / refresh / remove` |
| `map` | `list / info / search / resolve / install / import / verify / extract / default / change / remove / reinstall` |
| `addon` | `list / info / search / resolve / install / import / enable / disable / remove / reinstall` |
| `plugin` | `list / sources / stack / install / compile / enable / disable` |
| `admin` | `list / add / remove`，支持 SourceMod、Left4Bots、Admin System |
| `rcon` | `exec`，从私有命令文件读取一行 Source 控制台指令 |
| `task` | `submit / list / show / logs / cancel / retry / delete` |
| `transfer` | `export / import / install`，支持来源 Manifest 与离线 ZIP；导入旧 `packages.json` |
| `backup` | `create / inspect / restore`，完整状态或仅插件栈 |
| `storage` | `usage / cleanup`，可清理缓存、Workshop 缓存、已结束进程遗留的临时目录 |
| `network` | 原有 `check`，新增 `status / configure` |
| `repair` | `mission-bom / permissions / transaction` |
| `skill` | 可选本地 Agent 接入：`show / export / list / status / install / update / uninstall`，不要求服务器环境 |

任意子命令的 `--help` 列出参数。原有安装、Steam 认证与网络预检详见 README 和对应文档。

## 房间登记与旧服接管

`main` 自动来自原有 CLI 的安装状态；旧服务器不会被 `server install` 自动覆盖。

```bash
sudo python3 bin/l4d2 room scan --dir /opt/l4d2 --json
sudo python3 bin/l4d2 room list --json

sudo python3 bin/l4d2 room adopt --id room1 --dir /opt/l4d2 \
  --service l4d2 --script /opt/l4d2/start_l4d2.sh \
  --cfg /opt/l4d2/left4dead2/cfg/server.cfg --user steam --dry-run

# 核对后使用同一条命令，把 --dry-run 替换为 --yes
```

接管要求服务来自本机 systemd 目录、以指定非 root 用户运行、无 drop-in，并能关联到安装目录中的启动脚本和配置。地图、端口需为可解析的字面启动参数；动态变量脚本需先人工整理。运行中的服务必须能验证端口归属。接管只登记状态，不重启、不重新下载游戏。

```bash
sudo python3 bin/l4d2 room create --id room2 --from-room main --port 27016 --name "Room 2" --dry-run
sudo python3 bin/l4d2 room status --room room1 --json
sudo python3 bin/l4d2 room configure --room room1 --visibility public --steam-group-id YOUR_SHORT_GROUP_ID --dry-run
sudo python3 bin/l4d2 room restart --room room1 --dry-run
sudo python3 bin/l4d2 room logs --room room1 --lines 100
```

新房间复用来源房间的游戏文件和运行用户，创建独立 cfg/unit，并保持停止且不开机启动。配置保存不重启；运行中改端口后继续查询已记录的实际运行端口。公开模式可以关联短 Steam 组 ID，组模式使用原版组优先规则，不代表严格成员白名单。

手动编辑已接管/新建房间文件后，CLI 会报告冲突。核对变更后可执行 `room refresh --room ID --dry-run`，再用 `--yes` 接受新的基线。原有 `main` 继续使用其私有配置摘要和事务规则，不由 refresh 接管。

`room remove` 要求房间停止：接管房间只注销登记，新建房间移除其 cfg/unit；不删除共享游戏文件。`main` 登记受保护。

## 地图、Mod 与来源记录

```bash
sudo python3 bin/l4d2 map list --room main --json
sudo python3 bin/l4d2 map search "Run to the Hills" --json
sudo python3 bin/l4d2 map resolve 2232584588 --json
sudo python3 bin/l4d2 map install 2232584588 --room main --dry-run
sudo python3 bin/l4d2 map install 2232584588 --room main --yes --timeout 1800

sudo python3 bin/l4d2 map import --room main --file /path/to/campaign.vpk --dry-run
sudo python3 bin/l4d2 addon import --room main --file /path/to/mod.vpk --dry-run
sudo python3 bin/l4d2 addon disable mod.vpk --room main --dry-run
sudo python3 bin/l4d2 map verify --file /path/to/campaign.vpk --json
```

Workshop 支持合集、已知多 Part 清单、API children 和描述中的兄弟包解析。可选 Steam Web API key 可增强依赖发现。多 Part 安装失败会保留已成功安装的包；重试会校验并跳过相同内容，继续缺失部分。来源版本变化或本地资源被改动时会拒绝覆盖，需显式检查和移除后重新安装。

GameMaps 使用 `--source gamemaps` 加数字 details ID。站点保护或没有可用文件时返回失败，可改用已下载文件导入。CLI 不接受任意下载 URL。

地图 VPK 校验 CRC、长度和安全相对路径；提取仅允许游戏资源目录。VPK v1/v2 及带齐 `_dir.vpk`、`_000.vpk` 等文件的分卷索引可读取与离线导入。分卷索引保留原文件名。Workshop 多 Part 指多个独立包，与 VPK 分卷不同。

地图安装记录提取文件的 SHA256 和归属。遇到不同内容的 loose 文件会拒绝覆盖；已存在且未登记的相同 loose 文件不认领，卸载时保留。普通 Mod 不展开到游戏目录；含 BSP 的包不能作为普通 Mod 导入。

从 `map list` 选择实际已安装的地图名，再设置默认地图或在线切换：

```bash
sudo python3 bin/l4d2 map default YOUR_INSTALLED_MAP --room main --dry-run
sudo python3 bin/l4d2 map change YOUR_INSTALLED_MAP --room main --dry-run
sudo python3 bin/l4d2 map remove campaign.vpk --room main --dry-run
sudo python3 bin/l4d2 map reinstall campaign.vpk --room main --dry-run
```

在线切图需要该房间配置 RCON 密码，并查询验证目标地图。删除会保护所有共享房间的默认地图与运行地图；运行地图无法确认时拒绝删除。普通删除保留来源，`--purge` 同时删除来源记录。只移除登记归属且摘要未变化的文件，保留其他包共享的文件。地图包不能通过普通 Mod 启停命令切换。

旧面板来源记录可直接导入，无需运行面板：

```bash
sudo python3 bin/l4d2 transfer import --room room1 --format legacy \
  --file /var/lib/l4d2-manager-web/packages.json --dry-run
```

旧记录导入只认领能与 VPK 验证一致的自定义 maps/missions；不会猜测其他未登记 loose 资源的归属。没有来源 ID 的包不能自动重新下载。

## SourceMod、编译与管理员

```bash
sudo python3 bin/l4d2 plugin list --room main --json
sudo python3 bin/l4d2 plugin stack --room main --component core --dry-run
sudo python3 bin/l4d2 plugin stack --room main --component left4dhooks --dry-run
sudo python3 bin/l4d2 plugin sources --json
sudo python3 bin/l4d2 plugin compile --room main --builtin infected_counter --deploy --dry-run
sudo python3 bin/l4d2 plugin compile --room main --file /path/to/custom.sp --deploy --dry-run
sudo python3 bin/l4d2 plugin disable custom.smx --room main --dry-run
```

框架也可通过 `--metamod-archive`、`--sourcemod-archive` 使用本地官方压缩包。显式安装前生成私有插件快照，选择 32-bit MetaMod loader，补齐 `cfg/sourcemod`，保留现有配置和启停态。不自动安装第三方广告、误标击杀插件或启用不兼容的投票链。Left4DHooks 是受保护依赖，不能直接禁用。

编译优先 `spcomp64`，回退到可用的 `spcomp`；编译进程以游戏用户执行。部署会备份旧插件并保存 `.sp` 源码，之后需显式重启受影响房间。未加 `--deploy` 时仅保存编译产物。CLI 随包提供 `infected_counter` 与 `jiangmen_changelevel_fix` 两份项目源码。

```bash
sudo python3 bin/l4d2 admin list --room main --backend sourcemod --json
sudo python3 bin/l4d2 admin add STEAM_1:0:123456 --flags 99:z --room main --dry-run
sudo python3 bin/l4d2 admin remove STEAM_1:0:123456 --room main --dry-run
```

EMS 后端选择 `left4bots` 或 `admin-system`。管理员修改后通过插件自己的 reload 或房间重启生效；SourceMod 的详情或自定义控制台操作可使用 RCON。

## RCON 与后台任务

RCON 密码沿用 `server configure --rcon-password-file`，其他登记房间使用 `room configure --rcon-password-file`。密码文件须为 0600。控制台指令同样放入私有文件：

```bash
sudo python3 bin/l4d2 rcon exec --room main --command-file /path/to/private-command.txt --dry-run
sudo python3 bin/l4d2 task submit --yes -- map install 2232584588 --room main --timeout 1800
sudo python3 bin/l4d2 task list --json
sudo python3 bin/l4d2 task show YOUR_TASK_ID --json
sudo python3 bin/l4d2 task logs YOUR_TASK_ID --lines 100 --json
sudo python3 bin/l4d2 task cancel YOUR_TASK_ID --dry-run
sudo python3 bin/l4d2 task retry YOUR_TASK_ID --dry-run
sudo python3 bin/l4d2 task delete YOUR_TASK_ID --dry-run
```

任务执行器使用独立进程运行同一套 CLI，不依赖浏览器或 Web 服务。只接受非交互运维命令，不接受 shell 或嵌套任务。交互式 Steam 登录应先在终端完成。任务状态和日志持久化，`task show` 返回阶段、分包数量以及当前下载字节和百分比；下载百分比只表示当前文件。记录进程启动身份，旧 PID 被复用时不会发送取消信号。异常退出的运行任务显示 interrupted，确认后可重试。并发冲突返回 operation_busy，不会同时改写资源；可在已有操作结束后重试。

## 资源传输和完整备份恢复

```bash
sudo python3 bin/l4d2 transfer export --room main --format manifest --output /private/content.json
sudo python3 bin/l4d2 transfer import --room main --format manifest --file /private/content.json --dry-run
sudo python3 bin/l4d2 transfer install --room main --dry-run
sudo python3 bin/l4d2 transfer export --room main --format zip --output /private/content.zip
sudo python3 bin/l4d2 transfer import --room main --format zip --file /private/content.zip --dry-run

sudo python3 bin/l4d2 backup create --room main --output /private/server-state.zip --dry-run
sudo python3 bin/l4d2 backup create --room main --scope plugins --dry-run
sudo python3 bin/l4d2 backup inspect --file /private/server-state.zip --json
sudo python3 bin/l4d2 backup restore --room main --file /private/server-state.zip --dry-run
```

Manifest 只保存来源与状态，导入不下载；`transfer install` 显式重新安装导入的缺失记录。ZIP 保存 VPK、摘要与启停态，导入验证成员路径、容量、重复目标和摘要。

完整状态备份包含共享房间配置、启动脚本、服务原文件、SourceMod/MetaMod 栈（含 disabled、扩展、gamedata、配置和源码）、普通 Mod 与 EMS 数据；地图只保存来源记录。备份文件是 0600，可能含 RCON、管理员及面板登录信息。

换机前先安装游戏本体，并登记目标房间。恢复要求全部共享房间停止：将备份主房间映射到 `--room`，其他房间按 ID 恢复，游戏路径、运行用户和监听地址跟随目标安装，端口保留备份值并检查冲突。服务从经过校验的房间参数重新生成，旧 unit 原文留在备份中供核对；动态脚本的额外自定义服务参数需要人工核对。原版 main 通过已有私有配置事务恢复。

恢复完成后不启动房间。先重新安装地图，再根据返回的 `saved_defaults` 使用 `room configure`/`map default` 恢复默认地图和开机启动选择，最后显式启动并检查。目标 main 缺失自定义地图时临时保留其已安装默认图。插件快照恢复会移除快照中没有的新插件，精确还原启用与禁用态。

`--network` 显式还原 CLI 的私有代理/API 配置、Steam CDN 标记块和 IPv4 优先行；不会覆盖整份 hosts/gai.conf。`--panel-state` 显式还原可选面板 env/来源记录，不安装、不启动面板。可用 `--sha256` 核对整份备份。

## 诊断、网络与清理

```bash
sudo python3 bin/l4d2 room doctor --room main --json
sudo python3 bin/l4d2 storage usage --room main --json
sudo python3 bin/l4d2 storage cleanup --room main --scope temporary --dry-run
sudo python3 bin/l4d2 repair mission-bom campaign.txt --room main --dry-run
sudo python3 bin/l4d2 repair permissions --room main --scope maps --dry-run
sudo python3 bin/l4d2 repair transaction --dry-run
sudo python3 bin/l4d2 network status --json
sudo python3 bin/l4d2 network configure --proxy-file /private/proxy.txt --dry-run
sudo python3 bin/l4d2 network configure --steam-api-key-file /private/steam-api.txt --dry-run
sudo python3 bin/l4d2 network configure --hosts-file /path/to/steam-hosts.txt --prefer-ipv4 --dry-run
```

诊断组合服务、端口、游戏查询、磁盘、插件清单、SourceMod 最近错误 session、mission BOM/括号及 VPK 解析结果。mission 修复仅移除 BOM，不猜测补括号。权限修复只处理指定资源目录，不跟随符号链接。CDN 输入使用 `IP 域名`，只允许 Steam 相关域名；不内置过期 IP。

代理配置用于内容 HTTP 请求，不等于 SteamCMD 全协议路由。`--clear-proxy` 清空代理，`--reset-ipv4` 移除本工具管理的优先行。

清理范围限定为 CLI 内容缓存、CLI SteamCMD 的 Workshop 缓存或已结束进程的临时目录，保留仍在使用的临时目录。缓存清理不会为可重建数据再复制一份回滚副本，以便磁盘接近满时仍可释放空间。游戏地图文件通过有归属记录的 map remove 删除。

## 状态与验证范围

| 路径 | 内容 |
| --- | --- |
| `/etc/l4d2-cli/rooms.json` | 私有房间登记及文件摘要，0600 |
| `/etc/l4d2-cli/network.json` | 私有代理/API 配置，0600 |
| `/var/lib/l4d2-cli/operations/packages/` | 按游戏安装目录隔离的资源来源、提取归属和启停状态 |
| `/var/lib/l4d2-cli/operations/tasks/` | 私有任务状态与日志 |
| `/var/lib/l4d2-cli/operations/snapshots/` | 私有备份 |
| `/var/lib/l4d2-cli/operations/transaction.json` | 中断文件操作的私有回滚记录 |

文件与协议工作流、输入边界和回滚有自动回归覆盖。2026-10-17 在 Ubuntu 20.04 x86_64/glibc 2.31 的独立测试游戏目录完成房间接管、双实例生命周期、运行中配置及 RCON、真实 Workshop 战役下载/切图/卸载重装、官方插件栈下载加载、内置 SourcePawn 编译、后台任务、离线 ZIP 和同机完整备份恢复验收。测试通过后将当前开发源码构建的原生包安装为全局 CLI；既有主房间保持运行。

本轮不覆盖真实跨机器迁移、GameMaps/多 Part 在线下载、所有游戏模式及玩家加入。Workshop 直连未取得下载信息，经临时配置的本地代理完成下载；验收后恢复原网络设置。站点下载仍取决于上游网络和权限，服务器端查询通过不代表客户端对局通过。版本号仍为 `0.1.0a1`，上述开发构建不等同于已发布的同版本附件。

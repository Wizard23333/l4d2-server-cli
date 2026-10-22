---
name: l4d2-manager
description: 使用 l4d2 CLI 安装、接管和维护 Left 4 Dead 2 专用服务器，管理房间、地图、Mod、SourceMod 插件、管理员、日志、任务和备份；也用于配置可选 Web 面板或安装本 Skill。适用于 L4D2 服主运维任务。
metadata:
  skill-version: "1"
---

# L4D2 CLI 服务器管理

通过 `l4d2` CLI 完成操作。本文合并服务器搭建、地图、插件、排障、Web 面板和备份迁移指引，不依赖其他 Skill 或旧面板 helper。Web 面板和 Agent Skill 都是可选入口，服务器运维不依赖它们。

## 确认执行环境和真实能力

先运行 `l4d2 --version`、`l4d2 --help` 和 `l4d2 capabilities --json`；对选定操作查看 `l4d2 <命令组> <操作> --help`。以实际帮助为准，旧发布包可能没有开发源码中的命令。缺少能力时说明缺失项，使用用户指定的新版 CLI 或源码入口；不要编造参数或默认回退到旧 shell/helper 改写受管文件。

优先使用已安装的 `l4d2`。便携包可用其绝对路径；源码可用 `python3 /path/to/cli/bin/l4d2`（Windows 使用 `python`）。调用命令时将参数作为独立参数传递，路径和用户名不拼接成 shell 表达式。

`skill` 命令支持本地 Windows/Linux/macOS，无需游戏、sudo、SSH、systemd 或 Web。服务器运维在目标 Linux 主机执行：已有 SSH 授权时可通过用户确认的 SSH 别名调用目标机 CLI。本机安装 Skill 不会自动安装远程 CLI，也不授予服务器访问权限。

读取 `room list --json`，再查询目标 `room status --room ROOM_ID --json`，按需查询 `room doctor` 和 `storage usage`。首次安装可用 `doctor`、`network check`。主机、房间 ID、地图、端口和插件都来自本次查询；不要沿用历史服务器快照、个人 SSH 别名、Steam 组 ID、固定 CDN IP 或旧客户端路径。

## 执行约定

- 用户已明确授权目标和操作时继续执行；仅在目标、破坏范围或重启影响尚不明确时补充确认。变更先用同参数的 `--dry-run` 检查计划，再在授权范围内使用 `--yes`。`--yes` 是 CLI 确认参数，不会扩展用户授权。
- 服务器修改通常需要 sudo；`auth login` 和本地 `skill` 操作使用普通用户。按当前命令帮助判断支持哪些输出、超时和预览参数。
- JSON 命令检查退出码及信封中的 `ok`、`error`、`data`，不能把命令成功当成服务健康。`status` 成功只代表取得报告，应检查 `game_ready`、服务状态、端口归属和待生效配置。公网连接和实际玩家加入另行验证。
- 安装资源、保存配置、编译部署和恢复备份通常不自动重启；如需生效，单独选择房间重启。检查当前地图、在线人数及共享同一游戏目录的其他房间。在线切图会中断当前关卡。
- 不输出或写入 Skill/仓库的秘密：RCON/GSLT/Steam token、SSH 私钥、Steam API key、代理订阅与认证信息、面板口令。使用 CLI 支持的私有文件输入与脱敏输出；不要用普通命令行参数传密码，也不要要求用户把真实秘密贴到聊天中。
- 文件冲突或外部修改应先检查，不强制覆盖。中断事务先预览 `repair transaction`；已接管房间的人工改动核对后用 `room refresh` 接受新基线。不要用大范围 chown、rm 或改写配置绕过 CLI 保护。

## 任务到命令的映射

以下是命令导航，完整参数通过 `--help` 查询；本表不是对已安装 CLI 版本的保证。

| 用户任务 | CLI 入口 |
| --- | --- |
| 安装本体、准备安装源 | `server install`、`download steamcmd`、`download server`、`auth login`、`network check` |
| 扫描并接管旧服务器 | `room scan`、`room adopt`、`room list` |
| 多房间配置与运行 | `room create/configure/status/start/stop/restart/logs/doctor/refresh/remove` |
| 原有 main 基础管理 | `server configure/start/stop/restart/status/logs` |
| 地图与战役清单 | `map list`、`map info` |
| Workshop/GameMaps 获取 | `map search/resolve/install`、`addon search/resolve/install` |
| VPK 校验、导入和提取 | `map verify/import/extract`、`addon import` |
| 默认地图、在线切图 | `map default`、`map change` |
| 资源启停、卸载与恢复 | `map enable/disable/remove/reinstall/forget`、`addon enable/disable/remove/reinstall/forget` |
| SourceMod 框架与插件 | `plugin sources/stack/list/install/enable/disable/compile` |
| 管理员与控制台 | `admin list/add/remove`、`rcon exec --command-file` |
| 独立后台任务 | `task submit/list/show/logs/cancel/retry/delete` |
| Manifest、VPK ZIP 与旧来源记录 | `transfer export/import/install` |
| 整服或插件栈备份恢复 | `backup create/inspect/restore` |
| 存储与修复 | `storage usage/cleanup`、`repair mission-bom/permissions/transaction` |
| 下载网络配置 | `network status/configure` |
| 可选面板 | `web install/configure/start/stop/restart/status/check/logs/uninstall` |
| 可选 Agent 接入 | `skill show/export/list/status/install/update/uninstall` |

## 首次部署与旧服接管

确认已有 SSH 公钥回连和 sudo 能力后再收紧 SSH。云安全组、本机防火墙和路由/NAT 分别检查；CLI 本地就绪不代表这些入口已放行。主机名、时区、系统更新和账号加固按用户要求处理，不能因安装游戏就自动修改。

`server install` 面向 CLI 管理的新安装，安装后保持停止。已有非空游戏目录使用 `room scan` 和 `room adopt`，接管前核对服务、用户、启动脚本、cfg 和游戏路径；动态脚本、未知 drop-in、无法验证端口归属时先解释阻碍。接管仅登记，不重装或重启。

新房间从已有房间创建，端口独立，游戏目录可共享。`main` 是原有 CLI 安装的房间标识，不要假设它等于旧服的 Room 1。公开/组模式使用短 Steam group ID；不接受 groupID64 或组页面 URL。组优先模式不等于严格成员白名单，不把 GSLT 当成组列表不可见的通用修复。

Steam 认证与下载权限、网络连通性是不同问题。密码/Guard 登录和扫码后端缓存不能混用；完整游戏包离线导入与仅准备 SteamCMD 工具包也不同。遵循实际命令的账号、缓存和来源约束，不把登录成功报告成游戏下载成功。

## 地图、战役与 VPK Mod

先查存储和 `map list`，用实际地图名选择章节。Workshop 安装先 resolve，检查合集、所需多 Part 和完整 install IDs；资源包没有 BSP 不代表安装失败，但整组缺少地图包必须提示。可选 Steam API key 仅作依赖发现增强，不写入 Skill。GameMaps 仅用数字 details ID；下载受站点保护时可选用户已下载的 VPK 导入，不提供任意 URL 绕过入口。

Workshop 多 Part 是多个独立包，VPK 分卷是 `_dir.vpk` 加 `_000.vpk` 等文件，不能混淆。离线导入前检查完整文件集。CLI 校验和提取负责路径、CRC、长度与资源归属；地图与普通 Mod 分开处理，不将含地图的 VPK 当普通 Mod。

`map default` 修改下次启动地图；`map change` 使用 RCON 在线切图，不持久化默认地图。成功后查询当前地图；持久配置与运行状态分别说明。

普通 remove 保留来源记录以便 reinstall；`--purge` 删除来源，forget 用于移除记录，以实际帮助与预览为准。删除/停用前确认所有共享房间的默认图、运行图和共用资源。旧来源记录用 `transfer import --format legacy` 导入，不猜测未登记文件归属。

客户端也需要相同版本资源。重复下载应检查服务端/客户端版本、重复订阅和旧资源冲突；不要未经确认就清空客户端 downloads。用户需要本地备份恢复时先确认本机游戏路径，导出服务端包并由用户授权写入客户端目录，不把 Linux 服务端路径当成本机 Windows 路径。

## SourceMod、SourcePawn 与管理员

VPK Mod 与 MetaMod/SourceMod `.smx` 分开管理。先 `plugin list` 查框架、启用和禁用态；安装使用 `plugin stack` 支持的组件或受信任文件。保留 Left4DHooks 等受保护依赖，不默认启用旧包中的投票链或广播他人信息的公告配置。插件名不能证明功能，按源码与加载结果判断。

自写 `.sp` 通过 `plugin compile --file` 或内置源码编译，按需 `--deploy`；CLI 选择可用编译器并备份产物，不手工覆写受管路径。Source 引擎插件运行架构和 spcomp64 编译器架构是两件事，32 位游戏不能加载 64 位 MetaMod 库。框架安装需包含 SourceMod 的 cfg。

编写插件时保留已知 L4D2 经验：左上角同步 HUD 不可靠，稳定文字播报可用聊天；带 `TIMER_FLAG_NO_MAPCHANGE` 的计时器需在 `OnMapStart()` 重建，并在结束时清理句柄。部署后检查 SourceMod 最近 error session，必要时在已配置 RCON 下查询 `meta version`、`sm version` 和插件列表。没有 RCON 密码时不能伪造验证成功。

管理员先选择实际使用的 `sourcemod`、`left4bots` 或 `admin-system` backend，再添加正确 SteamID。未安装的 EMS 插件无需创建空白管理员目录。回滚保留启用/禁用态；共享游戏目录的插件操作可能影响多个房间。

## 任务、导入导出与备份恢复

长任务可用 `task submit -- <实际 CLI 操作及参数>`，提交成功只代表已接受，继续查询 show/logs 到终态。失败先看阶段与错误；已完成部分可能保留，按实际重试语义处理。取消后核对状态和残留。任务独立于 Web，面板关闭后继续通过 CLI 管理。

来源 Manifest 适合地图/Mod ID 与状态的轻量迁移；ZIP/VPK 适合离线资源传输。导入来源记录与实际重新安装是独立操作。清单缺少 ID 的包无法仅靠名字重新下载，应保留离线包。

整服迁移先创建并 inspect 私有备份，包含配置/启动服务、插件及 disabled 态、源码、管理员、可选网络与面板状态；地图优先保存来源 ID，新机重下。不要把配置事务回滚或单独 Manifest 称作完整服务器备份。

新机先准备游戏与目标房间，预览 restore，核对路径/用户/端口；网络和面板状态按需显式选择恢复，不覆盖整份 hosts/gai.conf，也不默认启动面板。按返回的恢复说明补齐地图、源码重编译、默认图与开机启动，最后在授权范围内启动并查询验证。备份内含秘密，保存在私有目录，不提交仓库或上传公共位置。

## 排障与网络

区分服务进程、端口、游戏查询、资源加载和公网连接。先读诊断与相关时间段日志；模型物理错误、地图脚本缺失、Steam 认证波动不自动等于服务器崩溃。SourceMod 日志和禁用插件清单也属于排障信息。

mission KeyValues 报错检查 BOM 和括号；`repair mission-bom` 只去 BOM，不猜测修补括号。报错指向模型时查包完整性、冲突和客户端版本。权限修复选择明确资源范围，不递归更改整个安装目录。

先 `network status/check`，按实际失败项处理 HTTP 代理、Steam 协议连接或 IPv6 出网。CDN hosts 使用用户确认的有效输入，不内置历史 IP。`--prefer-ipv4` 只用于确有需要的系统配置；HTTP 代理不等于 SteamCMD 全协议已路由，代理服务部署与订阅另行授权。

## 可选 Web 与 Agent Skill

仅在用户需要界面时管理 `web`；确认监听地址、端口、登录配置和访问范围。使用 `web check/status/logs` 验证，全部运维仍可直接调用 CLI。不要再部署旧 helper 来补新 CLI 的功能，也不要将安装 Skill 与启用 Web 绑定。

在 Agent 所在机器运行 `skill list --json`，选择工具及 user/project scope。`skill show` 查看，`skill export --output FILE` 导出单一 Skill；`skill install --agent AGENT` 安装。`--project DIR` 选择项目，`--target-dir DIR` 覆盖 skills/rules 根目录。Continue 仅项目规则适配；以命令帮助中的适配器列表为准。

status 区分 current/outdated/modified/unmanaged/missing/not_installed；update 只更新受管安装。冲突时默认保留用户文件，用户明确要求替换才使用 `--force`，并报告备份位置。uninstall 仅移除本 CLI 登记的文件；不会递归删除技能目录，也不会自动清理以前安装的六个子 Skill。已有旧技能需要用户选择是否停用，避免重复触发。

完成后报告操作对象、实际结果、是否待重启、任务是否结束以及验证范围。说明失败和阻碍，不把模拟检查或本机查询当成真实玩家对局验证。

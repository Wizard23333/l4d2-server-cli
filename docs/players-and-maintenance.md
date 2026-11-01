# 好友开黑与日常维护

适用于 alpha.3。房间操作在实际 Linux 主机执行，默认房间为 `main`；多房间先用 `l4d2 room list` 查明 ID。命令支持 `--json`；修改先用同参数 `--dry-run` 预览，再按授权使用 `--yes`。本页地址、路径和 SteamID 均需替换。

## 复制一份加入说明

```bash
sudo l4d2 room invite --room main --address YOUR_SERVER_IP
sudo l4d2 room connect-check --room main --address YOUR_SERVER_IP
```

`invite` 输出可复制给朋友的房名、当前地图、实际运行端口、控制台命令或 Steam 组大厅步骤、地图准备和故障反馈要求。没有 `--address` 时保留占位，发送前替换；CLI 不会向外部 IP 服务探测公网地址。

`connect-check` 区分服务、UDP 归属、本机查询、待生效配置和当前主机对指定地址的查询，异常时给下一步并返回非零退出码。云安全组、本机防火墙、路由器转发和运营商 NAT 需分别检查。

外网朋友可用 Python 源码/wheel 入口在 Windows、Linux 或 macOS 查询，无需 sudo 或游戏服务：

```bash
l4d2 network probe --address YOUR_SERVER_IP --port 27015
```

Linux 可执行包不能在 Windows 上执行；Windows 使用 Python 入口，例如 `python D:/path/to/l4d2-server-cli/bin/l4d2 network probe ...`。端口使用邀请里的实际端口。

查询通只证明当前电脑收到 L4D2 A2S 响应。查询不通无法单独区分防火墙、NAT、地址错误或服务停止。最终请在正版游戏客户端启用开发者控制台，按邀请加入，测试过关和重连；不要用 RCON 密码加入游戏。

## 分享地图、分包与依赖

```bash
sudo l4d2 map share --room main
sudo l4d2 map share --room main --id WORKSHOP_ITEM_OR_COLLECTION_ID
sudo l4d2 map share --room main --filename campaign_part1.vpk --filename campaign_part2.vpk
sudo l4d2 map share --room main --offline
```

默认选择启用的地图；`--filename` 可重复，也可选 Mod。命令使用登记来源和 `install_ids` 保留全部已知分包，递归展开 Workshop 合集与必需条目，去重并处理循环引用。最多展开 128 条，超限标记不完整。`--id` 只核对指定条目/合集，与文件选择二选一。

Steam 匿名详情接口不保证返回必需条目。需要完整依赖核对时，用私有文件配置自己的 Steam Web API key：

```bash
sudo l4d2 network configure --steam-api-key-file /private/steam-api-key.txt --dry-run
sudo l4d2 network configure --steam-api-key-file /private/steam-api-key.txt --yes
sudo l4d2 map share --room main
```

不要在普通参数、聊天或分享文字中填写 key。未配置 key、条目隐藏、网络失败、依赖超限或本地来源未知时，`complete=false` 并显示待核对项。作者描述中的普通链接只作参考，不自动认定必需。`--offline` 无网络调用，仅按本地记录生成。

GameMaps/本地 VPK 显示来源页（如有）、文件名与已登记摘要，服主需提供同版本文件并核对依赖。朋友先订阅全部分包和必需条目，等待 Steam 下载完成。来源声明也可能漏写依赖，清单不能证明客户端已下载或地图对局已验证。

## 配置好友名单

此功能需要 MetaMod/SourceMod。它根据通过 Steam 后端验证的个人 SteamID64 检查名单，机器人例外，管理员没有自动放行。Steam 组优先是另一种原生加入方式，不是严格名单。

先查看共享安装范围，安排停止所有共享游戏目录的房间。框架安装和访问策略均先预览：

```bash
sudo l4d2 room list
sudo l4d2 plugin stack --room main --component core --dry-run
sudo l4d2 plugin stack --room main --component core --yes
sudo l4d2 access configure --room main --mode friends --allow-file /private/friends.txt --dry-run
sudo l4d2 access configure --room main --mode friends --allow-file /private/friends.txt --yes
sudo l4d2 room start --room main --yes
sudo l4d2 access status --room main
```

`friends.txt` 每行一个个人 SteamID64，包含服主，可用 `#` 开头注释；不接受昵称、主页 URL、组 ID 或 Steam2 格式。

CLI 编译内置插件，按游戏端口写独立策略；所有共享目录房间会有显式策略，首次未选择的房间保留公开。保存前做插件备份，保存后不自动启停。CLI 启动核对文件摘要及插件的新鲜运行回执，策略缺失/被改动时拒绝启动；未取得有效回执会按启动失败处理。新建共享房间、改端口或恢复插件备份后，需要重新配置策略。

回到公开方式仍需停止共享房间并显式保存：

```bash
sudo l4d2 access configure --room main --mode public --dry-run
sudo l4d2 access configure --room main --mode public --yes
```

插件复查已连接玩家，身份验证失败或不在名单的真人会被踢出；验证有短暂等待，因此不是网络层连接过滤。插件/框架被卸载、崩溃或绕过 CLI 启动时不能保证限制。`room health` 或定时检查可发现回执失效，只报告异常，不自动重启。

`runtime_verified` 是文件及插件回执核对，不是玩家验收。发布时真实加入仍为 `unverified`。开放给朋友前按下表验收，记录版本、时间、房间、结果和脱敏日志：

| 场景 | 核对结果 |
| --- | --- |
| 名单内正版账号加入、断开再加入 | 身份验证、进图、重连成功 |
| 名单外正版账号直连/大厅加入 | 验证后拒绝，管理员不能绕过 |
| 换图、过关、房间重启 | 名单继续生效，回执刷新 |
| 删除策略、停用框架、Steam 验证失败 | 报告故障，不误报已保护 |
| 多房间不同名单 | 按端口使用独立策略，公开房间不误拒绝 |

## 停服更新官方游戏

```bash
sudo l4d2 room update --room main --dry-run
sudo l4d2 room update --room main --yes
```

更新作用于游戏目录，所有共享目录房间必须先停止。CLI 先保存私有配置/插件备份，再以登记的游戏账号调用 SteamCMD 更新和校验，检查运行依赖与受保护配置，完成后保持停止。

默认尝试匿名下载并复用已登记工具来源。有授权要求时，按 [Steam 登录](steam-auth.md) 以普通用户验证缓存，再指定同一账号和工具：

```bash
sudo l4d2 room update --room main --steam-user YOUR_STEAM_LOGIN_NAME \
  --steamcmd-path /home/YOUR_USER/private-cache/steamcmd/steamcmd.sh --yes
```

至少预留 5 GiB，大型更新可能更多。失败或中断保留半成品、更新记录和备份；用同一设置重跑，完成校验后才能由 CLI 启动。若更新改变受保护配置/插件，先核对并恢复备份，再重试。备份不含旧游戏二进制，不承诺自动回滚旧游戏版本。先检查插件兼容性再启动和实测。本版不在有人游玩时自动停服更新。

## 定时备份与健康检查

先全局安装 CLI。定时任务要求 root 拥有且普通用户不可改写的入口，默认 `/usr/local/bin/l4d2`；便携入口需指定 `--cli-path`。

```bash
sudo l4d2 schedule add --id nightly --room main --kind backup --at 04:00 --keep 7 --dry-run
sudo l4d2 schedule add --id nightly --room main --kind backup --at 04:00 --keep 7 --yes
sudo l4d2 schedule add --id health-main --room main --kind health --every-minutes 15 --dry-run
sudo l4d2 schedule add --id health-main --room main --kind health --every-minutes 15 --yes
sudo l4d2 schedule list
```

每日时间使用**目标服务器时区**，预览显示其时区名。每日任务使用 systemd 日历与漏跑补执行；分钟任务从启动/上次结束后计时。桌面、SSH 或 Web 关闭后，服务器 timer 仍执行。同一任务不重叠运行。

备份为私有 ZIP，包含配置、插件、Mod 和来源清单，地图主体和官方游戏二进制不进入备份。新备份成功后，只删除该任务历史中超过保留数、路径与摘要匹配的旧备份；其他备份保留。移动或修改旧文件会停止清理并报告异常。备份含秘密，勿公开或发给朋友。恢复访问插件后需重新配置名单。

备份会核对捕获文件的实际摘要。持续写入的插件数据可能造成快照冲突，任务会失败并保留旧备份；先查看结果，必要时在停服窗口试跑。访问插件的临时心跳回执不作为备份状态。

健康检查核对本机就绪、待生效设置、空间、资源读取、更新记录和访问回执，异常返回非零退出码。结果保存在服务器并可从 `schedule list`、systemd 服务日志查看，不主动发送消息或重启游戏。

先手动试跑；停用任务保留备份：

```bash
sudo l4d2 schedule run --id nightly --dry-run
sudo l4d2 schedule run --id nightly --yes
sudo l4d2 room health --room main
sudo l4d2 schedule remove --id nightly --dry-run
sudo l4d2 schedule remove --id nightly --yes
```

完整结果用 `schedule list --json`，日志用 `journalctl -u l4d2-cli-nightly.service`。不需要健康检查时也应移除对应任务。

实现依据：[Steam Workshop 详情接口](https://partner.steamgames.com/doc/webapi/IPublishedFileService)、[SourceMod 已验证身份接口](https://github.com/alliedmodders/sourcemod/blob/master/plugins/include/clients.inc)、[systemd timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)。

# 配置与权限

CLI 只管理 `main`，unit 为 `l4d2-main.service`。首次可指定安全绝对游戏路径，推荐 `/opt/l4d2`；后续不能通过 `--dir` 静默迁移。

| 路径 | 用途 | 权限 |
| --- | --- | --- |
| /etc/l4d2-cli/config.toml | 公开配置及期望版本号 | root，0644 |
| /etc/l4d2-cli/secrets.toml | RCON 密码（兼容读取旧入场密码以便清除） | root，0600 |
| /var/lib/l4d2-cli/state.json | 来源、阶段、运行状态，无密码 | root，0644 |
| /var/lib/l4d2-cli/private-hashes.json | 配置摘要 | root，0600 |
| /var/lib/l4d2-cli/config-transaction.json | 临时回滚记录，可能包含秘密 | root，0600 |
| /var/lib/l4d2-cli/steamcmd | 工具与可能存在的登录缓存 | l4d2，目录 0700 |
| /etc/systemd/system/l4d2-main.service | 服务定义 | root，0644 |
| /opt/l4d2/left4dead2/cfg/l4d2-cli.cfg | 游戏配置，含秘密 | root:l4d2，0640 |

游戏以专用系统用户 `l4d2` 运行，不授予 sudo。安装、配置、启停和日志需要管理员授权。普通用户可读非敏感状态和诊断，但私有配置完整性及端口归属可能显示 unknown。

使用 CLI 修改配置，不直接改受管文件。外部变更退出 4，不自动覆盖。配置保存不会重启；运行中改端口时仍查询已记录的运行端口，下一次重启使用新配置。

```bash
sudo l4d2 server configure --name "Friends Server" --map c2m1_highway \
  --mode coop --difficulty hard --no-autostart --yes

# 私有 UTF-8 密码文件，密码不传入普通命令行
chmod 600 /path/to/rcon-password.txt
sudo l4d2 server configure --rcon-password-file /path/to/rcon-password.txt --yes
sudo l4d2 server configure --clear-join-password --clear-rcon-password --yes

sudo l4d2 server configure --mode versus --yes
sudo l4d2 server restart --yes
```

房间名称只接受 ASCII；原版引擎会截断非 ASCII 名称。非空入场密码在真实原版服务器上造成 A2S 不回复，CLI 返回 `unsupported_join_password`，不会落盘；已有配置先用 `--clear-join-password` 清除。`--join-password-file` 仅保留兼容解析，不作为可用的私密房间功能。RCON 密码不受此限制。这也不影响 Steam 密码/Steam Guard 登录。

默认绑定全部网卡时，CLI 通过无发包的路由查询选取本机 IPv4 执行 A2S；实际网卡查询成功后才算就绪。本机 IPv4 不作为公网地址，外网查询/客户端连接另验。

显式 difficulty 只用于 coop；versus 使用游戏默认人数与难度。开发源码接受已安装的官方或自定义地图，名称经过校验，并扫描 loose BSP 与启用的 VPK；c14 暂限定 coop，实际兼容性待验收。

密码文件必须无符号链接、无组/其他用户权限、最大 1024 字节；密码最大 256 字符，允许末尾一行换行，内部控制字符不允许。向导空输入保留原密码；清除用显式 clear 参数。

autostart 只 enable/disable，不影响当前游戏。stop/restart 展示地图与可查询的玩家数量后确认。非终端或 JSON 模式用 `--yes`。

status 退出 0 表示成功取得报告，也可能是停机/未安装。自动化读取 game_ready、readiness 与服务状态。doctor 对失败项返回非零；公网连通和玩家加入仍需外部验收。


## 公开与 Steam 组模式

配置字段为 `visibility`（默认public）和 `steam_group_id`（默认空）。旧配置缺少字段时按默认值读取，不自动重写文件。

| 模式 | sv_steamgroup | sv_steamgroup_exclusive | sv_allow_lobby_connect_only | 加入方式 |
| --- | --- | --- | --- | --- |
| public | 空 | 0 | 0 | 外部客户端直连/大厅 |
| group | 指定短 ID | 1 | 1 | Steam 组成员账号，通过 Steam 组服务器列表/大厅验证 |

```bash
sudo l4d2 server configure --visibility group --steam-group-id YOUR_SHORT_GROUP_ID --dry-run
sudo l4d2 server configure --visibility group --steam-group-id YOUR_SHORT_GROUP_ID --yes
sudo l4d2 server restart --yes

# 简写；同时选择 group
sudo l4d2 server configure --steam-group-id YOUR_SHORT_GROUP_ID --yes

# 切回公开，自动清空原组 ID
sudo l4d2 server configure --visibility public --yes
sudo l4d2 server restart --yes
```

组 ID 只接受单个短十进制正整数（1..4294967295），不接收 groupID64、URL、多组列表、负数或配置文本。开发源码允许显式组合 `--visibility public --steam-group-id YOUR_SHORT_GROUP_ID`，表示关联组但不限制公开加入；单独 `server configure --visibility public` 继续清空组 ID。group 首次选择时需要 ID，已有组模式可保留原 ID。仅更改名称/地图等参数不会清除组配置。

status 的 `desired_config` 显示已保存设置，`running_config` 显示由 CLI 记录的当前运行设置；无法确定运行版本时为 null。运行中保存新模式不会重启，`configuration_pending=true`，连接指南继续按当前运行模式提供；重启成功后才清除 pending。group 模式的 `connection.player_command=null`、`join_method=steam_group_lobby`，不会提供会被大厅限制拒绝的直连指令。

原版引擎实际帮助说明：exclusive=1 限制空房先由组员加入，有玩家后公开玩家也可加入；不能视作强制组员白名单。组 ID 不是登录凭据，设置它不代表 Steam 组列表已刷新或玩家已经加入；列表、组员/非组员及大厅连接均须客户端验证。此功能不要求提供下载账号 token 或 GSLT。

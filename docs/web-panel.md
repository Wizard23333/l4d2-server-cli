# 可选 Web 管理面板

面板基于 L4D2-Server-Manager-Skill 的 `frontend-redesign` 优化分支（`6d4817f`），保留侧栏、深浅主题、房间、资源、安装任务和迁移页面。服务器操作统一调用当前安装的 CLI，读取同一份房间登记、资源来源和任务状态，不再部署旧的 `app.py` 或 `l4d2-webctl` 业务实现。

CLI 和游戏服务器均可独立运行，不要求启用 Web。面板用于管理已经安装或接管的服务器；首次游戏安装、Steam 交互认证以及面板自身的安装维护仍从终端执行。

## 安装与自检

先安装包含面板静态资源的新版 CLI，并确认目标房间已经登记：

```bash
sudo l4d2 room list --json
chmod 600 /path/to/panel-password.txt
sudo l4d2 web install --room main --password-file /path/to/panel-password.txt --dry-run
sudo l4d2 web install --room main --password-file /path/to/panel-password.txt --yes
sudo l4d2 web check --json
```

密码文件是 UTF-8 私有普通文件，密码至少 12 字符。密码不通过普通命令行传入，服务器只保存带随机盐的 PBKDF2 摘要。

默认监听 `127.0.0.1:8081`。通过 SSH 隧道访问时，在客户端执行：

```bash
ssh -L 18881:127.0.0.1:8081 YOUR_SSH_HOST
```

随后打开 `http://127.0.0.1:18881/`，使用安装时选择的用户名（默认 `admin`）和密码登录。端口冲突时使用 `--port` 选择空闲端口。

若需要监听服务器网卡，可显式配置监听地址和端口；主机防火墙、云安全组及反向代理按实际访问方式配置：

```bash
sudo l4d2 web configure --host 0.0.0.0 --port 8080 --room main --dry-run
sudo l4d2 web configure --host 0.0.0.0 --port 8080 --room main --yes
```

安装和配置只重启 Web 服务，不重启任何游戏房间。升级 CLI 后再次执行 `web install --yes` 更新服务入口，保留已有登录配置。

`web check` 只读检查安装状态、服务、可信 CLI 路径、秘密文件权限、sudoers、前端资源、HTTP 健康和实际 CLI 桥接。自检不等同于全部功能验收。

## 页面与命令能力

| 页面 | 能力 |
| --- | --- |
| Overview / Performance | 房间和任务概览，CPU、内存、交换分区、磁盘、运行时间及服务资源 |
| Rooms | 根据实际登记生成房间卡片，选择战役/章节，保存默认地图、按需重启 |
| Install & Jobs | Workshop/GameMaps 搜索、ID 安装、浏览器下载链接、本地 VPK 校验导入、后台任务进度、取消、重跑及历史 |
| Map Packages | 来源记录、分包分组、地图包启停、重装、删除、来源清单与 ZIP 导出 |
| Mods & Plugins | VPK Mod 启停、框架安装、动态插件列表、依赖保护、按实际共享房间重启 |
| Installed Maps | 已装战役/章节及过滤 |
| Migration | 来源 Manifest 和离线 VPK/ZIP 导入导出 |
| Operations | 从 CLI 参数定义生成表单，覆盖房间创建/接管/配置/启停、在线切图、插件编译、管理员、RCON、备份恢复、日志、诊断、网络、存储和修复 |

侧栏底部可选择资源所属房间。共享游戏目录的变更遵循 CLI 的影响范围；插件页只会重启实际共享该目录的房间。

Operations 中选择操作、填写参数或上传文件，先执行 `Preview change`，核对计划后执行 `Apply reviewed change`。预览确认与参数绑定，五分钟后失效。长时间变更可选择后台任务；导出和备份完成后可下载结果。

地图包停用同时处理已提取资源；默认图、正在运行的图及共享资源受到保护。未登记来源/文件归属的旧地图先通过 `transfer import --format legacy` 导入来源信息。Workshop 本地文件关联使用公布的文件大小匹配分包，并校验 VPK 内容；该匹配不是作者签名。

如果源站元数据需要代理，而大文件 CDN 直连更快，可显式使用 `network configure --direct-downloads --yes`（面板 Operations 也提供此项）。使用 `--proxy-downloads` 恢复安装文件也走代理；默认保持代理同时用于元数据和下载。

## 服务维护

```bash
sudo l4d2 web status --json
sudo l4d2 web logs --lines 100
sudo l4d2 web stop --yes
sudo l4d2 web start --yes
sudo l4d2 web restart --yes
sudo l4d2 web uninstall --dry-run
```

卸载停止并移除 Web unit 和该面板的 sudoers；保留配置、私有凭据和传输文件，便于恢复。游戏、房间和 CLI 运维数据继续保留。

| 路径 | 用途 |
| --- | --- |
| `/etc/l4d2-cli/web.json` | 非秘密面板配置 |
| `/etc/l4d2-cli/web-secrets.json` | 密码摘要和会话秘密，root/面板组，0640 |
| `/var/lib/l4d2-cli/web/` | 上传、导出及下载暂存 |
| `/etc/systemd/system/l4d2-cli-web.service` | 可选服务，运行用户 `l4d2web` |
| `/etc/sudoers.d/l4d2-cli-web` | 仅允许固定 CLI 的 `web _bridge`，无 shell 或通配参数 |

耗时任务由独立 transient systemd unit 执行，停止 Web 后继续运行；使用 `l4d2 task` 查看和管理。旧版面板与新版面板使用不同服务名，切换端口前先停止占用该端口的旧面板，保留其配置和状态作回退。

## 验收

```bash
python3 -m unittest discover -s tests -p test_web.py -v
sudo python3 scripts/acceptance/web_panel.py \
  --url http://127.0.0.1:8081 --password-file /path/to/panel-password.txt \
  --room YOUR_ISOLATED_TEST_ROOM --work-dir /path/to/private-test-output
```

默认进行只读验收。追加 `--mutations` 明确授权在指定隔离房间安装和清理合成资源，验证上传、任务、地图/Mod 启停、默认图保护、管理员及 ZIP 回导；合成 BSP 仅用于文件操作验收，不作为可玩地图。游戏启停、真实地图切换、插件加载、备份恢复及浏览器渲染另外按实际环境验收。

前端开发源码在 `web/frontend/`：

```bash
cd web/frontend
npm ci
npm run build
```

构建会执行 TypeScript 检查并更新随 CLI 分发的本地静态资源，不需要在服务器安装 Node.js 或运行前端开发服务器。

GameMaps 可能返回 HTTP 403，自动下载受源站限制时，任务会显示失败原因。可改用对应 Workshop 来源，或在浏览器完成下载后通过本地 VPK 上传安装；该限制不应作为自动下载验收通过。Workshop 的可达性也取决于部署网络。

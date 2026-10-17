# Steam 首次登录与非匿名下载

## 何时需要登录

CLI默认使用匿名身份向Steam下载官方服务端内容，匿名下载成功即可继续安装。如果Steam拒绝匿名下载授权（例如 `No subscription`），可选择账号下载，由Steam按该账号的权限处理内容请求。无需为正常的匿名安装预先登录。

`auth login`完成账号认证并生成私有缓存，安装或制作游戏包时再显式使用同一账号和缓存。登录核验不等于下载授权核验，是否能取得所需内容以实际下载为准。

DNS、连接超时和代理异常需要另行处理；登录不能修复网络。使用完整游戏包离线导入时，目标机无需登录Steam。房间公开/Steam组设置及已安装房间的日常启停独立于下载账号认证。

首次账号登录不需要已有缓存。以普通用户在真实Linux终端执行；远程使用 `ssh -t`。

## 首次登录

```bash
AUTH_CACHE="$HOME/.local/share/l4d2-cli/first-login"
l4d2 auth login --fresh --cache "$AUTH_CACHE"
```

流程：输入 Steam 登录账号名 → 准备/更新工具 → 原生密码提示 → 按提示完成 Steam Guard → CLI 再执行一次禁止密码提示的缓存登录核验。
`--steam-user NAME` 可跳过账号名提示。密码/验证码交给 SteamCMD，不放进参数、聊天或 CLI 日志。

`--fresh` 要求新建或空目录；遇到非空目录返回 `fresh_cache_required`，不会删除旧登录。换另一个 `--cache` 可重来。已有工具的 config/Steam/config 不会导入；含登录文件的工具归档会被拒绝。普通模式仍支持已有缓存。

可以额外选择 `--steamcmd-archive ./steamcmd_linux.tar.gz` 或 `--steamcmd-path /path/to/steamcmd.sh`，二者互斥。最小工具包仍可能联网自更新。`auth login` 不安装系统包，不再要求整套游戏依赖，但 SteamCMD 本身仍需可运行的32位环境；更新/运行失败请先处理原生终端错误。总时限默认300秒，可加 `--timeout 600`。

## 登录成功后

只有返回成功并显示 `cached_login_verified=true` 后才使用此缓存：

```bash
sudo l4d2 server install --steam-user YOUR_STEAM_LOGIN_NAME \
  --steamcmd-path "$AUTH_CACHE/steamcmd/steamcmd.sh"

l4d2 download server --output ./server-linux.tar.gz \
  --steam-user YOUR_STEAM_LOGIN_NAME --steamcmd-path "$AUTH_CACHE/steamcmd/steamcmd.sh"
```

使用刚登录的同一账号名和缓存路径。登录成功不代表下载授权或 Linux 平台已验证，`download_authorization` 仍为 unknown。
缓存过期可在原目录不带 --fresh 再登录；要重测首次登录则选另一个空目录并加 --fresh。跨机器复制不保证免认证。

## 凭据与终端

要求 stdin/stdout/stderr 均为前台终端；拒绝 root、--json、--password 和 --token。原生进程保留控制终端并接管前台进程组，CLI 不捕获交互；取消/超时回收本次进程组、恢复终端与回显。原生输出可能显示账号名。
目录0700，配置等非可执行文件0600。原生登录配置可能含可复用 token，不能上传 GitHub、工单或游戏包。安装/打包克隆缓存，不改来源，游戏包不含登录配置。
Steam Web API Key/GSLT 不能替代 SteamCMD 登录凭据。依据 [Valve 的缓存登录说明](https://partner.steamgames.com/doc/sdk/uploading#Using_SteamPipe_In_A_CI_CD_Environment)。网络、授权、平台和内容下载分别验收，HTTP代理成功不证明Steam内容连接可用。

## 登录失败时

原生认证成功但缓存复验失败时保留原缓存，检查网络和Steam Guard状态，然后不带 `--fresh` 重试。不要把账号登录成功当作应用下载授权成功；安装/下载遇到授权或平台拒绝时，以实际错误阶段为准。

密码登录为默认方式，也可显式加 `--method password`。可选的 [扫码登录](qr-login.md) 使用独立后端和缓存。

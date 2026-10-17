# 二维码登录（可选）

密码登录仍是默认方式；显式使用 `--method password` 也保持原流程。扫码使用独立的 DepotDownloader 后端，其凭据不能作为 SteamCMD 缓存使用。

## 准备后端

使用 [SteamRE/DepotDownloader 官方发布](https://github.com/SteamRE/DepotDownloader/releases/tag/DepotDownloader_3.4.0) 中的自包含 Linux x64 文件。当前适配版本为3.4.0；使用自包含程序，无需另装系统.NET。

```bash
curl -fL https://github.com/SteamRE/DepotDownloader/releases/download/DepotDownloader_3.4.0/DepotDownloader-linux-x64.zip -o DepotDownloader-linux-x64.zip
unzip DepotDownloader-linux-x64.zip -d ./depotdownloader
chmod 700 ./depotdownloader/DepotDownloader
```

也可在其他机器下载后上传；CLI 接受本地可执行文件，不要求目标机能访问 GitHub。归档/DLL不能直接传给 --depotdownloader-path。

## 扫码与核验

```bash
QR_CACHE="$HOME/.local/share/l4d2-cli/qr-first-login"
l4d2 auth login --method qr --fresh --cache "$QR_CACHE" \
  --depotdownloader-path "$PWD/depotdownloader/DepotDownloader"
```

输入 Steam 登录账号名（不是昵称），然后使用 Steam 手机 App 扫描终端二维码并确认。账号名用于随后验证同一个账号的缓存，不会与 -qr 同时传给后端。无需输入账号密码或关闭 Steam Guard。

每次 --fresh 都需新的/空目录；非空缓存不会被覆盖。继续同一目录时去掉 --fresh，可再次扫码。过期二维码由后端自动更新；取消或超时会恢复终端。命令只在扫码后取得新的 Linux depot222863清单、并用缓存再次取到清单时报告成功。它仍不证明完整游戏已下载。

会话存放在指定私有目录中的 .NET IsolatedStorage，目录0700、文件0600。CLI仅在私有登记文件记录账号名、后端散列和核验状态，不读取/输出原始token。SteamCMD密码缓存与扫码缓存互不替代。

## 下载和安装

```bash
l4d2 download server --qr-cache "$QR_CACHE" --output ./server-linux.tar.gz
sudo l4d2 server install --server-archive ./server-linux.tar.gz --offline
```

扫码缓存由 DepotDownloader 使用，生成完整 Linux 游戏包后交给既有安装器导入。直接安装的 SteamCMD 密码流程保持不变；server install 没有 --method qr 参数。离线导入前备齐系统依赖。

完整下载从官方 app222860 的 public 分支选择 Linux 内容，校验后再检查服务端运行文件与默认官方地图并打包。首次采用20GiB准备预算；登记半成品可按实际分配块抵扣且至少保留2GiB，失败内容保留供重试。不导出扫码会话、account.config 或 .DepotDownloader 元数据。清单注明 DepotDownloader 来源；未取得游戏build时保留未知值。

## 网络与支持范围

能访问Steam网页/API不保证能连接Steam CM或取得二维码；HTTP代理成功也不能证明Steam内容协议已通过代理。使用可连通Steam的网络；手机App与服务器出口地区差异可能触发Steam风险检查，应核对自己发起的登录，不关闭Steam Guard。

二维码生成已有实机验证，批准登录、缓存复用及完整Linux下载全链路尚未完成实机验证，当前作为实验能力提供；默认密码方式仍保留。

外部程序遵循其 [上游许可证](https://github.com/SteamRE/DepotDownloader/blob/DepotDownloader_3.4.0/LICENSE)，不打包进本项目源码、wheel或可执行包。

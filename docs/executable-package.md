# Linux x86_64 可执行包

版本：`v0.1.0-alpha.1`。从 [预览版Release](https://github.com/Wizard23333/l4d2-server-cli/releases/tag/v0.1.0-alpha.1) 下载 [Linux x86_64可执行包](https://github.com/Wizard23333/l4d2-server-cli/releases/download/v0.1.0-alpha.1/l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.tar.gz) 和 [SHA256校验文件](https://github.com/Wizard23333/l4d2-server-cli/releases/download/v0.1.0-alpha.1/l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.sha256)。

包内有 `l4d2`、`_internal/`、许可证及构建来源，内置CPython3.12运行时，不需要安装系统Python、venv或pip。请保留整个目录，不能只复制 `l4d2` 文件。CLI与游戏系统依赖分别安装。

## 解压运行

将可执行包与同名 `.sha256` 文件放在同一目录：

```bash
curl -fLO https://github.com/Wizard23333/l4d2-server-cli/releases/download/v0.1.0-alpha.1/l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.tar.gz
curl -fLO https://github.com/Wizard23333/l4d2-server-cli/releases/download/v0.1.0-alpha.1/l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.sha256
sha256sum -c l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.sha256
tar -xzf l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64.tar.gz
cd l4d2-server-cli-v0.1.0-alpha.1-linux-x86_64
# alpha.1 的旧附件不含安装器；后续发布包内置 install.sh
curl -fLo install.sh https://raw.githubusercontent.com/Wizard23333/l4d2-server-cli/main/scripts/packaging/install.sh
sudo bash ./install.sh
l4d2 --version
l4d2 --help
```

在运行systemd的Linux x86_64/glibc主机使用；可执行包采用glibc2.31基线，不支持ARM、Alpine/musl或Windows。其他发行版仍需分别验证，不能根据打包成功声称全部可用。

```bash
l4d2 doctor
l4d2 network check
sudo l4d2 server install --dir /opt/l4d2
sudo l4d2 server configure --name "My L4D2 Server" --visibility public
sudo l4d2 server start
sudo l4d2 server status --json
```

`doctor`诊断当前主机和登记状态；尚未安装、缺依赖或网络不可用时可能报告失败。CLI不自动开放主机防火墙/云安全组；需放行游戏UDP端口并从外部客户端确认能加入。账号认证使用普通用户执行 `l4d2 auth login`，不要用sudo。

本包不包含SteamCMD、游戏文件、账号缓存、生产配置或测试报告。游戏安装仍需32位库、可用下载来源及足够磁盘。离线备用来源和全部参数以 `l4d2 COMMAND --help` 为准。

安装器默认校验 `FILES.sha256`，将清单中的文件和包内相对链接复制到 `/usr/local/lib/l4d2-server-cli/<清单摘要>/`，设置安装账号所有权并去除组/其他账号写权限，再创建 `/usr/local/bin/l4d2`。因此普通用户及sudo均可在任意目录使用同一命令；原下载和解压目录可移除。安装CLI不改动游戏文件、配置或服务。

重复安装相同包会复用已校验目录；升级先验证新目录再切换命令，失败时保留原入口。已有其他程序的同名文件或链接不会覆盖。无需管理员权限时可用 `bash install.sh --prefix "$HOME/.local"`，并自行把 `$HOME/.local/bin` 加入PATH；该方式不注册sudo命令。便携运行仍支持 `./l4d2`，必须保留完整目录。CLI不会自动升级自己。

## 构建和验证

构建脚本为 `scripts/packaging/build_linux.py`，在Linux上用固定运行时和工具构建目录包，不从Windows交叉编译、不发布到GitHub。`build-info.json`记录运行时来源/SHA256、工具版本、受测代码文件摘要及动态库要求；`FILES.sha256`可在解压目录内核对文件。

当前仍为alpha预览版；支持环境及已知限制见项目Linux支持说明；包入口验证不替代完整游戏安装或客户端对局。

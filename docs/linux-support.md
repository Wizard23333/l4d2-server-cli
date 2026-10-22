# Linux 支持范围

CLI管理Linux x86_64专用服务器，要求glibc和实际运行的systemd。ARM、Alpine/musl、无systemd容器及Windows原生服务管理不受支持。

可选 `skill` 命令仅处理本地 Agent 指令文件，可在 Windows/Linux/macOS 的 Python 3.8+ 源码或 wheel 中运行，无需游戏或 systemd。此例外不扩展服务器运维平台范围，详见 [Agent Skill](agent-skill.md)。

## CLI运行方式

| 安装方式 | Python要求 | libc要求 |
| --- | --- | --- |
| Linux x86_64可执行目录包 | 内置CPython3.12，不需要系统Python/pip | glibc2.31+打包基线 |
| 源码或wheel | 系统Python3.8+；3.8–3.10随附Tomli，无需在线安装运行依赖 | 主机glibc，且能运行SteamCMD/游戏 |

可执行包应保留整个目录和内部符号链接。游戏的32位依赖、systemd及系统工具仍由目标主机提供，不能由内置Python替代。

## 发行版依赖适配

| 家族 | 包管理器 | 主要游戏依赖 |
| --- | --- | --- |
| Ubuntu、Debian及兼容衍生版 | APT / dpkg-query | ca-certificates、lib32gcc-s1、lib32stdc++6、lib32z1 |
| Fedora、RHEL、Rocky、AlmaLinux及兼容衍生版 | DNF，缺少时YUM / RPM | ca-certificates、glibc.i686、libgcc.i686、libstdc++.i686、zlib.i686 |
| openSUSE、SUSE | Zypper / RPM | ca-certificates、glibc-32bit、libgcc_s1-32bit、libstdc++6-32bit、libz1-32bit |
| Arch、Manjaro及兼容衍生版 | Pacman | ca-certificates、lib32-glibc、lib32-gcc-libs、lib32-zlib |

CLI使用os-release的ID/ID_LIKE选择已实现的家族适配，未知家族明确拒绝。缺少ss、useradd、runuser、ldd或nologin时会检查对应依赖包。

发行版版本间包名和仓库可用性存在差异。Arch需管理员预先启用multilib并保持完整系统更新；CLI不自动启用额外仓库，不执行 `pacman -Sy` 局部刷新，也不关闭签名校验、SELinux或防火墙。

## 主机和网络条件

- 安装、配置及启停使用sudo；游戏以独立普通用户运行。
- 首次游戏安装采用20GiB空闲预算。已登记半成品按实际分配块抵扣，至少保留2GiB；失败重试继续使用同一登记目标。
- 游戏目录、服务名、端口需无冲突；不自动接管已有非空游戏目录或同名服务。
- 在线安装需要Steam和必要的软件源可达；已有本地工具包仍可能触发SteamCMD更新。
- 严格离线导入需事先备齐系统依赖；安装离线不代表游戏认证/运行可以断网。
- CLI不自动修改防火墙、云安全组或NAT，需自行放行游戏UDP端口并从外部客户端验证。

## 验证范围

Ubuntu20.04 x86_64/glibc2.31已有真实官方内容下载安装、systemd生命周期及公网查询验证。可执行包已在同一环境验证独立运行、现有服务读取、只读操作计划和经HTTP代理的官方工具下载；未用可执行包重新安装或重启游戏。其他发行版具有适配和回归覆盖，尚未分别完成干净环境下的完整流程验证；不能将发行版识别成功当作安装成功。

当前为alpha预览版。二维码批准与完整下载、严格离线完整游戏包、versus及长时间/多玩家场景仍需进一步验证。网络预检只反映当次已探测入口，不能保证整个Steam下载过程持续可用。

# 完整原版游戏包格式 v1

在另一台网络正常的受支持 Linux 主机准备官方内容，再在目标主机导入。当前为alpha包格式，严格断网的完整制作/导入链路尚未完成实机验证。

```text
l4d2-server-linux.tar.gz
├── manifest.json
└── serverfiles/
    ├── srcds_run
    ├── srcds_linux
    ├── bin/...
    ├── left4dead2/...
    ├── left4dead2_dlc1/...
    └── steamapps/...
```

manifest 记录 format=l4d2-server-bundle、schema_version=1、app_id=222860、os=linux、host_arch=x86_64、UTC 生成时间、可得的游戏 build、来源与 files 列表。

- 文件：相对 path、type=file、size、mode、SHA256。
- 内部符号链接：相对 path、type=symlink、target；目标必须在 serverfiles 中、存在且非链接链。不支持硬链接/设备文件。
- 最多 150000 个成员，展开总量最多 50 GiB；安装另检查磁盘空间。

prepare 只下载到新暂存目录，排除 addons/MOD、登录缓存、私有配置、server.cfg、CLI cfg、日志与私钥。系统依赖需要在断网前备齐。

导入核对可选整体 SHA256，检查 manifest、逐文件散列/大小/权限、平台、Linux ELF runtime、官方资源和安全路径；拒绝逃逸、重复/未列出成员和链接越界。解压到暂存目录后提交，已有登记半成品保留。

散列证明传输与内部一致性，不证明发布者身份或官方来源。只使用可信主机经 CLI 准备的包。SteamCMD bootstrap 与完整游戏包格式不同，不能互换；游戏包不随 GitHub 源码发布。

offline 导入不下载、不执行 SteamCMD 或 apt。缺依赖/runtime/地图时失败并记录阶段。游戏运行与玩家认证需要的网络另行验收。

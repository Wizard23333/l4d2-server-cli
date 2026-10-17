# 实机验收脚本

仅用于已完整安装、已停止且允许修改配置的 CLI 测试房间。脚本核对 `--expected-game-dir`，只控制固定 `l4d2-main.service`，不负责游戏下载、不操作其他服务。

```bash
sudo python3 scripts/acceptance/vanilla_room.py \
  --cli ./bin/l4d2 --expected-game-dir /opt/l4d2-cli-test \
  --evidence-dir /tmp/l4d2-acceptance-NEW --allow-service-changes
```

证据目录必须尚不存在。脚本会反复启停和更换官方地图，替换该测试房间的 RCON 密码；最终保留 coop/c1m1_hotel、无入场密码、不开机启动的运行房间。结果 JSON 与合成秘密文件保持在 0700 私有目录，不应提交原始目录。

流程覆盖安装后已停止、配置、启动、重复启动、重复安装、运行中保存/pending、重启加载新地图、停止释放端口、重复停止、再次启动、最终房间和 doctor。独立外网 A2S 与游戏客户端加入另验；脚本不会声称已有人进入。实际下载授权和安装需先单独执行并留存证据。

脚本只覆盖以上生命周期断言。逐次结果保存在本地被忽略的 `docs/test-results/`，不提交或分发；脚本不替代完整安装与客户端测试。


## 公开 / Steam 组往返

```bash
sudo python3 scripts/acceptance/steam_group.py \
  --cli ./bin/l4d2 --expected-game-dir /opt/l4d2-cli-test \
  --steam-group-id YOUR_SHORT_GROUP_ID --evidence-dir /tmp/l4d2-group-NEW \
  --allow-service-changes
```

要求初始为已安装、已启动且无在线玩家的公开测试房间，并已配置可用私有RCON。通过已有私密配置在内存中认证，只读取三个非秘密cvar，不打印密码。会执行公开→组→公开→组，最终保留组模式供客户端验收；不会停止旧两房间/面板或修改防火墙。结果目录必须尚不存在且保持私有。实际组列表和客户端加入不由此脚本冒充验证。

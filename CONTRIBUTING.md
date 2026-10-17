# 贡献指南

首个预览版专注单实例原版服务端；开发源码增加多房间、地图、插件、任务、传输和备份等独立运维能力。业务逻辑维护在 `src/l4d2_cli/ops/`，命令接口位于其中的 `cli.py`。可选 Web 面板与 Agent 接入应复用该执行层及其状态格式。

## 本地验证

Python 3.8+，自动测试不需要Steam账号、不启动游戏服务、不调用真实包管理器：

```bash
python -B -m unittest discover -s tests -v
python -B scripts/check_release.py
```

构建检查使用独立虚拟环境中的工具；它们不是CLI运行依赖：

```bash
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
python scripts/check_release.py --artifact dist/l4d2_server_cli-0.1.0a1-py3-none-any.whl --artifact dist/l4d2_server_cli-0.1.0a1.tar.gz
```

Windows只验证兼容逻辑和打包；Linux特定测试会跳过。真实安装/启停应在独立VM或明确授权的测试房间运行，[实机脚本](scripts/acceptance/README.md)会修改测试服务，不属于CI。

## 提交要求

测试代码、测试计划继续维护；逐次运行结果写入本地 `docs/test-results/`，该目录不提交，也不进入源码包。

- 报告问题时提供版本、环境、复现步骤和脱敏错误；不要上传Steam/RCON/代理/SSH秘密。
- 用有意义的回归覆盖改变的行为，避免把模拟通过写成真实安装或客户端通过。
- 修改参数、安装来源和支持边界时，同步README、配置说明及相关测试。
- 保留现有许可证与第三方声明。内置Tomli保持上游内容及SOURCE.json摘要一致。

用户文档维护在 `docs/`，仅描述实际功能、用法和已知限制；需求讨论、开发计划和逐次验收记录保留本地，不提交公开仓库。

预览版发版步骤及只构建验证入口见 [Linux打包维护说明](scripts/packaging/README.md#自动发布预览版)。

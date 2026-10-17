# Linux 可执行包构建

用户安装见项目根README和 `docs/executable-package.md`。这里只处理CLI，游戏和账号数据不进入包；构建和验证结果保存在被忽略的 `docs/test-results/`。

在Linux x86_64、glibc2.31基线上，以普通用户在独立工作目录构建。使用 `runtime.json` 指定的未修改CPython安装包并验证SHA256，解压后创建venv；所需系统工具为objdump/readelf/strip。构建工具版本固定在 `requirements-linux-build.txt`，不写入全局Python。

```bash
/path/to/python/bin/python3 -m venv /path/to/build-venv
/path/to/build-venv/bin/python -m pip install -r scripts/packaging/requirements-linux-build.txt
/path/to/build-venv/bin/python scripts/packaging/build_linux.py \
  --runtime-archive /path/to/cpython-runtime.tar.gz \
  --runtime-licenses /path/to/runtime-source-licenses \
  --work /path/to/new-build-dir --output /path/to/output
```

`--runtime-licenses`使用同一上游标签中的原始 `LICENSE*` 文本（见runtime.json的license_source），不可省略。离线构建可先下载并校验wheel，再用pip的 `--no-index --find-links /path/to/wheelhouse` 安装。脚本拒绝复用已存在的工作目录或覆盖同名包。

发布前在新目录校验摘要并解压，不依赖构建venv/PYTHONPATH；测试版本/所有帮助、原生系统子进程、打包模板、普通用户权限拒绝、sudo只读status/doctor及dry-run，核对前后服务PID/运行标识/配置摘要。启停和完整下载另在授权测试环境验收，不能为打包检查重启现有房间。

目录包会在进程中恢复用户原本的LD_LIBRARY_PATH，避免把内置库路径传给SteamCMD或系统包管理器；解压使用tar以保留安全的内部符号链接和执行权限。

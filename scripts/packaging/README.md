# Linux 可执行包构建

用户安装见项目根README和 `docs/executable-package.md`。这里只处理CLI，游戏和账号数据不进入包；构建和验证结果保存在被忽略的 `docs/test-results/`。

在Linux x86_64、glibc2.31基线上，以普通用户在独立工作目录构建。使用 `runtime.json` 指定的未修改CPython安装包并验证SHA256，解压后创建venv；所需系统工具为objdump/readelf/strip。构建工具版本固定在 `requirements-linux-build.txt`，不写入全局Python。

```bash
/path/to/python/bin/python3 -m venv /path/to/build-venv
/path/to/build-venv/bin/python -m pip install --require-hashes --only-binary=:all: -r scripts/packaging/requirements-linux-build.txt
/path/to/build-venv/bin/python scripts/packaging/build_linux.py \
  --runtime-archive /path/to/cpython-runtime.tar.gz \
  --runtime-licenses /path/to/runtime-source-licenses \
  --work /path/to/new-build-dir --output /path/to/output
```

`prepare_runtime.py --output /path/to/new-inputs` 可自动下载并校验固定运行时和许可证。`--runtime-licenses`使用同一上游标签中的原始 `LICENSE*` 文本（见runtime.json及runtime-licenses.json），不可省略。离线构建可先下载并校验wheel，再用pip的 `--no-index --find-links /path/to/wheelhouse` 安装。脚本拒绝复用已存在的工作目录或覆盖同名包。

发布前在新目录校验摘要并解压，不依赖构建venv/PYTHONPATH；测试版本/所有帮助、原生系统子进程、打包模板、普通用户权限拒绝、sudo只读status/doctor及dry-run，核对前后服务PID/运行标识/配置摘要。启停和完整下载另在授权测试环境验收，不能为打包检查重启现有房间。

目录包会在进程中恢复用户原本的LD_LIBRARY_PATH，避免把内置库路径传给SteamCMD或系统包管理器；解压使用tar以保留安全的内部符号链接和执行权限。

## 自动发布预览版

`.github/workflows/release.yml`监听 `vMAJOR.MINOR.PATCH-alpha.N`、`-beta.N`、`-rc.N` 的tag推送（N从1开始）。这条流程始终发布为Pre-release，不设置为Latest稳定版；不需要另外配置PAT或Steam账号，使用发布任务自己的 `GITHUB_TOKEN`。

发下一版时：

1. 同时修改 `pyproject.toml` 和 `src/l4d2_cli/__init__.py` 的版本，例如 `0.1.0a2`。
2. 按需新增 `docs/releases/v0.1.0-alpha.2.md`，描述实际功能与限制；未提供时自动生成带正确版本下载链接的简要说明。
3. 提交并推送到main，然后创建匹配tag：

```bash
git tag -a v0.1.0-alpha.2 -m "v0.1.0-alpha.2"
git push origin v0.1.0-alpha.2
```

对应关系：`0.1.0a2` → `v0.1.0-alpha.2`；`0.2.0b1` → `v0.2.0-beta.1`；`0.2.0rc1` → `v0.2.0-rc.1`。缺少版本同步、无效tag或稳定版tag不会进入预览发布。

流程先校验版本并复用完整CI，再验证固定CPython及20份许可证摘要，用哈希锁定的工具构建目录包。构建后校验归档/文件来源/模板和SHA256，解压运行版本、全部帮助与JSON错误检查，并在Ubuntu20.04容器验证glibc2.31基线。容器检查只运行CLI入口，不代表该发行版完整游戏安装或systemd已验证。

仅构建通过才把 `.tar.gz` 与同名 `.sha256` 作为附件先上传草稿；GitHub返回的两个附件摘要核对一致后才公开为预览版。只有发布任务具备 `contents: write`。已有发布不覆盖；重跑发布任务只接受相同提交和相同附件，部分失败的草稿可继续补齐。重新完整构建的归档可能产生不同摘要，不能借重跑替换已发布版本；应使用新版本tag。

在Actions页面手动运行 **Preview Release** 时，只执行版本检查、CI、构建和解压验证，保存7天Actions产物，不创建tag或Release。可先用这个入口验证打包工具升级。已发布tag不要移动；现有 `v0.1.0-alpha.1` 保持原样。

用户README中的首版固定下载示例不会由发布任务自动提交修改；新版本Release正文和包内INSTALL.md自动使用对应tag，可从仓库Releases列表选择版本。

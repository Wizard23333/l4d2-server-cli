# Agent Skill 安装与维护

CLI 内置一个完整的 `l4d2-manager/SKILL.md`，整合原来的入口与六个子 Skill。它指导 Agent 查询和操作 CLI，不依赖旧面板、其他 Skill 或仓库脚本。Web 与 Agent 接入均为可选能力。

在 **Agent 所在机器**使用包含 `skill` 命令的开发构建或源码。Windows/Linux/macOS 均可执行这些本地文件操作，无需游戏、sudo、SSH 或 systemd。可执行目录包目前仍仅支持 Linux；其他平台使用 Python 3.8+ 源码或 wheel。

```bash
l4d2 skill list --json
l4d2 skill show
l4d2 skill install --agent codex --dry-run
l4d2 skill install --agent codex --yes
l4d2 skill install --agent claude-code --project /path/to/project --yes
l4d2 skill install --agent continue --scope project --yes
```

源码使用 `python bin/l4d2`（Windows）或 `python3 bin/l4d2`（Linux/macOS）替代 `l4d2`。服务器运维仍在已授权的目标 Linux 主机执行，本地安装 Skill 不会安装远程 CLI 或授予访问权限。

## 适配器与安装路径

下表列出根目录：原生适配器追加 `l4d2-manager/SKILL.md`；Continue 追加 `l4d2-manager.md`，为同一内容添加按相关性应用的规则元数据。

| `--agent` | 项目根目录下 | 用户根目录下 | 格式 |
| --- | --- | --- | --- |
| `codex` | `.agents/skills` | `~/.agents/skills` | Skill |
| `claude-code` | `.claude/skills` | `~/.claude/skills` | Skill |
| `cursor` | `.cursor/skills` | `~/.cursor/skills` | Skill |
| `windsurf` | `.windsurf/skills` | `~/.codeium/windsurf/skills` | Skill |
| `cline` | `.cline/skills` | `~/.cline/skills` | Skill |
| `copilot` | `.github/skills` | `~/.copilot/skills` | Skill |
| `continue` | `.continue/rules` | 不支持 | Rule |
| `generic` | `.agents/skills` | `~/.agents/skills` | Skill |

默认 `--scope user`；`--project DIR` 隐含项目范围。单独使用 `--scope project` 选择当前目录，项目目录必须已存在。`--target-dir DIR` 覆盖 skills/rules 根目录，可用于旧版 Codex 根目录或自定义设置。通用适配器仅放置文件，需确认 Agent 能发现该路径。

Continue 仅提供有官方文件路径依据的项目规则安装；Windsurf 的旧项目目录仍受支持。Agent 未发现新增文件时刷新或重启。云端 Agent 可能需要已提交的项目 Skill，不能依赖本机用户目录。

## 查看、更新、卸载和导出

```bash
l4d2 skill status --agent codex --json
l4d2 skill update --agent codex --dry-run
l4d2 skill update --agent codex --yes
l4d2 skill uninstall --agent codex --dry-run
l4d2 skill uninstall --agent codex --yes
l4d2 skill export --output /path/to/SKILL.md --yes
```

管理已安装内容时传入相同的 scope/project/target 参数。状态区分 `current`、`outdated`、`modified`、`unmanaged`、`missing` 和 `not_installed`；使用内容摘要识别更新，同一 CLI 版本号下的内容变化也能识别。

安装生成 `.l4d2-manager.install.json`，记录归属与已安装摘要，它不是另一个 Skill。update 只更新有有效记录的安装。默认拒绝覆盖不同的已有内容；明确使用 `--force` 时先保留 `.bak-*` 备份。uninstall 仅删除受管指令文件和记录，保留目录、备份及用户其他文件；即使使用 `--force` 也不删除未登记文件。无效记录需人工检查。安装路径中的符号链接和 Windows junction 会被拒绝。

内容相同的手工副本可以通过 install 登记。不会修改共享 `AGENTS.md`、SSH、服务器配置，也不会自动删除以前安装的六个子 Skill。旧安装需在各工具中检查后自行移除，避免重复触发。

`skill show` 输出原始 Markdown；`--json` 使用 CLI 的标准结果信封。所有文件变更支持 `--dry-run` 和 `--yes`；预览不创建目录。export 只生成指定的独立文件，不附带安装记录。

## 分发与维护

权威源为 `src/l4d2_cli/skills/l4d2-manager/SKILL.md`，随源码包、wheel 与 Linux 可执行包分发。无需下载远程模板。Skill 仓库仅保留导出镜像，维护者用其中的 `scripts/sync_cli_skill.py --cli-root PATH` 同步，并加 `--check` 检查一致性。

## 官方路径依据

2026-10-17 核对的官方说明：

- [OpenAI 本地 Skill 发现](https://learn.chatgpt.com/docs/build-skills)
- [Claude Code Skills](https://code.claude.com/docs/en/skills)
- [Cursor Skills](https://cursor.com/docs/skills)
- [Windsurf/Cascade Skills](https://docs.windsurf.com/windsurf/cascade/skills)
- [Cline Skills](https://docs.cline.bot/customization/skills)
- [GitHub Copilot Skills](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills)
- [Continue 本地规则](https://docs.continue.dev/customize/deep-dives/rules)

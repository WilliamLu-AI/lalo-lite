# present-plan

在 Codex 或 Claude Code 当前聊天中，用 Lalo 讲解指定文档。项目与此 Skill 均采用 Apache-2.0。

在公开仓库根目录运行：

```powershell
.\install-skill.ps1 -HostName codex
.\install-skill.ps1 -HostName claude
```

也可以用 `-HostName both` 同时安装。Codex 安装到 `~/.agents/skills/present-plan`，Claude Code 安装到 `~/.claude/skills/present-plan`。先按主 README 安装依赖并构建前端；安装器会记录源码目录的位置，启动时使用该目录的 `.venv`。移动目录后重新安装。其他同名、非本安装器管理的 Skill 会保留原样并提示处理。

重新打开或刷新宿主后，Codex 使用 `$present-plan`，Claude Code 使用 `/present-plan`，并指定文档。对应 CLI 需已安装登录；语音服务需要按主说明配置。

`scripts/launch.py` 需要 Python 3.10+，支持 `LALO_SOURCE`、安装器记录的源码路径或仓库内入口，也兼容 `--app` 和 `LALO_BINARY` 可执行文件入口。Claude 会话 ID 应从 Skill 展开的值通过 `--session` 显式传入。

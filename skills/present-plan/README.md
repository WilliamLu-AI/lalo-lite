# present-plan

在 Codex 或 Claude Code 当前聊天中，用 Lalo 讲解指定文档。此 Skill 采用 Apache-2.0；Lalo 应用独立授权。

在公开仓库根目录运行：

```powershell
.\install-skill.ps1 -HostName codex
.\install-skill.ps1 -HostName claude
```

也可以用 `-HostName both` 同时安装。Codex 安装到 `~/.agents/skills/present-plan`，Claude Code 安装到 `~/.claude/skills/present-plan`。安装器会记录仓库内应用的路径，无需设置环境变量；移动仓库后重新运行安装器。已有同名、非本安装器管理的 Skill 时，会停止而不覆盖。

重新打开或刷新宿主后，Codex 使用 `$present-plan`，Claude Code 使用 `/present-plan`，并指定文档。对应 CLI 需已安装登录；语音服务需要按主说明配置。

直接调用应用无需 Python。可选的 `scripts/launch.py` 需要 Python 3.10+，支持 `--app`、`LALO_BINARY`、安装器记录的应用路径或仓库内程序。Claude 会话 ID 应从 Skill 展开的值通过 `--session` 显式传入。
# Lalo Lite

用语音讲解和问答，帮助你读懂 Markdown、HTML 与文本方案。

## 开始使用

请先安装 Git 与 Git LFS，再下载完整仓库：

```powershell
git lfs install
git clone https://github.com/WilliamLu-AI/lalo-lite.git
cd lalo-lite
git lfs pull
```

请勿直接使用 GitHub 的“Download ZIP”：主程序通过 Git LFS 分发，需要下载真实二进制，不能只保留指针文件。

获取完整仓库后，直接运行 `app/windows-x64/lalo.exe`，应用会在浏览器中打开。不需要另外解压应用包，也不需要安装 Python 或 Node.js。请保留 app 目录中的全部文件。

在仓库根目录打开自己的文档：

```powershell
.\app\windows-x64\lalo.exe open --document "D:\documents\proposal.md"
```

使用 AI 问答及语音前，打开右上角“设置 → 语音模型”，添加厂商连接和凭据，再分别选择 ASR、TTS 和独立问答模型并保存。Codex / Claude 及源会话在“模型与会话”中选择。也可通过首次启动生成的 `%LOCALAPPDATA%\LaloLite\.env` 设置初始配置。页面配置保存在用户数据目录的 `voice-config/`，请勿分享该目录。对应服务商可能收取费用。

首次使用语音，下载约 8.3 MiB 的语音模型：

```powershell
.\app\windows-x64\lalo.exe init --download-model
```

下载后重启。关闭浏览器不会退出后台应用，退出请执行：

```powershell
.\app\windows-x64\lalo.exe stop
```

当前是未签名 Windows x64 预览版，macOS 版本尚未提供。

## 在 Codex 或 Claude Code 中使用

在仓库根目录运行以下命令，可安装到两个工具：

```powershell
.\install-skill.ps1 -HostName both
```

仅安装一个工具时，将 `both` 换成 `codex` 或 `claude`。脚本不需要管理员权限，会记录应用位置；移动仓库后重新安装。已有非本安装器管理的同名 Skill 时会停止，请先自行备份处理。若系统策略阻止脚本运行，请先审阅脚本，再按你的设备管理要求允许运行。

重新打开或刷新工具后，在方案所在聊天中使用 `$present-plan`（Codex）或 `/present-plan`（Claude Code），并指定文档。对应 CLI 需事先安装并登录。

## 数据与许可

- 本地配置和使用记录保存在用户目录。不要分享配置文件或应用生成的私人链接。
- 使用 AI 服务时，必要的文档、问题和音频会发送给你配置的服务商。
- 应用闭源分发，用户使用的 present-plan Skill 采用 Apache-2.0。
- 应用和第三方许可随 app 目录提供，文件校验值位于 `app/windows-x64/SHA256SUMS`。
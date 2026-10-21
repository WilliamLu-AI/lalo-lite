# Lalo Lite

通过语音讲解和问答，帮助你阅读 Markdown、HTML 与文本方案。

- 围绕文档提问，边听解释边定位内容块。
- 支持连续讲解、打断、续讲、播放倍速和自动跟随。
- 可连接 Codex 或 Claude Code 的当前会话，也可使用独立问答模型。
- ASR、TTS 和 LLM 分别配置，支持 Native 与 LiveKit AgentSession 两种语音运行时。

## 安装与启动

需要 Python 3.10+、Node.js 20+。当前已验证 Windows；其他系统的设备与 CLI 兼容性需自行验证。

```powershell
git clone https://github.com/WilliamLu-AI/lalo-lite.git
cd lalo-lite
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
npm ci
npm run build
.venv/Scripts/python.exe lalo.py
```

安装后也可以运行 `start.ps1`。macOS / Linux 使用 `.venv/bin/python`，启动入口为 `start.sh`。

打开自己的文档：

```powershell
.venv/Scripts/python.exe lalo.py open --document "D:\documents\proposal.md"
```

浏览器只是阅读入口，关闭页面不会退出本地服务。退出服务：

```powershell
.venv/Scripts/python.exe lalo.py stop
```

## 模型与语音

打开右上角“设置 → 语音模型”，添加厂商连接和 API Key，再分别选择 ASR、TTS 和独立问答模型并保存。Codex / Claude Code 需预先安装并登录，在“模型与会话”中选择。

| 接入方式 | ASR | TTS | LLM |
| --- | --- | --- | --- |
| 千问 | 流式、整句 | Qwen3-TTS-Flash、Realtime | OpenAI 兼容接口 |
| MiniMax | — | 原生连接、LiveKit 插件 | — |
| OpenAI | 整句识别 | LiveKit 插件 | Chat Completions |
| ElevenLabs | 实时识别 | LiveKit 插件 | — |
| OpenAI 兼容服务 | 按服务能力 | 按服务能力 | 按服务能力 |

并非所有型号都支持相同参数；选择具体型号后请先测试连接或试听。OpenAI Realtime 一体化语音接口尚未适配。

千问 TTS 连接选择“千问 TTS”：北京地址为 `https://dashscope.aliyuncs.com`，新加坡地址为 `https://dashscope-intl.aliyuncs.com`，使用对应地域的百炼 Key。模型填写 `qwen3-tts-flash` 或 `qwen3-tts-flash-realtime`，音色例如 `Cherry`。

首次使用语音可安装话轮检测模型：

```powershell
.venv/Scripts/python.exe lalo.py init --download-model
```

模型约 8.3 MiB，按锁定版本下载并校验，不包含在仓库中。下载后重启服务。

默认使用 NativeRuntime。使用 LiveKit AgentSession 时，在用户数据目录的 `.env` 中设置 `VOICE_RUNTIME=livekit`，然后重启服务。本地一人一 AI 使用现有浏览器与 WebSocket，不需要另起 LiveKit Room 服务。

## 在当前聊天中使用

`skills/present-plan` 是面向使用者的文档讲解入口。Windows 可在完成安装后运行：

```powershell
.\install-skill.ps1 -HostName both
```

刷新 Codex 或 Claude Code 后，在文档所在聊天使用 `$present-plan` 或 `/present-plan`。安装器只记录源码目录的位置；移动仓库后需重新运行。

## 数据与许可

配置、加密凭据和本地使用记录保存在用户数据目录：Windows 默认为 `%LOCALAPPDATA%/LaloLite/`，macOS 为 `~/Library/Application Support/LaloLite/`，Linux 为 `~/.local/share/lalo-lite/`。可通过 `LALO_HOME` 更改位置。

仓库中的 `.env.example` 仅包含空凭据示例。请在界面或用户数据目录中填写自己的配置，不要提交 API Key、聊天记录、音频或用户数据目录。调用服务商时，必要的文档、问题和音频会发送给所选服务商，对应服务可能收费。

本项目采用 [Apache-2.0](LICENSE)。字体、话轮模型和依赖保留各自许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

# Lalo Lite

通过语音讲解和问答，帮助你阅读 Markdown、HTML 与文本方案。

- 围绕文档提问，边听解释边定位内容块。
- 支持连续讲解、打断、续讲、播放倍速和自动跟随。
- 支持同一局域网内多人共同阅读、收听讲解与提问，由主持人控制共同位置。
- 可连接 Codex 或 Claude Code 的当前会话，也可使用独立问答模型。
- ASR、TTS 和 LLM 分别配置，支持 Native 与 LiveKit AgentSession 两种语音运行时。

## 安装与启动

需要 Python 3.10–3.13、Node.js 22.22+。当前已验证 Windows；其他系统的设备与 CLI 兼容性需自行验证。

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
| OpenAI 兼容服务 | Audio Transcriptions / Chat Audio（分别选择协议） | Audio Speech | Chat Completions |

并非所有型号都支持相同参数；选择具体型号后请先测试连接或试听。OpenAI Realtime 一体化语音接口尚未适配。

千问 TTS 连接选择“千问 TTS”：北京地址为 `https://dashscope.aliyuncs.com`，新加坡地址为 `https://dashscope-intl.aliyuncs.com`，使用对应地域的百炼 Key。模型填写 `qwen3-tts-flash` 或 `qwen3-tts-flash-realtime`，音色例如 `Cherry`。

可选安装话轮检测模型（不影响云端 ASR 使用）：

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

刷新 Codex 或 Claude Code 后，在文档所在聊天使用 `$present-plan` 或 `/present-plan`。安装器先检查前端构建及应用诊断，再安装 Skill 并记录源码目录的位置，清理旧版本的二进制路径指针。模型凭据和 CLI 登录仍需分别配置；缺少可选话轮模型不会阻止安装。移动仓库后需重新运行。


## 配置优先级与诊断

首次启动会把用户数据目录 `.env` 中的模型、地址与识别模式初始化到本机 `voice-config/profiles.sqlite3`。进程环境变量优先于 `.env`；初始化之后，运行时和 `doctor` 都以界面保存、当前选中的模型配置为准。修改 `.env` 的地址、模型或 `STREAMING_ASR` 不会覆盖已保存的配置，请在“设置 → 语音模型”中修改。语音运行时 `VOICE_RUNTIME` 仍从环境配置读取，修改后重启。

默认连接中的凭据引用环境变量；环境凭据在服务启动时读取，修改后重启。默认千问实时识别优先使用 `STREAMING_ASR_API_KEY`，整句识别使用 `OMNI_API_KEY`。在界面重新填写连接 Key 后，改用本机加密保存的 Key，供该连接的实时和整句识别共用；修改地址或厂商须重新填写凭据。

ASR 协议需要与服务端实际接口一致：

| 设置中的供应商 / 协议 | 请求接口 | 音频格式 | 识别方式 |
| --- | --- | --- | --- |
| 千问 | DashScope Chat Completions / 推理 WebSocket | 厂商专用格式 | 整句或实时 |
| OpenAI / OpenAI 兼容 | `/audio/transcriptions` | multipart WAV 文件 | 整句 |
| OpenAI Chat Audio / 兼容 ASR | `/chat/completions` | 裸 Base64 与 `format: wav`，不发送 DashScope 的 `asr_options` | 整句 |
| ElevenLabs | Scribe 实时接口 | 插件音频流 | 实时 |

“OpenAI 兼容”不保证服务支持上述所有接口。只有 Chat Completions 音频接口的服务请选择 Chat Audio，并填写支持音频输入的模型；该模型也可能按提示词生成额外文字，请先测试转写结果。

`batch` / 整句识别：麦克风仍持续采集、检测讲话，讲话结束后发送整段音频。`stream` / 实时识别：讲话时即发送音频。两种方式都能语音提问，`batch` 不代表关闭麦克风。`STREAMING_ASR=on/off` 只初始化默认配置，之后由选中 ASR 配置的识别方式决定。

千问实时连接有独立的完整 WS/WSS 地址，不再从自定义 HTTP 地址推导 WebSocket 路径。整句恢复使用显式的模型 ID 和 HTTP 地址；两项留空可禁用。恢复仍使用 DashScope ASR 协议，地址须与连接 HTTP 地址同源。HTTP-only 服务应使用整句模式及其支持的协议。已有配置会一次性补齐字段；自定义地址无法确定实时端点时，需要手动填写。

运行诊断：

```powershell
.venv/Scripts/python.exe lalo.py doctor
```

`capabilities` 表示本机配置和依赖检查是否满足使用条件；`readiness` 给出各项原因。`configured` 不等于网络、凭据或模型已通过验证，`connectionTested: false` 表示这份诊断没有请求模型服务，也不会产生模型调用费用。实际验证请使用“语音模型”中的识别测试、试听和模型连接测试。测试结果当场显示，不持久保存为就绪承诺。

页面的配置检查会列出缺少的 API Key、服务地址、CLI 命令和可选模型。没有凭据仍可阅读文档，使用对应功能时会明确提示缺什么。源码不包含生成的 `web/app.bundle.js`，必须先运行 `npm ci` 与 `npm run build`；缺少构建时启动会报错，直接访问服务也会显示构建步骤。

本地 Smart Turn 是可选的话轮结束检测模型，不是 Whisper ASR。未安装时使用静音分段，云端 ASR 不依赖本地 Whisper 权重。

`CLAUDE_COMMAND` 可以直接填写可执行文件路径（含空格也无需拆分），例如 `C:/Tools/Claude/claude.exe`。需要固定参数时使用 JSON 数组，例如 `["C:/Tools/Claude/claude.exe", "参数"]`；不会通过 Shell 拆分或执行命令字符串。选择 Claude 启动时会提前校验命令。

Claude Code 会在最终结果成功、完整讲解格式校验通过后开始播报；普通前言与工具中间候选不会作为讲解内容。支持最终 `structured_output`、完整的 `StructuredOutput` 工具结果，以及普通 JSON 返回。

## 局域网共同讲解（LiveKit Room）

同一局域网内，一台电脑运行 Lalo 并担任主持人，其他电脑通过邀请链接参加。参会人可阅读同一文档、独立浏览和恢复跟随、文字提问或申请后按住说话。主持人控制讲解、同步共同位置及结束会议；回答新问题后等待主持人继续。

先准备可用的 LiveKit 服务及浏览器可访问的 WSS 地址。Lalo 不会通过以下命令安装或启动 LiveKit。HTTPS 页面不能连接不安全的 WS 地址；LiveKit 的媒体端口也必须对局域网设备可达。会议采用内存状态，关闭服务会结束会议，旧邀请不能恢复。

在 `lalo doctor` 显示的 dataDirectory 下修改 `.env`（也可用 `--home` 指定专用数据目录）。沿用该目录已有的模型配置，并补充：

```dotenv
LALO_MEETING_PUBLIC_URL=https://192.168.1.10:8444
LALO_MEETING_TLS_CERT=C:/Lalo/certs/server.pem
LALO_MEETING_TLS_KEY=C:/Lalo/certs/server.key
LALO_MEETING_TLS_CA=C:/Lalo/certs/root-ca.pem
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_PUBLIC_URL=wss://192.168.1.10:8443/livekit
LIVEKIT_API_KEY=替换为自己的应用标识
LIVEKIT_API_SECRET=替换为自己的密钥
```

地址和路径都是示例，需要替换。`LIVEKIT_URL` 是 Lalo 后端使用的信令地址，`LIVEKIT_PUBLIC_URL` 是参会浏览器使用的信令地址；它们应指向同一个 LiveKit 服务。API Key 和 Secret 仅供服务端使用。

证书需包含主机局域网地址以及 `127.0.0.1`，并被两台电脑的浏览器信任。使用私有 CA 时，`LALO_MEETING_TLS_CA` 指定其根证书，供本机 CLI 验证 HTTPS；使用系统已信任的证书可留空。浏览器的信任需要单独配置一次。仅把证书路径填进 `.env` 不会自动安装信任，也不需要每次测试重新安装证书。

启动前停止同一数据目录下的普通服务，然后在一个终端运行：

```powershell
.venv/Scripts/python.exe lalo.py stop
.venv/Scripts/python.exe lalo.py serve --meeting
```

保留这个终端，在另一个终端打开方案：

```powershell
.venv/Scripts/python.exe lalo.py open --document C:/Lalo/proposal.md --mode cloud
```

也可使用下方多人会议 Skill 或 `open --mode codex/claude --session ...` 绑定源会话。`open` 会复用正在运行的 HTTPS 服务。若使用 `--home`，启动、打开与停止时须指定同一目录。

主持人在正式阅读器点击“开始会议”，把邀请链接发给参会人。主持人入会后申请麦克风权限并持续监听：直接说话，停顿后自动识别和回答，AI 讲解／回答期间疑似插话先短暂降低音量，识别确认是有效提问后才打断当前播报；底部按钮只用于“静音麦克风／开启麦克风”，不再逐句提交。权限被拒绝时需允许麦克风后点击开启；断线后为避免误收音，需要手动重新开启。参会人仍先申请发言，获准后按住说话、松开提交；排队和识别期间可取消自己的申请，切换窗口会提交其当前录音。参会人已获得语音发言权或正在识别时，主持人的自动提问暂不抢占该发言。参会人可独立开启／静音播放，没有麦克风也能文字提问和收听。停顿检测复用当前 VAD（约 0.7 秒静音），不是语义完成保证。低频轰鸣经过输入过滤；“嗯、啊、哦”等附和通常不打断，AI 最后一句为问句时保留简短回答。“停、暂停一下、等一下”识别后直接暂停。疑似插话降音量最多持续 1.5 秒，识别失败或误触发会恢复原音量，不重新生成讲解；实体外放环境的回声抑制仍需继续优化。


主持人可先浏览到目标段落，再点击“同步当前位置”；该操作暂停讲解并更新共同位置。其他人的独立浏览不会被强制打断。主持人刷新或连接恢复后需要手动继续，刷新保留身份仅限同一浏览器标签页；清空存储或关闭服务后不能据此恢复。

本模式显式监听局域网，默认单人模式仍只监听本机。允许参会设备访问 Lalo 的 HTTPS 端口、LiveKit 的 WSS 和媒体端口即可；不要将本机配置接口转发到公网。当前不提供真实桌面共享、公网部署或外放自然插话的完整验收保证。
### 多人会议 Skill

完成源码安装和前端构建后，安装用户入口：

```powershell
.\install-skill.ps1 -HostName both -SkillName lalo-meeting
```

刷新 Codex 或 Claude Code 后，使用 `$lalo-meeting` 或 `/lalo-meeting`，例如“用这个方案开一个多人讲解会议”。它会检查并复用会议服务、打开主持人阅读器并准备参会邀请；首次使用仍需配置 LiveKit 和 HTTPS。`-SkillName both` 可同时安装单人讲解与多人会议两个 Skill。

会议中的参会人可以看到共享文档、听到讲解与其他人的发言；请只邀请允许访问该文档的人。主持人启动链接与参会邀请不同，只分享界面生成的邀请链接。结束会议后邀请失效；关闭服务会结束所有内存中的会议。

## 数据与许可

配置、加密凭据和本地使用记录保存在用户数据目录：Windows 默认为 `%LOCALAPPDATA%/LaloLite/`，macOS 为 `~/Library/Application Support/LaloLite/`，Linux 为 `~/.local/share/lalo-lite/`。可通过 `LALO_HOME` 更改位置。

仓库中的 `.env.example` 仅包含空凭据示例。请在界面或用户数据目录中填写自己的配置，不要提交 API Key、聊天记录、音频或用户数据目录。调用服务商时，必要的文档、问题和音频会发送给所选服务商，对应服务可能收费。

本项目采用 [Apache-2.0](LICENSE)。字体、话轮模型和依赖保留各自许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

# 灵动脚本 (Lingdong Script)

Electron + React + Python 桌面自动化流程编辑器。可视化编排屏幕点击、OCR识别、条件分支、循环等自动化操作。

## 快速导航

| 命令 | 用途 |
|---|---|
| `npm run dev` | 启动开发环境 (Vite + Electron)，renderer :5173，HTTP API :7880 |
| `npm run build` | 构建 renderer (Vite) + main (tsc) |
| `npx electron-builder --win portable` | 构建免安装便携版 |
| `npx electron . --dev` | 在打包后强制开发模式 (loadURL) |
| `npx electron . --minimized` | 启动后最小化到任务栏 |
| `python -m pytest tests/` | 运行 Python 测试 (~296 tests) |
| `python python/mcp/server.py` | 启动 Python MCP 服务器 (stdio JSON-RPC) |
| `npm run ai:mcp` | 启动 TypeScript MCP 服务器 (5 tools, stdio) |
| `python tools/dsl_to_flow.py < file.dsl` | DSL 转 FlowScript |
| `curl -X POST http://127.0.0.1:7880/api/ping` | 检查 HTTP API 桥接 |
| `python python/voice/server.py 8765` | 启动语音对话服务器 (WebSocket 指定端口) |

## 目录结构

```
├── src/
│   ├── main/              # Electron 主进程
│   │   ├── main.ts        #   - 引擎管理, IPC handlers, 窗口生命周期
│   │   ├── api.ts         #   - HTTP API 服务器 :7880 (55 端点 + /mcp)
│   │   ├── env.ts         #   - isDev / getBaseDir 环境配置
│   │   ├── state.ts       #   - 全局状态管理
│   │   └── preload.ts     #   - contextBridge (40+ API 暴露)
│   └── renderer/          # React 前端 (Vite)
│       ├── App.tsx        #   - 根组件, 布局, 快捷键, HTN 注册
│       ├── store/         #   - Zustand + immer 状态管理 (index.ts, executor.ts)
│       ├── types/         #   - FlowNode, FlowEdge, NodeParams, FlowScript
│       ├── components/
│       │   ├── Canvas/    #   - FlowCanvas, FlowNode, EdgeLayer, CanvasControls
│       │   ├── PropertyPanel/ # - 参数编辑器 (55 种节点 × params/*.tsx)
│       │   ├── NodeLibrary/   # - 节点库面板
│       │   └── common/   #   - TopBar, StatusBar, LogPanel, ToolCallPanel,
│       │                  #     TriggerBar, ParamPromptDialog, MacroPanel,
│       │                  #     ScriptBrowser, TemplateSelector, ScheduleDialog,
│       │                  #     VoiceConversationPanel
│       └── utils/         #   - nodeDefinitions, pseudoParser, featureRegistry,
│                           #     semanticMatcher, htnRegistry, textGenerator,
│                           #     voiceConversation
├── python/
│   ├── agent/             # AI Agent 监督执行
│   │   ├── __main__.py    #   IPC 入口 (stdin/stdout JSON 协议)
│   │   ├── agent.py       #   LingdongAgent 统一入口 + text_protocol 循环
│   │   ├── meta_actions.py    #   26 个元动作 + 注册表 + 提示词生成
│   │   ├── supervisor.py     #   监督执行层（窗口焦点/OCR/对话框/重试）
│   │   ├── tool_path_selector.py # 工具访问模式分类 + 路径选择器
│   │   ├── config.py       #   AgentConfig dataclass + resolve_auto()
│   │   ├── planner.py      #   Plan DAG 分解 + PlanExecutor
│   │   ├── langgraph_agent.py #   ⚠️ 已废弃，仅保留向后兼容
│   │   ├── experience_db.py   #   经验数据库 (SHA256 签名索引)
│   │   ├── visual_locator.py  #   语义目标→屏幕坐标定位器
│   │   ├── dialog_handler.py  #   Windows 对话框检测/关闭 (win32 API)
│   │   └── menu_handler.py    #   Windows 菜单操作
│   ├── engine/            # Python 自动化引擎
│   │   ├── executor.py    #   - DSL 执行器 (核心)
│   │   ├── dsl_parser.py  #   - DSL 解析器 (190 关键字)
│   │   ├── dsl_flow_converter.py # - DSL↔FlowScript 双向转换
│   │   ├── engine.py      #   - stdin/stdout JSON 协议引擎入口
│   │   ├── condition_evaluator.py # - 条件求值
│   │   ├── crystallizer.py    # - 流程图→DSL 结晶化
│   │   ├── triggers.py        # - 触发器引擎
│   │   ├── scheduler.py       # - 定时调度
│   │   └── optimizer.py       # - 流程优化
│   ├── features/          # @feature 注册表 (246 功能, 227 MCP 工具, 43 模块)
│   │   ├── registry.py    #   - FeatureRegistry 单例, @feature 装饰器
│   │   ├── _all.py        #   - 导入所有 feature 模块 (43 模块, 246 @feature)
│   │   └── camera_features.py # - 摄像头捕获/录制功能
│   ├── voice/             # 语音对话服务器
│   │   ├── server.py      #   - WebSocket 语音服务器 (动态端口)
│   │   ├── download_models.py  # - 批量下载 STT 模型
│   │   └── test_stt.py    #   - STT 诊断脚本
│   ├── mcp/server.py      # Python MCP 服务器 (stdio JSON-RPC 2.0)
│   ├── macros/            # 宏录制保存目录 (.json 事件文件)
│   ├── interaction/       # 交互工具 (wheel_selector, region_picker, actions)
│   ├── perception/        # OCR, 图像匹配, 视觉融合
│   └── experience/        # 视觉定位经验库 (db.json + templates/*.png)
├── tools/                 # 工具脚本 (dsl_to_flow.py 等)
├── templates/             # 工作流模板 (.flow / .dsl 文件)
└── tests/                 # Python 测试套件 (~296 tests)
```

## HTTP API 桥接 (Claude Code 远程控制)

Electron 主进程内置 HTTP 服务器 `http://127.0.0.1:7880`（仅 localhost，可被 `API_PORT` 环境变量覆盖），Claude Code 可通过 curl 实时控制前端。

| 端点 | 用途 | 关键参数 |
|---|---|---|
| `POST /api/ping` | 健康检查 | — |
| `POST /api/load-script` | 加载完整流程图 | `{ script: FlowScript }` |
| `POST /api/run-dsl` | DSL 文本→流程图 | `{ dsl: string }` |
| `POST /api/add-node` | 添加节点 | `{ type, x, y, params? }` |
| `POST /api/update-node` | 更新节点属性 | `{ id, updates }` |
| `POST /api/update-node-params` | 更新节点参数 | `{ id, params }` |
| `POST /api/delete-nodes` | 删除节点 | `{ ids: string[] }` |
| `POST /api/add-edge` | 添加连线 | `{ from_node, to_node, from_port?, to_port? }` |
| `POST /api/delete-edges` | 删除连线 | `{ ids: string[] }` |
| `POST /api/auto-layout` | 自动排列节点 | — |
| `POST /api/generate-dsl` | 自然语言→DSL（调用LLM） | `{ description, apiKey? }` |
| `POST /api/create-flow` | DSL/描述→流程图（含参数检查） | `{ description?, dsl?, apiKey? }` |
| `POST /api/check-params` | 检查节点缺失参数 | `{ nodes[] }` |
| `POST /api/new-script` | 新建空白脚本 | — |
| `POST /api/set-view-mode` | 切换视图 | `{ mode: 'canvas'\|'pseudocode'\|'settings' }` |
| `POST /api/fit-screen` | 自适应缩放 | — |
| `POST /api/get-state` | 读取当前状态 | — |
| `POST /api/engine/start` | 启动脚本执行引擎 | `{ script }` |
| `POST /api/engine/pause` | 暂停引擎 | — |
| `POST /api/engine/resume` | 继续引擎 | — |
| `POST /api/macro/start-recording` | 开始宏录制（F6 热键） | `{ name?, include_mouse?, include_keys? }` |
| `POST /api/macro/stop-recording` | 停止录制并保存 | — |
| `POST /api/macro/list` | 列出已录制宏 | — |
| `POST /api/macro/play` | 播放宏 | `{ name }` |
| `POST /api/macro/delete` | 删除宏 | `{ name }` |
| `POST /api/macro/get-events` | 读取宏原始事件 | `{ name }` |
| `POST /api/macro/recording-status` | 录制器状态 | — |
| `POST /api/ai/generate` | 调用 LLM（DeepSeek） | `{ apiKey, messages[], apiBase?, model? }` |
| `POST /api/screenshot/capture` | 全屏截图（交互框选） | — |
| `POST /api/screenshot/select-region` | 框选屏幕区域 | — |
| `POST /api/ocr/region` | OCR 识别区域文字 | `{ x, y, w, h }` |
| `POST /api/wheel-select` | 滚轮选择器 | `{ options[], values?, title?, message? }` |
| `POST /api/undo` | 撤销 | — |
| `POST /api/redo` | 重做 | — |
| `POST /api/save-script` | 保存脚本 | — |
| `POST /api/set-script-name` | 重命名脚本 | `{ name }` |
| `POST /api/save-template` | 保存为模板 | `{ name, description? }` |
| `POST /api/list-templates` | 列出模板 | — |
| `POST /api/set-ui` | 更新 UI 状态（含 show_ai_dialog） | `{ updates: { dark_mode?, show_grid?, show_ai_dialog?, ... } }` |
| `POST /api/command` | 批量执行命令 | `{ commands: [{ action, payload?, delay? }] }` |
| `POST /api/copy-selected` | 复制选中节点 | — |
| `POST /api/cut-selected` | 剪切选中节点 | — |
| `POST /api/paste` | 粘贴 | — |
| `POST /api/select-all` | 全选节点 | — |
| `POST /api/clear-selection` | 清空选择 | — |
| `POST /api/duplicate-nodes` | 复制节点 | `{ ids: string[] }` |
| `POST /api/agent-start` | 启动 AI Agent 监督执行 | `{ goal, config?, supervise? }` |
| `POST /api/features/list` | 列出所有可用 @feature 工具 | — |
| `POST /api/features/reload` | 重新加载 @feature 注册表 | — |
| `POST /api/features/call` | 调用任意 @feature 工具 | `{ name, params }` |
| `POST /api/search-templates` | 语义搜索模板 | `{ query, maxResults? }` |
| `POST /api/marks/save` | 保存指令标记 | `{ marks: Record<string, {visible?,pinned?}> }` |
| `POST /api/marks/load` | 加载指令标记 | — |
| `POST /api/execute-js` | 在前端执行 JS 代码 | `{ code }` |
| `POST /mcp` | MCP over HTTP 端点 | 标准 MCP JSON-RPC |

## MCP 工具

提供 **两个 MCP 服务器**：

### 1. Python MCP (主)

启动: `python python/mcp/server.py`

通过 `@feature` 注册表动态生成 **227 MCP 工具**（过滤 16 hidden + 3 deprecated），覆盖：

| 分类 | 工具数 | 功能 |
|---|---|---|
| 交互动作 | 68 | click, type, swipe, hotkey, scroll, browser, filedialog, menu... |
| 感知识别 | 29 | find_image, OCR, color_detect, progress, A11y, fusion, camera... |
| 控制流 | 25 | wait, loop, condition, timer, software control, aggregate... |
| AI 规划 | 9 | plan, execute_plan, debug, VLM analyze, screen_understanding... |
| 宏指令 | 10 | record, playback, list, delete, convert_to_flow, visual_macro... |
| 系统功能 | 74 | window, process, clipboard, file, wifi, volume, display, scheduler, vault... |
| 调试诊断 | 32 | test, validate, system_info, feature introspection, healing, trigger... |

DSL 关键字: 190 个（自动从 @feature 注册表生成，含所有控制流关键字）
Agent 工具集: 26 元动作（高层语义） + 227 @feature 工具（底层能力），LLM 提示词中同时暴露
Agent 双路径执行: 元动作 → `meta_execute()`（参数校验 + 类型强制 + 直接调用），其他 → `supervisor.execute()`（完整监督流水线）
Agent 自我升级: 工具集从 @feature 注册表动态加载，新增功能自动纳入 Agent 可用范围（重启 Agent 生效）

关键工具示例：
- `dsl_to_flow(dsl: string)` — DSL 文本转 FlowScript JSON
- `flow_to_dsl(script: object)` — 流程图转 DSL 文本
- `run_dsl(dsl: string)` — 本地执行 DSL（免 token, 免后端）
- `create_flow_from_dsl(dsl, name)` — DSL → 保存 .flow → 返回 JSON

### 2. TypeScript MCP (桥接)

启动: `npm run ai:mcp` (`node dist/ai-interface/index.js --mode mcp`)

5 个工具用于前端集成和文件管理：

| 工具 | 用途 |
|---|---|
| `run_flow` | 执行 .flow 文件 |
| `list_flows` | 列出模板 |
| `create_flow` | 创建流程 |
| `ask_ai` | AI 问答 |
| `screen_capture` | 截屏 |

## 宏录制功能

Lobster 内置商业级宏录制，支持 F6 全局热键启停、鼠标智能过滤（<10px 跳过）、一键转换为流程图。

### 录制方式

| 方式 | 操作 |
|---|---|
| **F6 热键** | 按 F6 开始/停止录制（独立进程，不阻塞 UI） |
| **TopBar 按钮** | 点击录制按钮（红点动画），再点或按 F6 停止 |
| **MCP 工具** | `lobster_macro_start_recording` / `lobster_macro_stop_recording` |
| **HTTP API** | `POST /api/macro/start-recording` / `POST /api/macro/stop-recording` |

### MacroPanel 管理面板

- 录制后自动弹出宏列表
- 每个宏显示：事件数、时长、创建时间
- 操作按钮：播放、删除、**转换为流程图**
- 转换后生成 CLICK / INPUT_TEXT / KEYBOARD / WAIT 节点

### @feature MCP 工具

| 工具名 | 用途 |
|---|---|
| `lobster_macro_start_recording` | 启动独立录制器（F6 热键，非阻塞） |
| `lobster_macro_stop_recording` | 停止录制，返回事件统计和文件路径 |
| `lobster_macro_list` | 列出所有已保存宏 |
| `lobster_macro_playback` | 回放宏（支持 speed/loop 参数） |
| `lobster_macro_delete` | 删除宏文件 |
| `lobster_macro_get_events` | 读取原始事件数据 |
| `lobster_macro_convert_to_flow` | 宏事件 → FlowScript 流程图 |
| `lobster_macro_record` | **（已废弃）** 旧版阻塞式录制 |

### 录制文件格式

保存在 `python/macros/{name}.json`，格式：
```json
{
  "name": "登录流程",
  "events": [
    {"type": "click", "time": 0.5, "x": 100, "y": 200, "button": "Button.left", "pressed": true},
    {"type": "key",   "time": 1.2, "key": "hello", "pressed": true},
    {"type": "key",   "time": 1.8, "key": "Key.enter", "pressed": true}
  ],
  "duration": 2.5,
  "created": "2026-05-04T20:30:00",
  "include_mouse": true,
  "include_keys": true
}
```

### 视觉宏录制 (Visual Macro Recording)

自动从录制操作中提取按钮小图，用于后续离线 OpenCV 模板匹配定位，无需视觉模型。

**设计思路:**

普通宏录制只记坐标，界面变化后坐标无效。视觉宏录制在每次点击时截全屏，录制结束后从截图中智能检测按钮边界，裁剪小图存入经验库，后续通过 OpenCV 模板匹配定位，与界面布局无关。

**三阶段流程:**

| 阶段 | 操作 | 耗时 | 说明 |
|---|---|---|---|
| 录制 | `macro_start_visual_recording` | ~10ms/click | pynput 监听 + PIL/mss 截全屏存缓存目录 |
| 停止 | `macro_stop_visual_recording` | 即时 | 保存事件 JSON（含截图文件引用路径） |
| 提取 | `macro_extract_visual_templates` | ~200ms/click | **手动触发**，遍历 click → 智能裁图 → OCR → 存经验库 |

**按钮边界检测策略**（`_smart_crop_around_click`，三层递进）:

1. **floodFill 连通域**（容差 15→30→50）— 从点击点做种子填充，取连通域的 bounding rect
2. **Canny + findContours 兜底** — 边缘检测，找包含点击点的最小轮廓
3. **固定 40×40 裁剪兜底** — 前两者均失败时以点击点居中裁剪

**OCR 自动命名:**

裁剪后调用 EasyOCR 识别小图中的文字，用识别结果作为 `target_text` 存入经验库。以后直接 `visual_locator.locate("搜索", "click", ...)` 就能匹配到，完全离线。

**与普通宏录制的区别:**

| | 普通宏 | 视觉宏 |
|---|---|---|
| 录制数据 | 坐标+时间 | 坐标+时间+截图 |
| 回放方式 | 定点点击 | OpenCV 模板匹配定位 |
| 抗界面变化 | 否 | 是（按钮位置变了也能找到） |
| 存储 | `macros/*.json` | `macros/*.json` + `experience/templates/*.png` |
| VLM 依赖 | 无 | 无（OCR 可离线） |

**关键文件:**
- `python/features/visual_macro_features.py` — 3 个 @feature 工具（录制/停止/提取）
- `python/agent/visual_locator.py` — 定位器，查经验库 + OpenCV 匹配
- `python/agent/experience_db.py` — 经验数据库，JSON 索引 + PNG 模板
- `python/experience/templates/` — 裁剪小图存储目录

## 语音对话 (Voice Conversation)

内置语音对话功能，支持语音输入 → STT 识别 → LLM 处理 → TTS 朗读的完整流程。

### 架构

```
麦克风 → WebSocket → voice/server.py → Whisper/Paraformer STT
                                              ↓
Edge TTS / ElevenLabs ← LLM (DeepSeek) ← 识别文本
```

### STT 引擎

| 引擎 | 大小 | 准确率 | 备注 |
|------|------|--------|------|
| whisper-tiny | ~75MB | ❌ 中文欠佳 | 字节级 BPE 拼接问题 |
| whisper-base | ~139MB | ⚠️ 少量错误 | 基准模型 |
| **whisper-small** | ~461MB | ✅ 高 | **默认引擎** |
| whisper-large-v3-turbo | ~1.5GB | ✅ 最高 | 最佳质量，首次需下载 |
| paraformer | ~40MB | ✅ 高 | 阿里达摩院中文 ASR，ONNX 推理 |

支持一键切换引擎：设置面板选择 → 保存 → 语音面板自动重连。

### 语音对话面板

- `VoiceConversationPanel.tsx` — 浮动语音面板，显示对话记录、状态指示
- 支持连续对话模式（AI 回复后自动开始聆听）
- TTS 朗读支持 Edge TTS（免费）和 ElevenLabs（自定义音色）
- 状态栏显示当前引擎和连接状态

### 关键文件
- `python/voice/server.py` — WebSocket 语音服务器 (动态端口, 通过 `voice:server-url` IPC 获取)
- `python/voice/download_models.py` — 批量下载 openai-whisper / faster-whisper 模型
- `python/voice/test_stt.py` — STT 诊断脚本（测试麦克风、SAPI）
- `src/renderer/utils/voiceConversation.ts` — 前端语音客户端（641 行，状态机/音频调度/WebSocket 协议）
- `src/renderer/components/common/VoiceConversationPanel.tsx` — 语音面板 UI（321 行）

### WebSocket 协议

**客户端 -> 服务器:**
| type | 载荷 | 说明 |
|------|------|------|
| init 消息 | `{ apiKey, elevenlabsKey?, elevenlabsVoiceId?, sttEngine? }` | 连接后首个消息 |
| `start_recording` | `{}` | 开始麦克风录制 → STT → LLM → TTS 循环 |
| `transcript` | `{ text }` | 直接发送文字进入 LLM 管道 |
| `cancel` | `{}` | 取消当前对话轮次 |
| `redownload_model` | `{}` | 清除模型缓存并重新下载 |

**服务器 -> 客户端:**
| type | 载荷 | 说明 |
|------|------|------|
| `recording_start` | `{}` | 开始录制确认 |
| `stt_result` | `{ text }` | 语音识别结果（空串表示无语音） |
| `llm_start` | `{}` | LLM 开始处理 |
| `llm_text` | `{ text }` | LLM 文本回复 |
| `switch_engine` | `{ engine }` | STT 引擎已切换 |
| `model_redownloaded` | `{}` | 模型重下载成功 |
| `model_redownload_error` | `{ message }` | 模型重下载失败 |
| `tts_end` | `{}` | TTS 播放结束 |
| `error` | `{ message }` | 错误 |

**音频流:** TTS 音频块以原始二进制 ArrayBuffer 形式发送，前端通过 `AudioContext.decodeAudioData()` 解码并用精确调度消除间隙。支持 Edge TTS（默认）和 ElevenLabs（需 API Key）。

### 对话状态机
`VoiceState = 'idle' | 'listening' | 'thinking' | 'speaking'`
连续模式：说话 → 空闲 → 自动收听 → VAD 检测 → STT → LLM → TTS → 循环。VAD 基于 RMS 能量阈值，自适应环境噪声。

### 快速测试
```bash
# 启动语音服务器
python python/voice/server.py 8765

# 测试 STT 引擎识别质量
python -c "
import whisper, soundfile as sf
data, sr = sf.read('test_speech_16k_pcm.wav')
model = whisper.load_model('small')
result = model.transcribe(data.astype('float32'), language='zh')
print(result['text'].strip())
"
```

## 摄像头节点

支持摄像头画面捕获、录像和运动检测，作为自动化流程中的视觉输入源。

| 节点类型 | 用途 | 主要参数 |
|---------|------|---------|
| camera_capture | 单帧拍照 | camera_id, output_path, format(png/jpg), resolution |
| camera_record | 视频录制 | camera_id, output_path, fps, duration, resolution |

### @feature 工具（5 个）

| 工具名 | 用途 |
|--------|------|
| `camera_list` | 列出所有可用摄像头设备（索引 0-9） |
| `camera_capture` | 捕获一帧画面，支持保存文件或 base64 返回 |
| `camera_record_start` | 开始录制 AVI/MP4，支持限时录制 |
| `camera_record_stop` | 停止正在进行的录制，返回文件大小和时长 |
| `camera_detect_motion` | 帧差法运动检测，支持保存触发画面 |

- `python/features/camera_features.py` — @feature 注册的摄像头功能（5 个）

## 工作流

### 创建自动化流程
1. 在画布拖拽节点编排流程
2. 节点类型: 55 种（点击、输入、截图、OCR、条件、循环、等待、系统操作、摄像头等）
3. 属性面板编辑参数 (支持区域拾取、策略选择)
4. 文本视图可切换中文伪代码 / 英文 DSL 模式
5. 保存为 .flow 文件, 或导出 DSL

### 调试执行
1. 点击 "运行" 或按 **F5** 启动（前端快捷键）
2. 引擎逐节点执行, 高亮当前节点
3. 日志面板显示执行详情
4. 支持暂停/继续/停止
5. 节点标注成功/失败/跳过状态

**全局 F9 热键**: 即使在应用未聚焦时，F9 也可启停 Python 引擎（主进程全局快捷键）。

### AI 辅助
1. 点击 "AI 助手" 用自然语言描述需求
2. AI 生成 DSL → 自动转为流程图
3. 或通过 Claude Code 调用 MCP 工具生成
4. 模板文件在 `templates/` 目录

### AI Agent 监督执行
1. 点击 "AI Agent" 输入目标（自然语言）
2. Agent 自动调用 LLM 生成流程图脚本（可视化展示）
3. Agent 进入监督执行模式：逐步骤执行，每步包含：
   - **窗口焦点安全** — 自动检测并聚焦目标窗口
   - **执行动作** — 通过 feature registry 执行点击/输入/快捷键等
   - **屏幕稳定等待** — 等待操作后的界面变化完成
   - **OCR 验证** — 自动识别屏幕文字验证操作结果
   - **失败恢复** — 自动重试 / 替换方案 / 跳过 / 终止
4. 前端流程图实时高亮当前执行节点，显示成功/失败状态
5. 执行完成或失败后汇总报告

## Agent IPC 协议

AI Agent 子进程通过 stdin/stdout JSON 与 Electron 主进程通信。协议设计用于实时流式展示执行进度。

### IPC 命令（Electron → Agent，stdin）

| 命令 | 参数 | 用途 |
|---|---|---|
| `{"type": "start", "goal": "...", "config": {...}, "supervise": true}` | goal: 目标描述, config: api_key/api_base/model, supervise: 是否监督执行 | 启动 Agent |
| `{"type": "stop"}` | — | 请求优雅停止（当前步骤完成后退出） |
| `{"type": "guidance", "text": "..."}` | text: 指引文本 | 注入用户指引 |
| `{"type": "status"}` | — | 查询当前状态 |

### IPC 事件（Agent → Electron，stdout）

| 事件 | 关键字段 | 用途 |
|---|---|---|
| `{"type": "agent_start", "goal": "..."}` | goal | Agent 启动通知 |
| `{"type": "step_complete", "step_num": N, "tool": "...", "result": {...}}` | step_num, result.success, result.error | 每步执行结果（前端实时更新流程图） |
| `{"type": "agent_done", "status": "completed\|failed\|stopped", "total_steps": N}` | status, total_steps, fail_reason | Agent 结束 |
| `{"type": "log", "level": "info\|warn\|error", "message": "..."}` | level, message | 日志事件 |
| `{"type": "user_guidance_sent", "text": "..."}` | text | 用户指引确认 |
| `{"type": "error", "message": "..."}` | message | 错误事件 |

### 监督执行流程（supervise=true）

```
Agent 主循环 (__main__.py)
  ├─ Phase 1: generate_flow_script(goal)
  │   └─ 生成流程图脚本 → 前端展示
  ├─ Phase 2: parse_pseudocode(pseudocode)
  │   └─ 解析为结构化步骤 (click/type/hotkey/wait/launch...)
  └─ Phase 3: 逐步骤监督执行
      ├─ 窗口焦点安全 (get_active_window)
      ├─ 视觉定位 (visual_locator.locate → OpenCV/VLM)
      ├─ 执行动作 (registry.execute)
      ├─ 等待屏幕稳定 (wait_screen_stable)
      ├─ OCR 验证 (ocr_find_text / wait_screen_change)
      ├─ 失败恢复 (planner.decide_recovery)
      │   ├─ retry → 重试
      │   ├─ alternative → 替代方案
      │   ├─ skip → 跳过
      │   └─ fail → 终止
      └─ 发送 step_complete 事件 → 前端高亮节点
```

## 视觉定位 + 经验复用学习系统

Agent 监督执行时通过 **视觉定位** 模块将语义目标（如"搜索按钮"）转为屏幕坐标，并使用 **经验数据库** 实现"越用越快"。

### 架构

```
Agent 监督层 (langgraph_agent.py supervision wrapper)
  └─ step 0: _apply_visual_locator(name, args)
       ├─ 提取 target_text（如 "搜索按钮"）
       ├─ 截取全屏截图
       ├─ visual_locator.locate()
       │    ├─ ① ExperienceDB → 有模板?
       │    │    └─ OpenCV matchTemplate → 命中 → 返回坐标
       │    ├─ ② VLM 兜底 (多模态开启时)
       │    │    └─ VLM API 分析截图 → 返回坐标 → 裁剪小图 → 存入 ExperienceDB
       │    └─ ③ 均失败 → 返回错误，走正常执行流程
       └─ 更新 args.x / args.y → 监督执行使用坐标
```

### 三层设计

| 层 | 文件 | 功能 | 依赖 |
|---|---|---|---|
| **经验数据库** | `python/agent/experience_db.py` | JSON 存储经验条目，SHA256 签名（step_type\|target_text\|window_title），裁剪小图保存在 `experience/templates/` | 无外部依赖 |
| **视觉定位器** | `python/agent/visual_locator.py` | 统一入口 `locate()`：先查经验库→OpenCV 匹配，再 VLM 兜底 | OpenCV (`cv2`)，PIL (fallback) |
| **LangGraph 集成** | `python/agent/langgraph_agent.py` → `_apply_visual_locator()` | 在监督包装器 step 0 调用，自动注入坐标到工具参数 | `pyautogui` (截图) |

### 控制开关 (设置面板)

| 开关 | localStorage key | 效果 |
|---|---|---|
| **多模态 (视觉模型)** | `lingdong-llm-multimodal` | 开启后首次遇到新界面时调用 VLM API 分析截图定位元素。纯文本模型(如 DeepSeek)必须关闭 |
| **自动学习** | `lingdong-auto-learn` | 开启后每次执行成功自动记录屏幕状态，后续同目标可直接用 OpenCV 匹配，无需 API 调用 |

### 经验数据存储位置

```
python/experience/
├── db.json            # 经验索引（签名→路径/坐标/置信度）
└── templates/         # 裁剪小图（PNG）
```

### 生命周期

1. **首次执行**（自动学习 ON，多模态 ON）：
   1. Agent 需要点击"搜索按钮"
   2. 经验库无匹配 → VLM 发送截图+描述 → 返回坐标
   3. 执行点击 → 成功后裁剪按钮小图存入 `experience/templates/`
   4. 经验条目写入 `db.json`（含签名、坐标、置信度）

2. **再次执行同目标**（自动学习 ON，多模态 OFF）：
   1. Agent 需要点击"搜索按钮"
   2. 经验库命中 → 加载模板小图 → OpenCV `matchTemplate` 扫描屏幕
   3. 找到匹配位置 → 返回坐标 → 执行点击
   4. 整个过程无需调用任何 API，完全离线

3. **手动清除**：`python -c "from agent.experience_db import clear; clear()"`

### settings 面板已有开关

- `lingdong-llm-multimodal`: 多模态视觉开关（默认关闭，避免纯文本模型报错）
- `lingdong-auto-learn`: 自动学习开关（默认关闭）

两个开关同时开启才启用 VLM 兜底；仅 auto-learn 开启时只使用 ExperienceDB + OpenCV。

## Set-of-Mark (SoM) 标号系统

Agent 在执行时对屏幕元素进行 **蓝色框 + #N 编号** 标注（Set-of-Mark），MIMO 等纯文本模型直接用 `#3` 指定目标，无需输出模糊的文字坐标描述。

### 双轨 SoM 引擎

| 引擎 | 技术栈 | 定位精度 | 速度 | 状态 |
|---|---|---|---|---|
| **OmniParser** | YOLOv8 图标检测 + Florence-2 标题 + EasyOCR | 像素级 | ~3-8s (CPU) | 需 transformers 4.x |
| **UIA + OCR 兜底** | Windows UIA 控件树 + EasyOCR 文字检测 | 像素级 | ~1-3s | **默认工作路径** |

### 执行流程

```
_apply_som(b64_img)
  ├─ OmniParser 优先
  │    ├─ 启动 FastAPI 子进程 (lazy, 仅首次)
  │    ├─ POST /parse/ → 返回 {image_b64, label_map, count}
  │    └─ 失败 → 静默降级到 UIA+OCR
  └─ UIA + OCR 兜底 (always available)
       ├─ a11y_get_tree(depth=3) → UIA 控件列表
       ├─ ocr_extract_all() → OCR 文字框
       ├─ 去重合并 → 编号排序 (从上到下, 从左到右)
       └─ OpenCV 画蓝色框 + #N 编号
```

### 关键文件
- `python/agent/agent.py` — `_apply_som()` 核心方法，`_supervised_execute()` 中 `#N` 解析
- `python/agent/omniparser_client.py` — OmniParser FastAPI 客户端（子进程管理 + parse API）
- `omni_parser_repo/` — OmniParser V2 仓库（克隆自 microsoft/OmniParser-v2.0）
- `omni_parser_repo/weights/` — 下载的模型权重（icon_detect 40MB + icon_caption 1GB）

### MIMO text_protocol SoM 集成

```
run_text_protocol(goal)
  ├─ 系统提示词规则 8: "编号定位优先" 
  ├─ 每步截图 → _apply_som() → 标号图 + label_map
  ├─ 发送给 LLM: "屏幕元素编号... 使用 #N 指定目标"
  ├─ LLM 回复: target: #3
  └─ _supervised_execute → 解析 #3 → label_map[3] → (x, y, w, h)
```

### OmniParser 设置

```bash
# 克隆（已包含在 omni_parser_repo/）
git clone https://github.com/microsoft/OmniParser.git omni_parser_repo

# 下载权重
python -c "
import os; os.environ['HTTP_PROXY']='http://127.0.0.1:7897'
from huggingface_hub import snapshot_download
snapshot_download('microsoft/OmniParser-v2.0', local_dir='omni_parser_repo/weights')
"

# 注意: 需要 transformers < 5.0（当前 Python 3.14 + transformers 5.x 不兼容,
# 降级方案: pip install 'transformers<5.0'）
```

## 关键约定

- **新增节点类型**: `types/index.ts` + `nodeDefinitions.ts` + `params/*.tsx` + `FlowNode.tsx` (getNodeSummary) + `PropertyPanel.tsx` (switch case)
- **新增 MCP 工具**: 在 `python/features/` 用 `@feature` 装饰器声明，自动注册 MCP/CLI/HTTP/DSL
- **新增 Agent 能力**: 在 `python/agent/` 添加，`__main__.py` 驱动执行循环，`langgraph_agent.py` 监督包装
- **新增 API 端点**: 在 `src/main/api.ts` 的 `switch (url.pathname)` 中添加 case
- **状态管理**: Zustand + immer，`useStore` hook
- **Electron IPC**: `preload.ts` 暴露 → `main.ts` 处理 → Python 子进程
- **DSL 格式**: 大写关键字 `CLICK 100 200` / `LOOP 5 ... END` / `IF cond ... ELSE ... END`
- **Agent IPC 协议**: `python/agent/__main__.py` 通过 stdin/stdout JSON 与 Electron 通信（start/stop/guidance/status 命令，agent_start/step_complete/agent_done/log 事件）
- **Python 协议**: `engine/engine.py` 通过 stdin/stdout JSON 与 Electron 通信
- **测试**: `pytest tests/` ~296 tests (Python, 覆盖全部 @feature 执行, DSL 转换, 往返验证), 暂无前端测试脚本
- **中文编码**: 所有 Python 子进程通过 `spawnPython()` 强制 `PYTHONIOENCODING=utf-8`
- **快捷键**:
  - **F5**: 前端画布"运行/停止"执行
  - **F6**: 全局宏录制启停
  - **F9**: 全局系统级 Python 引擎启停（即使应用未聚焦）
  - **Ctrl+Z/Y**: 撤销/重做
  - **Ctrl+C/X/V**: 复制/剪切/粘贴节点
  - **Delete/Backspace**: 删除选中节点/连线
  - **Tab**: 切换面板显示
- **生产模式**: `app.isPackaged` 控制 `loadURL` (dev) 或 `loadFile` (prod)。`--dev` 标志可在打包后强制开发模式。无 `--prod` 标志。
- **对话框处理**: `agent/dialog_handler.py` 纯 win32 API 检测/关闭对话框。现代 IFileDialog (Windows 8+) 无法通过自动化保存（DirectUI 渲染），Agent 应使用 `file_write` 程序化保存文件而非触发 UI 对话框
- **视觉定位系统**: `agent/visual_locator.py` + `agent/experience_db.py`。语义目标→屏幕坐标的两步策略：经验库→OpenCV 匹配，VLM 兜底。`_apply_visual_locator()` 在 `langgraph_agent.py` 监督层 step 0 自动调用。所有 VLM 定位成功的坐标和按钮小图自动存入经验数据库供后续离线复用

## 节点类型速查 (55 种)

### 基础交互
| 类型 | 用途 | 主要参数 |
|---|---|---|
| click | 点击坐标 | x, y, method, offsetX, offsetY, delay_after |
| input_text | 输入文字 | text, click_before, clear_before, delay_after |
| keyboard | 快捷键 | keys[], modifiers[], delay_after |
| swipe | 滑动 | start_x, start_y, end_x, end_y, duration, delay_after |
| mouse_operation | 鼠标综合（点击/滚轮/悬停/移动） | sub_operation, x, y, method |
| click_action | 点击综合（坐标/图片/文字） | mode, method, delay_after |

### 图像与识别
| 类型 | 用途 | 主要参数 |
|---|---|---|
| image_click | 图片识别点击 | targets[], similarity, method, timeout, multi_scale, match_all |
| click_text | OCR 文字点击 | text, similarity, method, timeout |
| screenshot | 截图 | region, save_path, format |
| popup_close | 弹窗关闭 | strategies[], timeout, delay_after |
| color_check | 颜色检测 | x, y, w, h, expected_color, tolerance |
| camera_capture | 摄像头拍照 | camera_id, output_path, format |
| camera_record | 摄像头录像 | camera_id, output_path, fps, duration |

### 控制流
| 类型 | 用途 | 主要参数 |
|---|---|---|
| condition | 条件分支 | condition_type (image_exists/window_exists/variable_equals/color_match/scene_match/always_true), similarity |
| loop | 循环 | loop_type (count/condition/infinite), count |
| wait | 等待 | wait_type (time/image/window), duration, timeout |
| sub_script | 子流程 | script_id, script_name |
| scene_condition | 场景识别 | scene_name, similarity |

### 设备监控
| 类型 | 用途 | 主要参数 |
|---|---|---|
| progress_wait | 等待进度条 | wait_type, region, timeout, poll_interval |
| region_wait | 等待区域稳定 | region_type, x, y, w, h, timeout, poll_interval |

### 变量与数据
| 类型 | 用途 | 主要参数 |
|---|---|---|
| variable_set | 设置变量 | name, value |
| variable_get | 读取变量 | name, value |

### 系统操作 (独立类型)
| 类型 | 用途 | 主要参数 |
|---|---|---|
| shutdown | 关机 | delay_seconds, confirm |
| reboot | 重启 | delay_seconds, confirm |
| logoff | 注销 | confirm |
| hibernate | 休眠 | delay_seconds, confirm |
| lock_workstation | 锁定工作站 | confirm |
| volume_set | 设置音量 | level |
| volume_mute | 静音控制 | mute (true/false/toggle) |
| launch_program | 启动程序 | target, args, wait |
| kill_process | 终止进程 | name, force |
| wait_process | 等待进程 | name, wait_for (start/exit), timeout |
| focus_window | 激活窗口 | title, exact |
| set_wallpaper | 设置壁纸 | path, style |
| display_off | 关闭显示器 | confirm |
| system_sleep | 睡眠 | confirm |
| show_desktop | 显示桌面 | — |
| battery_status | 电池状态 | — |
| empty_recycle_bin | 清空回收站 | confirm |
| display_brightness | 亮度 | level |
| clipboard_set | 剪贴板写入 | text |
| clipboard_get | 剪贴板读取 | — |
| window_control | 窗口操作 | operation, title, width, height |

### 高级工具
| 类型 | 用途 | 主要参数 |
|---|---|---|
| web_browser | 浏览器操作 (Playwright) | action, url |
| dialog_action | 对话框操作 | action, timeout, button |
| a11y_action | 无障碍操作 (UIA) | action, name, role |
| wifi_action | WiFi 操作 | action, ssid, password |
| tool_call | 任意 @feature 工具 | feature_name, params |

### 聚合类型（多合一）
| 类型 | 包含子操作 | 用途 |
|---|---|---|
| system_power | shutdown/reboot/logoff/hibernate/lock_workstation/system_sleep/display_off | 系统电源统一入口 |
| audio_action | volume_set/volume_mute | 音频控制统一入口 |
| desktop_action | show_desktop/set_wallpaper/empty_recycle_bin | 桌面操作统一入口 |
| clipboard_action | set/get/get_image/get_files/clear | 剪贴板操作统一入口 |
| process_action | launch/kill/wait_start/wait_exit/focus | 进程操作统一入口 |

### 特殊节点
| 类型 | 用途 |
|---|---|
| start | 脚本入口点 |
| end | 脚本终止点 |

## 常见任务

```
# 运行测试
python -m pytest tests/ -x -q

# DSL → 流程图 往返验证
echo "CLICK 100 200" | python tools/dsl_to_flow.py

# 本地 DSL 执行（无需 Electron）
python -c "from engine.executor import DSLExecutor; e=DSLExecutor(None); e.run_dsl_sync('CLICK 100 200')"

# 查看 MCP 工具列表
python -c "from features._all import *; from features.registry import registry; print(registry.export_claude_context())"

# 启动滚轮选择器（独立测试）
python python/interaction/wheel_selector.py

# 通过 HTTP API 控制前端 (端口 7880)
curl -X POST http://127.0.0.1:7880/api/ping

# DSL → 流程图 → 加载到前端（一键）
curl -X POST http://127.0.0.1:7880/api/run-dsl -H "Content-Type: application/json" -d '{"dsl":"CLICK 500 400\nWAIT 2\nSCREENSHOT"}'

# 框选屏幕区域
curl -X POST http://127.0.0.1:7880/api/screenshot/select-region

# 启动 AI Agent
curl -X POST http://127.0.0.1:7880/api/agent-start -H "Content-Type: application/json" -d '{"goal":"打开记事本并输入文字","supervise":true}'
```

## 构建与部署

```bash
# 完整构建
npm run build                    # 构建 renderer (Vite) + main (tsc)

# 构建便携版
npx electron-builder --win portable   # 输出到 release/灵动脚本-*-portable.exe

# 测试生产模式
npx electron . --dev             # 打包后强制开发模式 (loadURL)
npx electron . --minimized       # 启动后最小化到任务栏
# 无 --prod 标志。打包后默认 loadFile；未打包时默认 loadURL。
# 使用 --dev 在打包后强制 loadURL。

# 发布到 GitHub Release
gh release create v1.0.1 "release/灵动脚本-1.0.0-portable.exe" --generate-notes
```

### 生产模式注意事项

- **CSP**：生产模式下从 `file://` 加载，已移除 CSP 头防止阻塞模块脚本
- **`isDev` 判断**: `!app.isPackaged || process.argv.includes('--dev')` — 打包后默认生产模式，加 `--dev` 恢复开发模式
- **`--minimized` 标志**：默认窗口正常显示，加此标志则启动后最小化
- **`getBaseDir()`**：所有 Python 子进程的 `cwd` 通过此函数统一解析，开发时指向项目根目录，生产时指向 `process.resourcesPath`
- **`extraResources`**：`python/`、`tools/` 目录通过 `extraResources` 复制到 asar 外部，Python 子进程可直接访问
- **Python 路径**：生产模式下 Python 脚本位于 `resources/python/`，通过 `getEngineScriptPath()` 统一解析
- **API 端口**: 默认 `7880`，可通过 `API_PORT` 环境变量覆盖

# 模型 Provider、视觉路由、Thinking、缓存与成本

本文只描述当前代码和官方文档能够证明的事实。模型能力与调用口径核对日期为
2026-07-31；如果上游模型能力发生变化，应同时更新
`python/winpilot_home/winpilot/model_adapter.py`、对应测试和本文，不能只改模型名称或提示词。

## 当前调用路径

当前产品 GUI 主线不是 Legacy `AgentRunner`。它由
`vnext/model_roles/visual_actor.py` 的单一 `LiveVisualActor` 通过
`modeling/ModelCallRuntime` 调用模型；默认仍是 OpenAI-compatible Chat
Completions，只有配置显式给出 `api.protocol` 或 `api.api_style` 时才会改走 OpenAI
Responses、Anthropic Messages 或 Gemini `generateContent`。Legacy
`AgentRunner`、编程/仓库 profile 和较大工具历史仍为兼容路径，不能反向决定
vNext 行为。

共享的 provider 代码仍保持薄适配：

- `ModelProfile` 归一化 provider、模型名和原生视觉能力。
- `OpenAICompatibleAdapter` 保持
  `client.chat.completions.create(...)` 的调用形状。
- `ProviderProtocolAdapter` 把同一 canonical message/tool/image 边界转换为 Responses、
  Messages 或 `generateContent` wire；
- `modeling/ModelCallRuntime` 负责 deadline、attempt 记录和 usage；
- `request_extra_body()` 处理 MiMo / Reasonix 的 thinking 参数。
- `normalize_usage()`、`accumulate_usage()` 归一化 token、缓存和成本。

它不是代理服务，也不替代 GUI Controller。配置仍来自
`python/winpilot_home/winpilot/config.py` 和本机、被 Git 忽略的
`python/winpilot_home/config.json`。

## Provider 识别

代码内置能力/usage 元数据语义的 provider family 包括 `mimo`、`deepseek`、
`reasonix`、`anthropic`、`gemini`、`openai` 和 `openai_compatible`。
`api.provider=auto` 会按 `base_url` 和 `model` 推断这些 family，无法
识别时使用 `openai_compatible`。显式填写的其他自定义 provider 字符串会原样
保留，但不会自动获得上述 family 的 thinking、价格过滤或 usage 特判。

`api.provider` 只用于能力、usage 与价格等元数据，**不选择 wire**。模型名和
`base_url` 同样不选择 wire：聚合器可以用 OpenAI-compatible 接口承载 Claude 或
Gemini，自动改成原生协议会破坏兼容。原生 wire 只接受以下显式配置：

- `api.protocol` / `api.api_style = openai_responses | responses`；
- `api.protocol` / `api.api_style = anthropic | claude`；
- `api.protocol` / `api.api_style = gemini | google`；
- `chat | chat_completions | openai_chat | openai_compatible` 明确保留默认 Chat 路径。

原生 adapter 当前是 provisional：仅支持非流式文本、Base64 data-URL 图片、function
tools 与工具后续回合所需的原生 reasoning/signature replay sidecar；不支持音频、
视频、文件或 provider built-in tools。canonical `strict:true` 会保留到 Anthropic
tool schema，signed thinking / thought signature 也随对应 tool exchange 回放。相关
5 个测试文件为 `214 passed`，但尚无三家真实 credential smoke 或可见 Electron
provider E2E，不能据此声称“大部分模型 API 已端到端适配”。

这只是当前 vNext 模型调用主线的薄协议层，不是完整 provider registry。模型名称
相似、上下文更长或推理更强，都不能自动推出它支持图片。

## MiMo V2.5 的视觉路由

| 模型 | WinPilot 原生图片能力 | GUI 路由 |
| --- | --- | --- |
| `mimo-v2.5` | 是 | 原生视觉主路径，直接接收真实 GUI JPEG |
| `mimo-v2.5-pro` | 否 | text-only GUI 路径；使用 UIA/OCR/CV/非视觉摘要，必要时使用独立 VLM |
| 名称符合当前 Claude 3/4/5 family 且 provider 为 Anthropic | 是 | 仅在显式 native wire 或兼容端点确实接受图片时使用 |
| Gemini 1.5 或 2+ family 且 provider 为 Gemini | 是 | 仅在显式 native wire 或兼容端点确实接受图片时使用 |
| 名称明确含 `omni`、`-vl`、`vision` 或 `gpt-4o` 的模型 | 是 | 原生视觉主路径 |
| 其他未显式登记的模型 | 默认否 | 可用显式 `api.supports_vision` 覆盖，随后仍需真实 E2E |

这里的 Pro 路由是有意的 fail-closed 设计，不是按模型档次猜测。小米官方
[模型页](https://mimo.mi.com/docs/en-US/quick-start/model)把
`MiMo-V2.5` 描述为原生全模态模型；官方
[图像理解文档](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/multimodal-understanding/image-understanding)
给出的 OpenAI-compatible 示例使用 `mimo-v2.5`，并明确写明当前只有该模型
支持图像理解。文档支持图片 URL 和 Base64 data URL 输入，正好覆盖 WinPilot
注入本地截图的方式。官方页面没有把 `mimo-v2.5-pro` 列为图像理解模型，
所以当前代码绝不向 Pro 路径发送 `image_url`。

这意味着：

- 纯视觉、像素密集型 GUI 任务优先选 `mimo-v2.5`。
- `mimo-v2.5-pro` 可用于文本规划、代码或其他不依赖截图的能力，但不能作为
  当前 WinPilot 的截图主模型；前端会明确提示它将走较慢的感知回退路径。
- 上游将来若为 Pro 正式开放图片输入，需要先做真实接口探测、补回归测试，
  再更新 `model_supports_vision()`，不能仅凭名称放开。

原生视觉路径在初始回合和每笔视觉事务后注入新鲜画面。当前 live vNext 不向模型
暴露 Legacy `observe(image_region=...)`；画面可能是经过验证的前台目标窗口，也
可能是完整虚拟桌面，每帧都会声明范围和提交图片尺寸。
`controller.target_window_capture_enabled=true` 只允许在前台、单窗口且范围可信时
裁剪，含多窗口或全局语义的任务会退回全桌面。关闭该设置可强制全桌面截图。
真实画面是权威观测，UIA/OCR/规则只是补充，不能覆盖相反的像素事实。

text-only 路径不会收到不可见的截图字节。它必须依赖 UIA、Win32、OCR、CV、
`nonvisual_summary`、确定性检查或单独配置的 VLM；因此在画布、游戏和自绘控件等
场景中通常更慢，也更容易受结构化感知缺失影响。

## MiMo Thinking 策略

小米官方
[Deep Thinking 文档](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/deep-thinking)
说明 `mimo-v2.5` 和 `mimo-v2.5-pro` 默认开启 thinking，可通过
`thinking.type=enabled|disabled` 控制；thinking 会增加延迟，并与最终答案共享
`max_completion_tokens`。多轮工具调用在 thinking 模式下还必须完整回传历史
`reasoning_content`。

更关键的是，官方
[API Integration FAQ](https://mimo.mi.com/docs/en-US/quick-start/faq/api-integration)
指出：tool calling 开启 thinking 时，`tool_calls` 可能混入
`reasoning_content`，造成不稳定或不完整输出，并建议在调用工具时关闭 thinking。
这与 GUI 实测中的截断、伪工具文本和参数不完整问题一致。

当前可复现 vNext 基线明确使用：

- `api.thinking=false`；
- Visual Actor `temperature=0.3`；
- 每个决策最多一次 provider attempt；
- 最近一笔原生 tool exchange；若 provider 返回 `reasoning_content`，只随该交换
  原样回传一次。

在相同 corridor 上临时设置 `thinking=true` 仍失败且更慢，因此已经回退，没有把
`adaptive` reasoning 策略接入 vNext。配置和 Legacy `AgentRunner` 中仍存在
`controller.native_visual_thinking_mode`，它属于兼容债务，不是当前产品主线的
目标架构。未来模型需要不同 reasoning 参数时，应作为显式模型 profile 在同一
E2E 矩阵 A/B；不能根据任务词、应用名或失败类型自动切换。

vNext 请求仍受 runner 总截止时间约束，late response 在工具分发前丢弃；已经执行
的 GUI 动作不会因模型调用失败而重放。该边界也不会让
`mimo-v2.5-pro` 获得图片能力。

## 缓存

当前 vNext 有两层有界上下文：服务端 Thread 只交给模型最近成功且有用户结果的原生
文本交换，最多 12 条 message / 16 KiB；进入视觉 mission 后，Actor 只额外保留最近
一笔 provider 原生 tool exchange 与新鲜权威帧。它不维护无界 append-only 历史，也不
为缓存重复旧截图。

冻结 Legacy `agent/loop.py` 确有 cache-friendly stable prefix：32 KiB 前保持
append-only history，并把 session continuity 放在前缀中保护 DeepSeek prefix cache。
当前唯一活动 vNext 不走这个 owner；不得为了 cache 命中把它接回主线。已删除的
`cache_strategy` 只是从未生效的标签。vNext 不发送 OpenAI `prompt_cache_key`、
Anthropic `cache_control` 或 Gemini `cachedContent`，没有主动缓存 owner；任何 provider
也不承诺一定缓存。

`normalize_usage()` 当前识别：

- DeepSeek：`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`。
- OpenAI Chat：`prompt_tokens_details.cached_tokens`。
- OpenAI Responses 风格：`input_tokens_details.cached_tokens`。
- Anthropic-style 代理字段：`cache_read_input_tokens`、
  `cache_creation_input_tokens`。
- 推理 token：顶层 `reasoning_tokens` 或 completion/output details 中的
  `reasoning_tokens`。

provider 不返回缓存字段时，WinPilot 不臆造命中；UI 中的零命中不等于缓存功能
失效，也可能只是接口没有返回该统计。每次模型响应会发布 `agent_usage`，并累计到 run
的 `model_usage`；这是成本/实验观测面，不会创建缓存。

真实可见 Electron 长 Thread 报告（20 个聊天 Turn，另含末尾一个失败 connector
probe）累计 `21626` prompt、`13248` cache hit、`8378` miss、`0` creation；新鲜 Thread
的两个 connector 场景累计 `92964` prompt、`53376` hit、`39588` miss、`0` creation。
这证明上游自动缓存字段确实被记录，不证明 vNext 主动创建缓存，也不能单独证明任务
完成率或节省金额。

## 成本

默认配置只内置 DeepSeek V4-Flash 的 CNY 估价。按 2026-07-14 核对的官方
[模型与价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，每百万
token 的缓存未命中输入、缓存命中输入和输出分别为 1 元、0.02 元和 2 元。
价格配置带 `provider` 过滤条件；当前 provider 不匹配时，系统继续记录
token/cache/reasoning usage，但不会套用别家价格或伪造成本。上游价格可能变化，
成本面板只是估算；修改默认模型或发现价格变更时必须同步默认配置、fallback、
测试和本文，最终账单以上游控制台为准。

MiMo 官方价格会变化，代码没有硬编码。需要估价时，应从官方
[模型与价格页](https://mimo.mi.com/docs/en-US/quick-start/model)取得当前价格，
再在本机配置中填写：

```json
{
  "api": {
    "provider": "mimo",
    "base_url": "https://api.xiaomimimo.com/v1",
    "model": "mimo-v2.5",
    "thinking": false,
    "pricing": {
      "provider": "mimo",
      "currency": "CNY",
      "input_per_m": null,
      "cache_hit_input_per_m": null,
      "cache_creation_input_per_m": null,
      "output_per_m": null
    }
  }
}
```

把 `null` 替换为核对过的每百万 token 价格后才会估价。`input_per_m` 也可写作
`cache_miss_input_per_m`。最终账单始终以上游控制台为准。

## 官方参考

- [MiMo 模型页](https://mimo.mi.com/docs/en-US/quick-start/model)
- [MiMo 图像理解](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/multimodal-understanding/image-understanding)
- [MiMo Deep Thinking](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/deep-thinking)
- [MiMo API Integration FAQ](https://mimo.mi.com/docs/en-US/quick-start/faq/api-integration)
- [MiMo OpenAI-compatible API Reference](https://mimo.mi.com/docs/api/chat/openai-api)

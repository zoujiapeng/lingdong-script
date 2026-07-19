# 模型 Provider、视觉路由、Thinking、缓存与成本

本文只描述当前代码和官方文档能够证明的事实。模型能力口径核对日期为
2026-07-14；如果上游模型能力发生变化，应同时更新
`python/winpilot_home/winpilot/model_adapter.py`、对应测试和本文，不能只改模型名称或提示词。

## 当前调用路径

WinPilot 只有一个 `AgentRunner`。GUI、编程和仓库任务共享同一条
OpenAI-compatible Chat Completions 调用路径；编程能力由 RuntimeProfile
启用，不存在第二套 Coding Agent 主循环。

`python/winpilot_home/winpilot/model_adapter.py` 是薄适配层：

- `ModelProfile` 归一化 provider、模型名和原生视觉能力。
- `OpenAICompatibleAdapter` 保持
  `client.chat.completions.create(...)` 的调用形状。
- `request_extra_body()` 处理 MiMo / Reasonix 的 thinking 参数。
- `normalize_usage()`、`accumulate_usage()` 归一化 token、缓存和成本。

它不是代理服务，也不替代 GUI Controller。配置仍来自
`python/winpilot_home/winpilot/config.py` 和本机、被 Git 忽略的
`python/winpilot_home/config.json`。

## Provider 识别

代码内置专门识别语义的 provider family 包括 `mimo`、`deepseek`、
`reasonix`、`openai` 和 `openai_compatible`。`api.provider=auto` 会按
`base_url` 和 `model` 依次推断 Reasonix、MiMo、DeepSeek、OpenAI，无法
识别时使用 `openai_compatible`。显式填写的其他自定义 provider 字符串会原样
保留，仍使用 OpenAI-compatible 调用形状，但不会自动获得上述 family 的
thinking、价格过滤或 usage 特判。

这只是运行时需要的能力识别，不是完整 provider registry。模型名称相似、
上下文更长或推理更强，都不能自动推出它支持图片。

## MiMo V2.5 的视觉路由

| 模型 | WinPilot 原生图片能力 | GUI 路由 |
| --- | --- | --- |
| `mimo-v2.5` | 是 | 原生视觉主路径，直接接收真实 GUI JPEG |
| `mimo-v2.5-pro` | 否 | text-only GUI 路径；使用 UIA/OCR/CV/非视觉摘要，必要时使用独立 VLM |
| 名称明确含 `omni`、`-vl`、`vision` 或 `gpt-4o` 的模型 | 是 | 原生视觉主路径 |
| 其他未显式登记的模型 | 否 | text-only GUI 路径 |

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

原生视觉路径在初始回合、`observe` 后和每笔视觉事务后注入新鲜画面。画面可能
是经过验证的前台目标窗口裁剪，也可能是完整虚拟桌面；每帧都会声明其范围。
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

因此 WinPilot 现在把 provider 的 reasoning 和 GUI 运行状态分开处理：

- `api.thinking=true` 或显式 `{"type":"enabled"|"disabled"}` 是用户配置，优先级最高；
- `api.thinking=false` 仍是默认值，但对本身默认关闭 thinking 的原生视觉 provider，
  `controller.native_visual_thinking_mode="adaptive"` 会在首帧、画面未变、动作被阻止、
  完成验收失败或恢复中的回合临时打开 reasoning；动作已经由新帧和因果证据确认后，
  例行回合才走轻量路径；
- `controller.native_visual_thinking_mode` 可设为 `off`、`adaptive` 或 `always`。它只读取
  当前 frame/action/verification evidence，不匹配任务词、应用名或 11 个样例；
- `api.extra_body` 可做显式高级覆盖；调用者需要自行承担兼容性、延迟和截断风险。

每次请求还受 `api.request_timeout_s`、`api.request_total_timeout_s` 和
`api.max_retries` 约束。总截止时间到达时，late response 会在工具分发前被丢弃；
因此重试不会重复已经执行的 GUI 动作。GUI 工具调用截断恢复仍会关闭 thinking，
以给完整函数参数保留输出预算。该策略适用于所有原生视觉任务，不是画图或连续
指针专属能力，也不会让 `mimo-v2.5-pro` 获得视觉能力。

## 缓存

WinPilot 的缓存策略是 `stable_prefix`：尽量保持消息前缀稳定并 append-only，
只在上下文过大时折叠旧的 `observe`、`read_text` 和 `list_windows` 结果。
这能让支持前缀缓存的上游提高命中率，但不承诺任何 provider 一定缓存。

`normalize_usage()` 当前识别：

- DeepSeek：`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`。
- OpenAI Chat：`prompt_tokens_details.cached_tokens`。
- OpenAI Responses 风格：`input_tokens_details.cached_tokens`。
- Anthropic-style 代理字段：`cache_read_input_tokens`、
  `cache_creation_input_tokens`。
- 推理 token：顶层 `reasoning_tokens` 或 completion/output details 中的
  `reasoning_tokens`。

provider 不返回缓存字段时，WinPilot 不臆造命中；UI 中的零命中不等于缓存功能
失效，也可能只是接口没有返回该统计。

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

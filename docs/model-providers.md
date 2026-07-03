# 模型 Provider、缓存命中与 Usage 统计

本文描述当前 WinPilot 代码里的真实机制，以及本次从本地 `cc-haha` 借鉴但没有照搬的设计边界。

## 当前真实机制

- 用户面对的是一个 WinPilot Agent；主 GUI / Computer Use capability 入口是 `python/winpilot_home/winpilot/agent/loop.py`，Coding capability 入口是 `python/winpilot_home/winpilot/agent/coding.py`。
- 两者仍然调用 OpenAI-compatible Chat Completions，不引入代理服务，也不改变 WinPilot GUI computer-use controller 主线。
- 通用适配层在 `python/winpilot_home/winpilot/model_adapter.py`：
  - `ModelProfile` 负责按 `api.provider/base_url/model` 推断 provider family。
  - `OpenAICompatibleAdapter` 保持 `client.chat.completions.create(...)` 调用形状。
  - `normalize_usage()` 统一解析 prompt/completion/cache/reasoning token 字段。
  - `accumulate_usage()` 统一累计 run 级 usage 和可用时的 cost。
- 配置源仍是 `python/winpilot_home/winpilot/config.py` 和本机 `config.json`；默认 `api.provider=auto`，默认文本模型仍是 `deepseek-v4-flash`。

## Provider 识别

`api.provider` 可以显式设置为 `deepseek`、`mimo`、`reasonix`、`openai_compatible` 等；默认 `auto` 会从 `base_url` 和 `model` 推断。

这不是完整 provider registry。当前只做运行时需要的薄识别：模型是否显式支持 vision、是否需要默认禁用 provider-side thinking、usage 字段如何归一化。

## MiMo / Reasonix Thinking

对 MiMo / Reasonix 这类 reasoning endpoint，适配层默认在 Chat Completions 请求里加：

```json
{"thinking": {"type": "disabled"}}
```

原因是 GUI/tool decision 更需要稳定输出工具调用，provider-side thinking 可能耗尽输出预算。用户可以在配置里设置 `api.thinking=true`，或通过 `api.extra_body` 传自定义参数覆盖。

DeepSeek 默认不加这个 `extra_body`。

## Vision 能力

主模型是否接收截图由 `ModelProfile.supports_vision` 决定，只在模型名明确包含 `omni`、`-vl`、`vision`、`gpt-4o` 时启用。

因此：

- `mimo-v2-omni` 会被视为 native vision。
- `mimo-v2.5-pro`、`mimo-v2-pro` 仍按 text-only 处理。
- DeepSeek chat/reasoner/v4 文本模型按 text-only 处理；截图需要走独立 VLM 配置。

## 缓存命中

WinPilot 的缓存策略本质是稳定前缀：尽量保持 messages append-only，只有上下文变大后才折叠旧 observe/read_text/list_windows 结果。这不是 DeepSeek 专属策略，MiMo/Reasonix/OpenAI-compatible endpoint 只要上游支持 prefix/prompt cache，也可以从稳定前缀受益。

之前统计上只读取 DeepSeek 风格的 `prompt_cache_hit_tokens`，所以 MiMo 或其他 OpenAI-compatible 返回的缓存字段不会显示为命中。现在 `normalize_usage()` 支持：

- DeepSeek: `prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`
- OpenAI Chat style: `prompt_tokens_details.cached_tokens`
- OpenAI Responses style: `input_tokens_details.cached_tokens`
- Anthropic-style proxy fields: `cache_read_input_tokens`、`cache_creation_input_tokens`

如果 provider 不返回任何 cache usage 字段，WinPilot 不会臆造 cache hit。

## Cost 统计

默认只内置 DeepSeek 的 CNY 价格估算：

```json
{
  "provider": "deepseek",
  "currency": "CNY",
  "input_per_m": 2.0,
  "cache_hit_input_per_m": 0.2,
  "output_per_m": 8.0
}
```

当当前 provider 不是 `deepseek` 时，这张价格表不会被套用到 MiMo/Reasonix。UI 会继续显示 token/cache，但不会把未知价格误显示成真实成本。

要给其他 provider 估算成本，可以在 `api.pricing` 里设置匹配的 `provider` 和价格字段。

## 借鉴 cc-haha 的部分

本次只借鉴了本地 `C:\Users\zou\Desktop\cc-haha` 的通用非 GUI 思路：

- provider 配置和 runtime 行为分层。
- usage 字段先归一化，再由上层累计和估价。
- cache read 字段兼容 DeepSeek/OpenAI/Anthropic-style proxy 多种形态。

没有直接照搬 cc-haha 的 provider service、proxy server、Anthropic/OpenAI 协议互转、桌面 GUI/runtime computer-use 实现。WinPilot 当前仍是轻量 OpenAI-compatible Chat Completions 调用路径。

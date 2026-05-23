"""
VLM 视觉语言模型功能注册
通过 providers.vlm 统一调用后端（支持 GPT-4o-mini / 本地 llama-server）
在设置中可通过 VLM_BACKEND 环境变量或 localStorage 切换。
"""
import os
import time

from features.registry import feature, P, TC, FeatureCategory as F
from providers.vlm import get_vlm_provider, estimate_cost


def _make_error_result(msg: str) -> dict:
    return {"content": "", "input_tokens": 0, "output_tokens": 0,
            "model": "", "elapsed_sec": 0.0, "error": msg,
            "cost": ""}


@feature(
    name="vlm_analyze",
    display_name="VLM 视觉分析",
    description="""【AI·视觉】截取当前屏幕并用 VLM 模型（GPT-4o-mini 或本地 Qwen-2.5-VL）分析画面内容。
返回自然语言描述的场景内容、按钮、文字、状态等信息。

需要设置中打开「多模态（视觉能力）」开关。
后端选择: GPT-4o-mini（默认）或本地模型（需 WSL2 llama-server :8084）。
在设置中可切换 VLM 后端。""",
    category=F.AI,
    params=[
        P("prompt", "str", "分析提示词，描述想看什么",
          default="请详细描述这个画面中有什么，包括所有按钮、文字、图标和界面元素的位置关系",
          example="这个画面里有什么按钮？", required=False),
        P("temperature", "number", "生成温度 0-1", required=False, default=0.1),
        P("max_tokens", "int", "最大输出 token 数", required=False, default=512),
    ],
    returns="dict{content, input_tokens, output_tokens, model, elapsed_sec, cost, error}",
    dsl_keyword="VLM_ANALYZE",
    dsl_template="VLM_ANALYZE {prompt}",
    examples=[
        "vlm_analyze prompt='这个画面里有什么按钮？'",
        "vlm_analyze prompt='描述QQ聊天界面的内容' temperature=0.3",
    ],
)
def vlm_analyze(prompt: str = "请详细描述这个画面中有什么",
                temperature: float = 0.1,
                max_tokens: int = 512) -> dict:
    # 检查多模态开关
    multimodal_env = os.environ.get("LLM_MULTIMODAL", "")
    if multimodal_env.lower() in ("false", "0", "no"):
        return _make_error_result("多模态功能已关闭。请在设置中启用「多模态（视觉能力）」开关后重试")

    start = time.time()
    try:
        provider = get_vlm_provider()
    except Exception as e:
        return _make_error_result(f"初始化 VLM provider 失败: {e}")

    result = provider.analyze_screen(prompt, temperature=temperature, max_tokens=max_tokens)
    elapsed = round(time.time() - start, 1)

    # 费用估算
    in_tok = result.get("input_tokens", 0)
    out_tok = result.get("output_tokens", 0)
    cost = estimate_cost(in_tok, out_tok)
    model = result.get("model", "")

    return {
        "content": result.get("content", ""),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "model": model,
        "elapsed_sec": elapsed,
        "error": result.get("error"),
        "cost": cost,
    }

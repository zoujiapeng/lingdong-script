"""
VLM 视觉语言模型功能注册
通过 llama.cpp server (WSL2) 调用 Qwen-2.5-VL 进行图像理解
llama-server 加载主 GGUF + mmproj 视觉编码器，暴露 OpenAI 兼容 API。
"""
import base64
import json
import os
import time
import urllib.request
import urllib.error

import cv2
import numpy as np

from features.registry import feature, P, TC, FeatureCategory as F

VLM_HOST = os.getenv("VLM_HOST", "http://localhost:8084")
DEFAULT_MODEL = "qwen2.5-vl:3b"


def _screenshot_base64() -> str:
    """截图并返回 JPEG base64 字符串"""
    from perception.vision import ScreenCapture
    img = ScreenCapture.capture()
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf).decode()


def _call_vlm(model: str, prompt: str, image_b64: str,
               temperature: float = 0.1, max_tokens: int = 512) -> dict:
    """调用 llama.cpp server 的 chat API (OpenAI 兼容格式)"""
    url = f"{VLM_HOST}/v1/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode()

    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {})
        return {
            "content": content,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }
    except urllib.error.HTTPError as e:
        return {"error": f"VLM HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"}
    except urllib.error.URLError as e:
        return {"error": f"无法连接 VLM server ({VLM_HOST}): {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


@feature(
    name="vlm_analyze",
    display_name="VLM 视觉分析",
    description="""【AI·视觉】截取当前屏幕并用本地 VLM 模型（Qwen-2.5-VL）分析画面内容。
返回自然语言描述的场景内容、按钮、文字、状态等信息。

需要 WSL2 中运行 llama-server 并加载 Qwen2.5-VL GGUF + mmproj。
llama-server 监听 localhost:8084。""",
    category=F.AI,
    params=[
        P("prompt", "str", "分析提示词，描述想看什么",
          default="请详细描述这个画面中有什么，包括所有按钮、文字、图标和界面元素的位置关系"),
        P("model", "str", "模型名称（已预加载到 llama-server）", required=False, default=DEFAULT_MODEL),
        P("temperature", "number", "生成温度 0-1", required=False, default=0.1),
        P("max_tokens", "int", "最大输出 token 数", required=False, default=512),
    ],
    returns="dict{content, input_tokens, output_tokens, model}",
    dsl_keyword="VLM_ANALYZE",
    dsl_template="VLM_ANALYZE {prompt}",
    examples=[
        "vlm_analyze prompt='这个画面里有什么按钮？'",
        "vlm_analyze prompt='描述QQ聊天界面的内容' temperature=0.3",
    ],
)
def vlm_analyze(prompt: str = "请详细描述这个画面中有什么",
                model: str = DEFAULT_MODEL,
                temperature: float = 0.1,
                max_tokens: int = 512) -> dict:
    start = time.time()
    try:
        image_b64 = _screenshot_base64()
    except Exception as e:
        return {"content": "", "error": f"截图失败: {e}"}

    result = _call_vlm(model, prompt, image_b64,
                          temperature=temperature, max_tokens=max_tokens)
    elapsed = round(time.time() - start, 1)
    return {
        "content": result.get("content", ""),
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "model": model,
        "elapsed_sec": elapsed,
        "error": result.get("error"),
    }

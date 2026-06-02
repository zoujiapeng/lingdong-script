# Agent 配置记录（成功方案）

> 记录时间: 2026-06-02
> 分支: `refactor/agent-redesign`（基于 `57231ff`）
> Python: C:\Python314
> 测试任务: "打开计算器，计算7除以99的结果"

---

## 目录

1. [MIMO 多模态方案](#1-mimo-多模态方案)
2. [DeepSeek 纯文本方案（SoM+OCR+窗口限定）](#2-deepseek-纯文本方案somocr窗口限定)
3. [代码改动清单](#3-代码改动清单)
4. [前端 localStorage 参数](#4-前端-localstorage-参数)
5. [关键路径图](#5-关键路径图)

---

## 1. MIMO 多模态方案

### 参数

| 参数 | 值 | 说明 |
|---|---|---|
| `model` | `mimo-v2-omni` | 小米多模态视觉模型 |
| `api_base` | `https://api.xiaomimimo.com/v1` | |
| `api_key` | `REDACTED_SILICONFLOW_KEY_1` | |
| `llm_protocol` | `auto` → `text` | auto 检测到多模态 → text |
| `planning` | `auto` → `per_step` | 每步规划 |
| `visual_strategy` | `auto` → `direct` | 多模态直接用截图 |
| `multimodal` | `true` | 关键标志 |
| `som_enabled` | `true` | (不影响多模态，多模态直接跳过 SoM) |
| `local_vision` | `false` | |
| `uia_enabled` | `false` | 不需要 UIA |
| `window_aware_ocr` | `false` | 不需要窗口限定 |
| `reflection_enabled` | `false` | 关闭记忆 |
| `auto_learn` | `false` | 关闭自动学习 |

### 执行路径

```
AgentView 前端
  → agentStart(goal, config)
  → LingdongAgent.run(goal, agent_config)
  → _unified_run()
  → resolve_auto() → multimodal=true
    → llm_protocol=text, planning=per_step
  → run_text_protocol()
    → capture screenshot
    → skip SoM（multimodal=true）
    → send image + text to LLM
    → LLM sees buttons directly in image
    → LLM outputs "工具: click\n参数:\n  target: 7"
    → meta_execute("click", {target: "7"})
    → click() → click_target("7")
    → ActionHandler clicks at coordinates
    → screenshot feedback loop
```

### 关键特点

- MIMO 直接看截图，不依赖 SoM 编号
- 所有按钮、文字、布局都在图片里，LLM 直接理解
- 每步截图验证，LLM 看到变化后决定下一步
- @feature 提示词已精简，只保留基础工具
- 无 UIA、无 OCR、无 YOLO 参与

---

## 2. DeepSeek 纯文本方案（SoM+OCR+窗口限定）

### 参数

| 参数 | 值 | 说明 |
|---|---|---|
| `model` | `deepseek-chat` | DeepSeek V3 / V4 |
| `api_base` | `https://api.deepseek.com/v1` | |
| `api_key` | `REDACTED_DEEPSEEK_KEY` | |
| `llm_protocol` | `text` | 必须用 text protocol |
| `planning` | `per_step` | 每步规划 |
| `visual_strategy` | `som` | SoM 编号标注 |
| `multimodal` | `false` | 纯文本模型 |
| `som_enabled` | `true` | 启用 SoM |
| `local_vision` | `false` | YOLO 可选，默认关闭 |
| `uia_enabled` | `false` | 关闭 UIA，纯视觉 |
| `window_aware_ocr` | `true` | 只扫活动窗口区域 |
| `reflection_enabled` | `false` | 关闭记忆 |
| `auto_learn` | `false` | 关闭自动学习 |
| `memory` | 已清空 | `~/.lingdong/agent_memory/*.json` 删除 |

### 执行路径

```
AgentView 前端
  → agentStart(goal, config)
  → LingdongAgent.run(goal, agent_config)
  → _unified_run()
  → resolve_auto() → multimodal=false + visual_strategy=som
    → llm_protocol=text, planning=per_step, vision_mode=som
  → run_text_protocol()
    → capture screenshot (1600px, quality=60)
    → _apply_som(b64_img, multimodal=false)
      → ① Local Vision Engine（YOLO, 可选）
      → ② UIA 控件树（uia_enabled=false → 跳过）
      → ③ OCR 文字检测（EasyOCR）
      → ④ 窗口限定（window_aware_ocr=true）
          只保留活动窗口内的元素
      → ⑤ 编号+画框
      → 返回 {image_b64, label_map, count}
    → 构建提示词:
      "屏幕元素编号:
        #1: 「计算器」 坐标(1200,500)
        #2: 「7」 坐标(1408,595)
        #3: 「÷」 坐标(1500,620)
        ..."
    → 发送文字提示词给 LLM（不含图片）
    → LLM 回复: "工具: click\n参数:\n  target: #2"
    → SoM #2 → 坐标(1408,595)
    → meta_execute("click", {target: "1408,595"})
    → click() → click_target("1408,595")
    → ActionHandler clicks at (1408, 595)
    → screenshot feedback → 重新 SoM → 循环
```

### 关键特点

- **纯视觉，无 UIA**：全靠 EasyOCR 读文字 + 编号
- **窗口限定减少干扰**：只扫活动窗口内部元素
- **无记忆污染**：reflection 和 auto_learn 都关闭
- **提示词精简**：只列基础工具，LLM 决策更快
- **SoM 文字→坐标兜底**：supervisor 里 `#N` 和文字都解析
- **首次 SoM 慢（30-60s）**：EasyOCR 加载模型，后续快

---

## 3. 代码改动清单

所有改动在 `refactor/agent-redesign` 分支上。

### 3.1 提示词精简

| 文件 | 改动 |
|---|---|
| `agent.py:80` | `SYSTEM_PROMPT_TEXT` — 从 ~1920 字精简到 ~300 字，只留基础工具 |
| `agent.py:1740` | LangGraph system prompt — 精简到 7 个工具，去掉路径策略 |
| `agent.py:1259` | `som_trust` → 移除了聊天操作流程等无关内容 |

### 3.2 Supervisor 修复

| 文件 | 改动 |
|---|---|
| `supervisor.py:68` | 新增 `self.uia_enabled: bool = True` |
| `supervisor.py:392-401` | SoM 文字→坐标匹配（新增 `elif` 分支） |
| `supervisor.py:403-418` | UIA 语义定位 → 加 `if self.uia_enabled` |
| `supervisor.py:431-443` | UIA 预聚焦 → 加 `if self.uia_enabled` |
| `supervisor.py:464-485` | 防重复拦截 → 改成精确匹配优先，短文字 (1-2字) 不触发 |

### 3.3 Agent 执行流修复

| 文件 | 改动 |
|---|---|
| `agent.py:476` | YOLO 引擎调用 → UIA 禁用时不传 uia_items |
| `agent.py:526-555` | UIA 控件树整段 → 包在 `if self.supervisor.uia_enabled` 里 |
| `agent.py:597` | 窗口限定过滤 → SoM 元素按活动窗口裁剪 |
| `agent.py:1802-1812` | LangGraph SoM 预扫描（首次截图+OCR+编号） |
| `agent.py:2320` | supervisor.uia_enabled 从 config 传播 |

### 3.4 Config 修复

| 文件 | 改动 |
|---|---|
| `config.py:165-171` | `visual_strategy=ocr_llm` 时强制 `protocol=text` + `planning=per_step` |

### 3.5 前端修复

| 文件 | 改动 |
|---|---|
| `AgentView.tsx:291` | 预设切换不再 `onKeyChange('')` 清空 key |
| `AgentView.tsx:1012-1018` | 切换预设时自动加载该模型对应的 key |
| `AgentView.tsx:390` | 新增 `lingdong-key-{model}` per-model key lookup |

---

## 4. 前端 localStorage 参数

### DeepSeek 配置（成功后保存）

```javascript
// 模型配置
localStorage.setItem('lingdong-llm-model', 'deepseek-chat');
localStorage.setItem('lingdong-llm-base', 'https://api.deepseek.com/v1');
localStorage.setItem('lingdong-deepseek-key', 'REDACTED_DEEPSEEK_KEY');
localStorage.setItem('lingdong-llm-multimodal', 'false');

// Agent 配置
localStorage.setItem('lingdong-agent-visual-strategy', 'som');
localStorage.setItem('lingdong-agent-local-vision', 'false');
localStorage.setItem('lingdong-agent-som', 'true');
localStorage.setItem('lingdong-agent-window-aware-ocr', 'true');
localStorage.setItem('lingdong-agent-multimodal', 'false');
localStorage.setItem('lingdong-agent-protocol', 'text');
localStorage.setItem('lingdong-agent-planning', 'per_step');
localStorage.setItem('lingdong-agent-auto-learn', 'false');
localStorage.setItem('lingdong-agent-verify', 'true');

// Per-model key 映射（切换预设时自动加载）
localStorage.setItem('lingdong-key-deepseek-chat', 'REDACTED_DEEPSEEK_KEY');
localStorage.setItem('lingdong-key-mimo-v2-omni', 'REDACTED_SILICONFLOW_KEY_1');
```

### MIMO 配置

```javascript
// 模型配置
localStorage.setItem('lingdong-llm-model', 'mimo-v2-omni');
localStorage.setItem('lingdong-llm-base', 'https://api.xiaomimimo.com/v1');
localStorage.setItem('lingdong-llm-key', 'REDACTED_SILICONFLOW_KEY_1');
localStorage.setItem('lingdong-llm-multimodal', 'true');

// Agent 配置（MIMO 用默认即可，auto 检测到多模态自动走 direct）
localStorage.setItem('lingdong-agent-protocol', 'auto');
localStorage.setItem('lingdong-agent-planning', 'auto');
localStorage.setItem('lingdong-agent-visual-strategy', 'auto');
localStorage.setItem('lingdong-agent-multimodal', 'true');
```

---

## 5. 关键路径图

```
                    ┌─────────────────────────┐
                    │   AgentView (React)      │
                    │  buildAgentConfig()       │
                    └──────────┬──────────────┘
                               │ IPC: agent:start
                               ▼
                   ┌───────────────────────┐
                   │   __main__.py          │
                   │   _run_agent()         │
                   │   AgentConfig.from_dict│
                   └──────────┬────────────┘
                              ▼
                   ┌───────────────────────┐
                   │   LingdongAgent        │
                   │   .run()               │
                   └──────────┬────────────┘
                              │ _unified_run()
                              ▼
          ┌─────────────────────────────────────┐
          │        resolve_auto()               │
          │  multimodal=true   │   multimodal=false│
          │  + visual_strategy │   + visual_     │
          │  = "direct"       │   strategy="som" │
          └────────┬──────────┴────────┬────────┘
                   │                   │
         ┌─────────▼──────┐   ┌────────▼──────────┐
         │ run_text_      │   │ run_text_protocol()│
         │ protocol()     │   │                    │
         │ (with image)   │   │ _apply_som()       │
         │                │   │  → OCR + 编号      │
         │ LLM sees       │   │  → 窗口限定        │
         │ image directly │   │  → 文字描述给LLM    │
         └────────┬───────┘   └────────┬──────────┘
                  │                    │
                  └─────────┬──────────┘
                            │
          ┌─────────────────▼─────────────────┐
          │   meta_execute() / supervisor.    │
          │   execute()                       │
          │    → SoM解引用 (#N→坐标)          │
          │    → UIA定位 (if enabled)         │
          │    → 视觉策略 (ocr_llm等)         │
          │    → registry.execute()           │
          │    → ActionHandler → pyautogui    │
          └─────────────────┬─────────────────┘
                            │
          ┌─────────────────▼─────────────────┐
          │   screenshot → feedback loop       │
          │   _append_step_feedback()          │
          │    → 截图 → SoM → LLM → ...       │
          └───────────────────────────────────┘
```

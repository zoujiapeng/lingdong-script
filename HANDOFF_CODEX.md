# HANDOFF_CODEX

更新时间: 2026-06-20
分支: `feat/winpilot-agent`
目标: 让 WinPilot 在非多模态/低视觉模型路径下具备更接近 Computer Use 的通用桌面操控能力，并减少假成功。

## 本轮核心结论

1. 单纯加长系统提示词没有解决问题。`full` 提示约 8.8k 字符，`compact` 约 2.2k 字符；真正的问题是感知噪声、工具选择跑偏、世界状态摘要不足。
2. 中文任务必须按前端 UTF-8 路径测试。PowerShell here-doc 曾把中文发成 `????`，导致 GUI-only 中文触发失效。后续 API 测试必须用 UTF-8 JSON 或 Unicode escape。
3. 世界模型原本只是“动作/窗口/重复状态记事本”，还不能形成“当前画布有什么、工具面板是否覆盖、颜色是否属于作品”的高层事实。本轮把这部分开始补到 observe 和 world advisory。
4. UIA 不是万能的。Paint 的 `Edit colors` 这种 `XAMLModalWindow` 会让主窗口 UIA observe 卡死，必须检测前台模态覆盖并降级到 OCR/CV/视觉摘要。

## 已完成代码更新

### Prompt 与任务模式

- 新增 `agent.prompt_style` 配置:
  - `full`: 原长系统提示。
  - `compact`: 保留硬约束的短提示。
- GUI-only 中文识别增强:
  - 支持 `GUI方式`、`可见GUI`、`必须通过可见`、`不要用脚本` 等中文表达。
  - GUI-only 主体工作会阻止 `search_files`、写文件、脚本生成、非只读 shell。
  - 只允许最终只读验证，例如 `Test-Path`、`Get-Content`、`Select-String`。
- 绘图任务 guidance 增强:
  - 要求 visible pixels，不只选工具。
  - 要求保存输出文件存在。
  - 如果用户要求颜色，保存图必须有对应颜色证据。

### 世界模型与视觉摘要

- `observe` 对绘图/画布/颜色任务追加 `Visual state summary`:
  - 优先定位最大浅色画布区域 `likely_canvas@(...)`。
  - 只统计画布内红/蓝/绿/黄/黑等粗粒度颜色。
  - 报告最大色块 bbox，避免工具栏小色块污染。
- 新增一次性 `[visual state warning]`:
  - 如果 popup/tool/color modal 打开，会单独提醒 LLM。
  - 明确说明“面板颜色不是画布证据”，要求先 OK/Escape/关闭面板再回画布。
- 增加工具面板循环检测辅助:
  - 已有 world stuck signal 保留。
  - 新视觉告警和 stuck replan 配合，减少重复点击 Line/Fill/Color 面板。

### UIA/感知稳定性

- `uia.py`:
  - 降低 `uiautomation` 全局搜索超时。
  - 降低 UIA 遍历时间预算，避免单轮 observe 过慢。
- `fusion.py`:
  - 新增 `_foreground_modal_over_target`。
  - 当前台 `XAMLModalWindow` / modal / popup 覆盖目标窗口时跳过主窗口 UIA。
  - 继续使用 Win32/OCR/CV，避免 observe 卡死。
- 现场验证:
  - Paint `Edit colors` 之前会卡住。
  - 修复后同状态 observe 约 4.6s 返回，OCR 能读到 `Edit colors`、`#ED1C24`、RGB 值。

### 绘图/保存/假成功防护

- `vision_cv.py` 新增:
  - `image_ink_stats(path)`: 判断保存图片是否空白、墨迹 bbox、连通域。
  - `image_color_stats(path)`: 判断保存图片是否有指定颜色像素。
  - `color_presence(image)`: 对窗口/画布截图提取粗颜色存在和最大 bbox。
  - `likely_canvas_bbox(image)`: 通用找大面积浅色画布区域。
- done gate 增强:
  - 绘图任务无 drag 不允许 done。
  - 要求输出图文件存在。
  - 要求颜色时，保存图必须有对应颜色像素。
  - 防止“黑线房子却声称红太阳/蓝房子”。
- drag 工具增强:
  - 拖动前后截局部图，计算 `visual_change_ratio`。
  - 返回 `visual_change_detected` 和解释，帮助模型知道是否真的画到画布。

### 搜索/编程任务

- 编程子 agent 增强:
  - 记录工具历史和完成拒绝次数。
  - clone/run 类任务不能只读 README 后成功。
  - 必须尝试 install/build/run/test/check 之一，否则 done 被拒绝。
  - 对 `.env`、token、credential 写入有保护。
- `cc-haha` 真实测试:
  - 能通过 GitHub 搜索找到仓库。
  - 能 clone 到桌面。
  - 能运行 `bun install`。
  - 能尝试 CLI 入口并报告缺少凭据/配置，而不是假成功。

### GUI 搜索/商店/上传任务

- GUI 搜索/商店任务:
  - shell/manifest/process 证据只能辅助。
  - 必须打开目标 app/client/page，并以可见 observe/read_text 结果作为完成证据。
  - 阻止用 `steam://rungameid` / install/run 协议替代 store/search 页面。
- 上传/发布任务:
  - 媒体文件被视为上传 payload，不是要播放/分析的内容。
  - 阻止 Windows Media Player/VLC/mpv/ffprobe/ffmpeg 等播放或分析路径。
  - playbook 过滤上传任务里的 `media-playback`。
  - 抖音测试中已能到创作者中心上传页，仍需继续打磨最终上传/标题/发布前停住流程。

### 前端/调试可观测性

- LLM forensic trace:
  - 每次 request/response/tool result 写入 `traces/<run_id>/NNN_*.json`。
  - 用于复现 LLM 每一步收到的信息，定位“它为什么会误判”。
- `/api/chat` 路径保持为前端等价路径。
- `/api/interject` 可中途纠正，已验证 agent 能收到纠正，但复杂工具状态下仍可能不够聪明。
- 前端运行中可插话/停止行为已参与测试。

### 路由/经验/复用

- GUI-only 任务保持走桌面 agent，不进入 coding agent。
- coding/repo 类任务排除经验 replay，避免跨 repo 假复用。
- 通信/QQ 等任务增加 done guard:
  - 发送消息必须在目标会话/输入区有最终可见证据。
  - 不能只凭 click/type/Enter ok 就 claim success。

## 真实测试记录

### Paint 绘图

- `full` prompt:
  - 找到并打开 Paint 后跑偏到 `search_files`/shell。
  - 甚至开始在 shell 输出另一套 agent prompt。
  - 结论: 长提示没有解决工具选择问题，反而容易上下文污染。
- `compact` prompt:
  - 更省 token，但没有硬约束时仍会先搜文件/命令行绕路。
- `compact + GUI-only guard`:
  - 成功走到 `focus_window -> observe -> drag`。
  - 确实通过 GUI 画线并保存文件。
  - 初期假成功: 画的是黑线房子/树，不是红太阳/蓝房子。
  - 后续加入颜色 done gate，防止这种假成功。
- UTF-8 中文路径测试:
  - 正常规划，不再乱码。
  - 能读 `Color 1: Red`、`Color 1: Indigo`。
  - 暴露工具面板循环和 UIA 卡死问题。
- 修复后:
  - 颜色面板状态会产生 `[visual state warning]`。
  - 现场验证 warning 已进入 LLM 上下文，模型响应为先 Escape 关闭面板。

### Steam

- 初始失败:
  - 用 shell 读本地 Steam manifests，误判游戏未安装/未打开 GUI。
- 改进后:
  - store/search 任务必须可见页面证据。
  - 阻止 run/install protocol 替代 store page。
  - 能打开 Steam store page，截图确认 Stardew Valley 页面可见。
  - 后续仍需处理 Steam webhelper 弹错和 DeepSeek API 连接中断。

### 抖音

- 本地已安装抖音，Chrome 创作者中心已登录。
- 初始失败:
  - 把上传视频任务误当媒体播放/内容分析任务。
  - 打开 Windows Media Player/VLC/ffprobe 路线。
- 改进后:
  - 上传任务会阻止播放器和分析器。
  - 能进入创作者中心上传页并打开文件选择流程。
  - 仍需要继续真实验证标题填写、文件上传完成证据、最终发布前停住。

### QQ

- 曾经能简单计算器任务成功。
- QQ 发消息复杂任务仍容易:
  - 多开 QQ。
  - 死盯未登录窗口，不切换已登录窗口。
  - 点击步骤偶尔对但模型不理解。
- 已加入窗口扫描/登录窗口避让/通信 done guard。
- 仍需继续针对 UIA 不可靠、OCR/SOM/YOLO 信息过载的问题做聚合摘要。

## 当前剩余问题

1. 绘图类任务仍没有达到稳定 computer-use 级别。
   - 现在能识别画布颜色和工具面板，但完整“红太阳 + 蓝房子 + 保存”还需要重新跑长任务验证。
2. UIA 卡死已缓解，但 OCR 在模态窗口可能仍耗时 4-5s。
   - 可继续优化为前台模态专门 observe 该模态区域，而不是主窗口。
3. 视觉摘要还只是颜色/画布级别。
   - 后续需要对象级摘要，例如“画布已有红色圆形、没有蓝色房子”。
4. YOLO/SOM/OCR 信息对 LLM 仍可能过载。
   - 下一步应做 top-k 任务相关裁剪，而不是把所有候选直接给模型。
5. DeepSeek 视觉能力尚未正式接入主流程。
   - 需要继续测试 DeepSeek vision/chat 接口是否稳定支持图像输入。

## 本轮新增/更新测试

- `test_app_resolution.py`
- `test_coding_run_gate.py`
- `test_false_success_guards.py`
- `test_fusion_modal_skip.py`
- `test_image_ink_stats.py`
- `test_router_gui_only.py`
- `test_target_window_anchor.py`
- `test_visual_state_summary.py`
- `test_vlm_guard.py`

最终本地测试:

```text
python -m pytest python/winpilot_home/tests -q
102 passed
```

## 推送说明

- 本地截图/运行证据未提交，已加入 `.gitignore`:
  - `output_*.png`
  - `_*.png`
- 不提交:
  - `python/winpilot_home/config.json`
  - `python/winpilot_home/traces/`
  - `python/winpilot_home/scripts/`
  - 本地截图和临时运行文件

## 下一步建议

1. 重跑完整任务:
   - `GUI方式测试：打开画图，用鼠标拖动画布画一个红色太阳和一个蓝色小房子，然后保存到桌面...`
2. 记录每一步 LLM 收到的 observe、visual warning、颜色摘要。
3. 如果仍失败:
   - 加对象级视觉摘要。
   - 给 observe 增加“当前前台模态/弹窗”显式字段。
   - 对重复工具面板点击增加更硬的恢复策略。
4. 再跑 QQ/抖音/Steam/网易云的长任务矩阵，逐个记录失败模式。

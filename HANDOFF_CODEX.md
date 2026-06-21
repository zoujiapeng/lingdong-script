# HANDOFF_CODEX

分支：`feat/winpilot-agent`

本文件记录最近 Codex/Claude Code 对 WinPilot 的主要改动和当前调试方向。权威架构说明见 `README.md`、`CLAUDE.md` 和 `docs/`。

## 近期核心成果

- WinPilot 主体位于 `python/winpilot_home/winpilot/`。
- `python/agent/__main__.py` 作为 IPC adapter 驱动 WinPilot runner。
- GUI 任务走 `AgentRunner`；编程/仓库任务走 `CodingRunner`。
- `CodingRunner` 增加 GitHub 搜索能力和 clone/run 类任务 gate，避免“只读 README 就成功”。
- GUI-only 任务限制 shell/file 绕路，尽量保持与前端真实测试路径一致。
- `done` 增加多类防假成功 gate：
  - 绘图要求真实拖拽、保存文件和颜色证据。
  - 通信要求指定文本在目标界面可见。
  - GUI 搜索要求屏幕证据。
  - 上传/发布任务阻止用播放/分析假装完成。
- 感知侧加入：
  - UIA 超时预算。
  - 前台模态覆盖时跳过目标 UIA。
  - 绘图/画布/颜色任务的视觉摘要。
  - drag 前后图像差异反馈。
- `agent.llm_trace` 默认开启，用来复现模型每一步收到的信息。

## 当前仍要盯的方向

1. **弱视觉/非多模态路径**
   OCR、CV、UIA 信息必须压缩成模型能理解的摘要。不要把所有原始框、文本、置信度噪声直接塞给模型。

2. **真实前端路径测试**
   用户特别强调“以前端为准”。测试时要截图、看 trace、看保存文件/进程/目标窗口，不能只看 agent 日志。

3. **UIA 覆盖不到的软件**
   QQ、抖音、Steam、网易云等场景应靠通用窗口扫描、焦点窗口限定、OCR/CV/VLM、进程检测协同，不写软件专属成功规则。

4. **窗口候选和重复错误窗口**
   agent 曾打开多个 QQ 并死盯错误窗口。需要继续让 observe/scan_windows 输出“候选窗口比较”和“当前焦点是否目标窗口”。

5. **DeepSeek 视觉**
   若 DeepSeek 视觉接口稳定可用，应作为 `perception/vlm.py` 可选维度接入。不要让文本路径退化成强依赖 VLM。

## 验证命令

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```

当前 WinPilot 测试集在 `python/winpilot_home/tests/`。根目录 `tests/` 混有旧体系和历史实验测试，失败时先判断是否引用已删除旧 Agent。

## 文档同步

本轮已把主要文档改为按当前代码说明：

- `README.md`
- `CLAUDE.md`
- `交接说明.md`
- `优化方案.md`
- `CHANGELOG.md`
- `docs/*.md`

后续若改动 Agent/接口/配置/测试命令，必须同步这些文件。

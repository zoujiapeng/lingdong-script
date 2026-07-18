# GUI Agent 端到端测试规范

本文定义什么可以叫“真实客户端 E2E”、什么可以计为通过，以及如何使用 11 个真实任务和组合长任务而不把它们写进生产逻辑。

## 测试分层

| 层级 | 入口 | 可以证明 | 不能证明 |
| --- | --- | --- | --- |
| 单元测试 | 直接函数/类 | 局部算法、合同、失败语义 | 用户客户端可用 |
| 组件/API 集成 | FastAPI、renderer client | 协议、持久化、模块协作 | Electron 焦点、窗口切换、真实输入 |
| Controller 靶场 | 真实受控窗口 + 物理输入 | 坐标、点击、拖拽、路径、按住、外部事件 | 未知真实软件泛化 |
| 客户端视觉 E2E | 可见 Electron 客户端 + 受控目标 | 与用户同入口的视觉闭环 | 真实消费软件兼容性 |
| 客户端真实任务 E2E | 可见 Electron 客户端 + 真实应用 | 当前机器、当前版本下的完整结果 | 跨机器统计成功率 |

测试名称必须诚实。直接调用 `/api/v2/arp/runs` 的测试是 runtime integration，不是客户端 E2E。

## 真实客户端 E2E 的必要条件

一次通过必须同时满足：

1. 找到并核验当前工作区启动的 Electron 进程；
2. 通过可见客户端控件填写自然语言目标；
3. 在客户端设置冷启动、步数等用户可见配置；
4. 点击真实“运行任务”控件；
5. 绑定点击后产生的准确 outer run id、child run 和 trace；
6. 保存提交前、handoff 后和终态截图；
7. runtime 没有超时、漂移、未配对输入或 unverified done；
8. 独立 oracle 证明用户目标实际成立；
9. 测试结束后只清理本次启动且已核验属于工作区的进程。

`status=done` 只表示候选完成。没有第 8 条时，报告必须是 `ungrounded`，不能是 `passed`。

## 冷启动与上下文

冷启动用于证明硬实力不依赖历史经验。`cold_start=true` 必须让同一个 run 的：

- Task Contract；
- ARP；
- RuntimeProfile；
- StageRuntime；
- AgentRunner；
- trace

共享 `experience_policy=disabled`。测试需验证没有 experience prompt、Skill replay、Memory Pack、经验写入和反馈。

冷启动不等于删除用户当前对话。上下文规则如下：

- 独立 case 开始前清空 session；
- 同一 scenario 的后续 turn 保留前序 turn 的 typed artifact/reference；
- “刚刚画的画”必须解析到本 scenario 已验证工件，而不是全局最近文件；
- 没有前序工件时必须询问、探索或失败，不能从长期记忆伪造；
- 迟到的旧 run 写回不得污染新 scenario。

## 11 个真实任务的定位

原始 11 题覆盖的不是 11 套产品功能，而是若干通用压力方向：

| 压力方向 | 例子所覆盖的能力 |
| --- | --- |
| 应用发现与复用 | 先找已运行/已登录窗口，再决定是否启动 |
| 开放世界导航 | 观察界面、搜索、滚动、进入子页面 |
| 连续指针 | 画、拖拽、限区路径、游戏式操控 |
| 工件引用 | 截图、刚刚的工件、跨应用传递 |
| 多阶段任务 | 打开、搜索、操作、验证的长链 |
| 视觉理解 | 自绘控件、图表、抓包列表、非标准界面 |
| 持续会话 | 等待外部消息、解析新命令、继续执行 |
| 失败恢复 | 弹窗、焦点丢失、错误窗口、动作无效果 |

允许测试代码保存原始文本、fixture 和精确 oracle。禁止生产 prompt、compiler、router、adapter、state key 或动作坐标根据题号、原句或专属对象分支。

## 组合长任务

`mixed` 套件不是简单拼字符串。每个 scenario 应描述：

- turns；
- turn 之间的 artifact/session dependency；
- 允许的等待点；
- 每个阶段 oracle；
- 最终 oracle；
- 清理方式。

示例结构：先创建一个视觉工件，下一 turn 引用“刚刚的工件”，再进入另一个应用处理，最后核对目标应用中出现的是同一 hash/内容。中间任一阶段失败都不能靠最后一段模型回答掩盖。

## 视觉靶场

`scripts/client_visual_e2e_run.py` 从客户端提交一句不包含规则的目标，例如“完成当前界面上的测试。”。Agent 必须自己从画面理解规则。

受控目标应随机化：

- 位置、尺寸、颜色和方向；
- source/target 数量；
- 路径和障碍；
- 按下后才出现的 checkpoint/target；
- 无关动画和焦点干扰；
- 需要滚动后才出现的内容。

oracle 必须从目标窗口外部记录可信 pointer/keyboard events 和最终状态，不读取 Agent 内部计划来决定成功。

当前视觉套件包含拖入、滑块、dynamic hold、限区 corridor 和 game-like drag。它验证通用输入机制，不得被描述为验证码、植物大战僵尸或任意游戏已经通过。

## 真实应用 oracle

优先级从强到弱：

1. 文件 hash、进程状态、应用导出结果或服务端回执；
2. 目标应用中的新鲜可见状态与 artifact identity；
3. 受控窗口的独立事件记录；
4. 新鲜帧上的视觉 judge；
5. UIA/OCR 文本交叉核对。

模型自述、工具 `ok`、旧截图、仅有像素变化、输入框中存在草稿，都不能自动证明发送、发布、保存或业务完成。

## 焦点与外部干扰

测试机器出现无关命令行、系统弹窗或用户操作时：

- 若抢占焦点或改变输入目标，本次 run 标记为 invalid/interfered；
- 不把它计为产品失败或成功；
- 恢复到干净环境后用相同 seed、提示、冷启动和步数重跑；
- 报告保留干扰原因，但最终基线只采用有效 run。

Agent 自己触发的窗口、Shell UI 或弹窗不是外部干扰，必须作为产品行为评估。

## 命令

先启动真实客户端：

```bash
npm run dev
```

另开终端：

```bash
# 全部受控视觉场景
npm run e2e:client:visual

# 原始 11 个真实任务
npm run e2e:client:original

# 多 turn / 依赖 / 组合长任务
npm run e2e:client:mixed

# 两组真实任务
npm run e2e:client:all
```

单个 deterministic 视觉 case：

```bash
python scripts/client_visual_e2e_run.py --case dynamic-hold --seed 424242 --fail-fast
```

常规回归：

```bash
python -m pytest python/winpilot_home/tests -q
python -m pytest tests -q
npm test
npm run typecheck
npm run build
npm run build:all
```

## 报告要求

每个 E2E 报告至少记录：

- git revision 和工作区；
- Electron PID/HWND/command line；
- task、scenario、seed、turn dependency；
- cold-start/session reset 证明；
- outer/child run id 和 trace 文件；
- model/provider/config hash；
- display/DPI/capture scope；
- tool attempts、successful tools 和输入清理状态；
- external oracle、errors、是否 grounded；
- before/after/terminal screenshots；
- duration 与各阶段耗时。

报告和 trace 位于忽略目录，不把大量一次性 run ID 写进 README 或架构文档。稳定结论可以写入 changelog，但必须同时写清适用范围和未覆盖范围。

## 发布门槛

涉及 GUI 主循环、感知、动作、窗口、完成判断或客户端 harness 的提交，至少需要：

- 相关聚焦测试；
- WinPilot 全量测试；
- TypeScript 测试和 typecheck（若触及前端/协议）；
- 一个真实客户端视觉 E2E；
- 改变真实应用逻辑时，对应真实任务 E2E 或明确记录未验证；
- GitHub Actions 通过。

单次成功不能换算成成功率。只有固定环境、固定协议、多 seed、多次重复和外部 ground truth 才能报告统计指标。

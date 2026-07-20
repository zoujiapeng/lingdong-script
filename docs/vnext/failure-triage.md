# vNext 失败诊断：工具与骨架优先

本项目最容易走偏的方式，是看到一次任务失败就给模型加一句规则、增加一个应用 adapter，或再调用一个模型角色。vNext 要求先证明基础工具和调度骨架正确，再讨论 prompt 或模型能力。

## 1. 总原则

> **如果人类从同一张清晰截图能判断，而 Actor 离线也能判断，但运行时执行错了，这是工具或骨架问题。**

> **如果 Actor 在干净上下文中仍无法理解清晰静态画面，且坐标与工具合同已验证，才是模型视觉能力问题。**

> **如果只在长任务中遗忘，而单义务局部执行正确，这是 Mission/调度问题，不是给 Actor增加更多任务提醒。**

> **如果 Verifier 拒绝后 Actor 重复相同 claim，这是 claim/reducer/context 反馈问题，不是放宽验收。**

## 2. 固定诊断顺序

每次失败按以下顺序检查；前一层未证明正确，不进入下一层。

### 2.1 用户入口与测试真实性

核对：

- 任务是否从当前工作区的可见 Electron 客户端提交；
- outer run、child run、trace 是否准确绑定；
- cold start/session 是否符合 case 定义；
- 外部窗口或用户命令行是否遮挡/抢焦点；
- external oracle 是否真的验证业务结果。

入口错误不能归因于 Agent。

### 2.2 Mission 编译与主线

核对：

- `original_goal` 是否逐字保留；
- obligation 是否描述结果而非 GUI 步骤；
- 是否错误拆分了界面才能知道的信息；
- artifact/recipient/target dependency 是否丢失；
- active obligation 是否由依赖机械选出；
- 用户修改目标后 revision 是否正确。

若单义务 fallback 能完成而多义务版本失败，应先修 Compiler/Reducer，不给 Actor 加“记得下一步”。

### 2.3 Capability Broker

核对：

- Actor 当回合实际看到哪些工具；
- necessary tool 是否缺失；
- 是否同时暴露大量无关工具；
- target/pointer/artifact/adapter 状态是否正确；
- 工具面变化是否来自真实状态，而不是应用名或 prompt；
- mutation adapter 与 GUI Actor 是否可能同时写桌面。

模型没有看到所需工具时，不是模型不会用；模型被二十多个工具干扰时，也不应先加工具选择提示。

### 2.4 应用与窗口发现

核对：

- 是否先枚举已运行、隐藏、托盘和可恢复窗口；
- Start Menu、StartApps、App Paths、快捷方式、安装信息是否完整；
- 候选 executable/process/window identity 是否一致；
- 启动动作后是否有因果快照；
- 是否错误新开实例、退回网页或绑定登录页。

找不到本地应用、重复启动已登录实例，优先修 AppResolver。不能通过 prompt 教模型应用安装路径。

### 2.5 Frame、目标与坐标

核对：

- 模型实际收到哪张图片，分辨率与 capture region 是什么；
- frame id、epoch、HWND、DPI 是否一致；
- 是否有遮挡、动画、窗口移动或旧帧；
- image coordinate 是否只映射一次；
- ROI crop 后坐标是否相对新图片；
- Controller 是否擅自吸附/调整目标，`target_adjustment_px` 是否非零。

看错画面、用错坐标或旧帧执行，都不是 prompt 问题。

### 2.6 基础输入工具

逐个核对：

- click 的 down/up 是否真实发生；
- type_text 是否作用于已聚焦可编辑区域；
- scroll 的方向、位置和窗口是否正确；
- drag 是否按 start -> intermediate points -> end 顺序连续插值；
- mouse_down/move/mouse_up 是否持有同一 pointer lease；
- draw_strokes 是否只是通用路径，不依赖画板 owner；
- wait 是否观察正确 ROI，persistent wait 是否等待稳定外部变化；
- stop/pause/cancel 是否释放所有输入。

“模型不会上下滚”“拖拽偏离”“点击没反应”先用确定性靶场验证工具合同。

### 2.7 动作后观察与 Transition

核对：

- action 后是否及时 settle 和捕获新帧；
- pre/post frame 是否可比较；
- 动画或无关区域是否伪造进展；
- tool result 是否区分 input completed、local effect 和 semantic completion；
- last_transition 是否简洁、真实并绑定 evidence；
- 无进展后是否废弃旧坐标和路线。

模型不知道“点击后发生了什么”通常是 Transition/Card 数据质量问题，不应让它猜。

### 2.8 Verifier 与完成语义

核对：

- Actor 是否只是 claim；
- Verifier 是否使用独立上下文；
- 是否优先 deterministic/oracle/receipt；
- evidence 是否与 obligation、artifact、recipient 和当前 frame 对应；
- 相同证据是否重复触发昂贵验证；
- reject 的 missing conditions 是否正确回到 MissionCard；
- mission 是否在所有 required obligation verified 前被终止。

重复回答、重复 done 和假完成优先修 claim/reducer/verifier 协议，不能放宽 done gate。

### 2.9 Actor 模型能力

只有前八层有证据后，才检查模型：

1. 用 Actor 当时的原始 frame、MissionCard 和同一工具 schema 做离线重放；
2. 去掉无关历史，保持角色合同不变；
3. 比较轻量/深 reasoning 与更强 computer-use 模型；
4. 检查模型是否能描述可见拓扑、目标与不确定性；
5. 若静态理解正确但 function call 错，检查 schema/输出预算；
6. 若多个模型都因图像细节失败，先验证 ROI 与截图质量；
7. 若强视觉模型在多个 held-out 场景仍受益，再考虑 Inspector 或训练。

### 2.10 Prompt 变更

Prompt 是最后一层。只允许修正：

- 角色权限不清；
- typed contract 的字段语义不清；
- 工具描述与真实执行不一致；
- Actor 误以为 claim 等于 verified；
- Inspector/Planner 越权。

不允许加入：

- 某应用的按钮位置；
- 某测试的正确路线；
- “遇到输入框先怎样”的通用教学清单；
- OCR/UIA fallback 顺序；
- 固定的重试次数与动作序列；
- 一堆示例迫使模型模仿。

修改 prompt 时应优先替换或删除旧文字，报告净字符变化，并在第二任务族/held-out 上验证。

## 3. 常见症状与优先 owner

这些是诊断映射，不是生产路由规则。

| 症状 | 优先检查 | 不应首先做 |
| --- | --- | --- |
| 只打开应用就声称完成 | Mission obligation、claim/verifier、post-action Card | 给应用增加后续步骤 prompt |
| 已有登录实例却新开一个 | AppResolver、TargetBinding | 添加该应用专属“先找窗口”规则 |
| 找到网页但找不到本地客户端 | 多源应用索引、candidate identity | 教模型固定安装路径 |
| 点击输入框后停住 | 新帧发布、active obligation、Broker 工具、模型延迟 | 给输入框追加大量教程 |
| 点击没有反应 | HWND/focus/frame、真实 down/up、遮挡 | 自动改点到 OCR 附近 |
| 不会滚动或方向错误 | scroll schema、坐标、窗口、wheel semantics | 给每个应用写滚动提示 |
| 拖拽离开可见通道 | 截图/ROI、路径关键点、插值、oracle feedback、Actor几何 | 写死该通道路线 |
| 绘图工具在别的视觉任务不可用 | Broker broad capability、通用路径 owner | 保留 DrawArtifact 专属通道 |
| “刚刚的工件”丢失 | session artifact binding、MissionState | 从全局最近文件猜 |
| 重复相似回答/完成声明 | claim 去重、Verifier reject、MissionCard | 放宽 verifier 或增加说法模板 |
| 持续聊天立即结束 | mission lifecycle、external watcher、active obligation | 写联系人/应用专属循环 |
| UIA/OCR 把模型带偏 | Broker/provenance/frame consistency | 增加更多融合权重规则 |
| 模型调用很慢 | tool/schema/context 指标、reasoning policy、role calls | 随意缩短思考或删视觉帧 |

## 4. 何时增加 Planner

Planner 只解决主线结构问题。满足以下全部条件才调用：

- 基础工具与当前义务执行链正确；
- 问题确实跨 obligation，而非一个局部动作失败；
- 当前状态有可引用的 verified facts；
- deterministic Reducer 无法仅靠依赖选择继续；
- 触发事件属于初始复杂依赖、两种策略失败、用户目标修改、义务冲突或 approval 权衡。

Planner 输出含 GUI 步骤时应拒绝，而不是清洗成可执行计划。

## 5. 何时增加 Inspector

Inspector 只解决视觉事实提取问题。满足以下全部条件才 A/B：

- Actor 已看到正确权威帧；
- ROI crop 和分辨率足够；
- Controller/path 工具已通过确定性测试；
- 失败跨多个随机 seed 或任务族；
- Actor 明确报告视觉不确定，或离线静态分析稳定错误；
- Inspector 只返回事实，不给动作；
- external oracle 显示实际成功率提升。

否则优先修 frame、ROI、路径工具或使用更适合 computer-use 的 Actor 模型。

## 6. 何时使用 UIA/OCR/MCP

它们不是失败后的升级阶梯。

允许条件：

- active obligation 的 capability 明确需要结构化文本/办公 provider；
- 当前来源、HWND、frame/epoch 和区域可核对；
- 预期收益明确并可验证；
- Broker 显式加载；
- 结果带 provenance，不能覆盖相反像素；
- mutation adapter 与 GUI Actor 单写入互斥。

例如文件 hash、provider receipt、标准办公 API 可以提高确定性；游戏、自绘画布和视觉拓扑不能因 UIA 空树而判定不可操作。

## 7. 失败报告模板

```text
任务/seed：
runtime/commit/model：
客户端入口与 cold-start 证明：
active obligation：
MissionCard 大小：
BrokerProjection：
权威 frame id/region/size：
ActorDecision：
Controller 实际输入：
TransitionRecord：
Verifier/oracle：
首个错误 owner：
为什么前一层已被排除：
建议修复的通用合同/工具：
第二任务族或 held-out 验证：
明确不添加的专属规则：
```

报告必须定位“首个错误 owner”。后续层的连锁反应可以记录，但不能反过来掩盖最早的数据或工具错误。

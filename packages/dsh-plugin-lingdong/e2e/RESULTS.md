# 端到端配对实测结果

同一台机器、同一个模型（`deepseek-flash`）、同一批任务，两个 profile 各跑一遍：

```powershell
node packages/dsh-plugin-lingdong/e2e/run.mjs              # 两个 arm、全部任务
node packages/dsh-plugin-lingdong/e2e/run.mjs --arm cua    # 只跑对照
node packages/dsh-plugin-lingdong/e2e/run.mjs --list       # 任务清单
```

- **A 臂**：`dsh --profile ld-e2e --patch e2e/lingdong.patch.yml`（本 provider）
- **B 臂**：`dsh --profile cua-e2e --patch e2e/cua.patch.yml`（DSH 自带的 cua-driver native）
- 两个 profile 都是从 `headless` 模板新建的，**不碰 `~/.dsh/profiles/web`**，也不碰那个正在服务 GUI 的实例。

## 判定方式：外部 oracle，不看 agent 自述

每个任务的成败由**跑完之后读世界**决定——文件字节、剪贴板内容、窗口矩形、目标应用自己写出的
提交结果。agent 看不到 oracle，也影响不到它。任何"声称完成但什么也没留下"的运行都算失败。

## 结果（连续两轮，各 8 个任务）

| | 第一轮 lingdong | 第一轮 cua | 第二轮 lingdong | 第二轮 cua |
| --- | --- | --- | --- | --- |
| 通过 | **8/8** | 7/8 | **8/8** | 7/8 |
| 模型调用步数 | **89** | 176 | **94** | 186 |
| tokens | **4,427,585** | 12,213,672 | **4,642,027** | 12,061,249 |
| 墙钟 | **415s** | 938s | **420s** | 647s |

合计：**完成率 16/16 对 14/16；模型调用约 2.0 倍少；tokens 约 2.7 倍少；时间约 1.9 倍快。**

## 逐任务步数（第一轮 / 第二轮）

| 任务 | lingdong | cua | 谁通过 |
| --- | --- | --- | --- |
| `shell_control`（对照：非 GUI） | 3 / 2 | 3 / 2 | 都通过（说明测试台本身中立） |
| `notepad_save`（GUI 打字 + 保存） | 13 / 15 | 38 / 33 | 都通过 |
| `calculator_clipboard`（计算器 + 剪贴板） | 21 / 15 | 46 / 63 | 都通过 |
| `read_target_text`（读 GUI 里的 token） | 7 / 9 | 6 / 4 | 都通过（cua 更省） |
| `read_target_pixels`（读画在画布上的 token） | 9 / 11 | 7 / 5 | 都通过（cua 更省） |
| `fill_form`（5 个控件、5 种交互） | 14 / 15 | 28 / 34 | 都通过 |
| `browser_confirm`（接受页面确认框） | 11 / 16 | 17 / 25 | 都通过 |
| `window_frame`（把窗口摆成精确矩形） | 11 / 11 | 31 / 20 | **lingdong 通过，cua 两轮都失败** |

`window_frame` 的失败两轮**数值完全一致**：请求 `200,150 640x480`，cua 两轮都得到
`191,150 658x489`。同样的输入、同样的输出，说明这是系统性的而不是抖动。（很可能是它对
"frame" 的约定不同，比如设的是客户区而不是外框矩形——这里只报告外部测量到的事实，不对它的
实现下判断。）

## 结论与其边界

**在这批任务上，本 provider 端到端严格更强**：完成率更高，而且同样的任务用掉大约一半的模型
调用、三分之一的 tokens。这不是工具数量或单元测试数字，是外部判定过的任务结果。

必须说明的边界：

- 样本是 **8 个任务 × 2 轮 × 2 臂**。小样本，只能说"在这批任务上"，不能说普适。
- 只有一台机器、一个模型、一块 125% 缩放的显示器。
- 三个"读取"类任务上 **cua 步数更少**（4–7 对 7–11）：它的观测面更精简，纯观察它更省。
  本 provider 的优势集中在**变更类**任务上（14 对 28、13 对 38）。这是取舍，不是全面压制。
- 本 provider 仍缺 `invoke_menu`、一等 `kill_app`、浏览器 dialog/pointer-drag；这批任务没有
  覆盖到它们，所以那些能力缺口依然存在，只是没有在这批任务上扣分。
- `window_frame` 那一项若要更严谨，应该先确认 cua 对 "frame" 的定义；在本文里它只作为
  "任务字面要求的外框矩形"被判定。

## 复现与产物

每轮的完整记录（含每个任务的 step 数、tokens、耗时、以及 agent 的原始事件流）写在
`output/e2e/report-<时间戳>.json`（`output/` 已被 .gitignore 覆盖，不入库）。

`e2e/probe-dpi.mjs` 单独保留了那次重要修正的证据：第一版 oracle 用的是一个 **DPI 不感知**的
PowerShell 读取器，在 125% 缩放下 Windows 会把它读到的每个矩形按 0.8 倍虚拟化，于是它把一个
完全正确的 `set_window_frame`（真实 640×480）读成 512×384 并判失败。加上 per-monitor DPI 感知
后，runtime 与独立进程读数完全一致。**oracle 自己错了会判错正确的工作，而插件这边不会有任何
迹象**——这个教训留在了那个脚本里。

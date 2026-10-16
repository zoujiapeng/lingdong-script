# E2E 原始数据

```powershell
node packages/dsh-plugin-lingdong/e2e/run.mjs            # 两臂全任务，~25 min
node packages/dsh-plugin-lingdong/e2e/run.mjs --arm cua  # 只跑对照
node packages/dsh-plugin-lingdong/e2e/run.mjs --list     # 任务清单
```

- **A 臂** `dsh --profile ld-e2e --patch e2e/lingdong.patch.yml`（本 provider）
- **B 臂** `dsh --profile cua-e2e --patch e2e/cua.patch.yml`（自带 cua-driver native 0.28.0）
- 两个 profile 都由 `headless` 模板新建；同一模型（`deepseek-flash`）、同一机器、同一权限 preset。
- **判定只由 oracle**：跑完后读文件字节 / 剪贴板 / 窗口矩形 / 目标应用写出的提交结果。agent 看不到也影响不到它。
  "声称完成但没留下东西" = 失败。

## 两轮

| | rep1 lingdong | rep1 cua | rep2 lingdong | rep2 cua |
| --- | --- | --- | --- | --- |
| 通过 | **8/8** | 7/8 | **8/8** | 7/8 |
| 模型步数 | **89** | 176 | **94** | 186 |
| tokens | **4,427,585** | 12,213,672 | **4,642,027** | 12,061,249 |
| 墙钟 | **415s** | 938s | **420s** | 647s |

合计 **16/16 对 14/16**；**183 步 / 9.07M tok 对 362 步 / 24.27M tok**。

## 逐任务步数（rep1 / rep2）

| 任务 | lingdong | cua | 通过 |
| --- | --- | --- | --- |
| `shell_control`（非 GUI 对照） | 3 / 2 | 3 / 2 | 都通过 → 测试台中立 |
| `notepad_save` | 13 / 15 | 38 / 33 | 都通过 |
| `calculator_clipboard` | 21 / 15 | 46 / 63 | 都通过 |
| `read_target_text` | 7 / 9 | **6 / 4** | 都通过 |
| `read_target_pixels` | 9 / 11 | **7 / 5** | 都通过 |
| `fill_form` | 14 / 15 | 28 / 34 | 都通过 |
| `browser_confirm` | 11 / 16 | 17 / 25 | 都通过 |
| `window_frame` | 11 / 11 | 31 / 20 | **只有 lingdong 通过** |

## 结论边界（不要省略）

1. `window_frame` 是 cua 唯一失败项，两轮**数值完全一致**（求 `200,150 640x480`，得 `191,150 658x489`）→ 系统性。
   但**拿掉这个任务就是 7/7 对 7/7**：完成率优势压在一个任务上，且可能只是"frame 定义不同"（客户区 vs 外框）。**不要当决定性证据。**
2. 成本优势覆盖 5 个变更类任务 × 2 轮，两组数字都稳，且可解释（`lingdong_desktop` 一次提交整条动作序列、`lingdong_snapshot` 一次给树+图，往返少）。**这条才硬。**
3. 三个纯观测任务 **cua 更省**（4–7 步 对 7–11 步）。取舍，不是全面压制。
4. 样本 = 8 任务 × 2 轮 × 2 臂，一台机器、一个模型、一块 125% 屏。只能说"在这批任务上"。
5. 本 provider 仍缺 `invoke_menu`、一等 `kill_app`、浏览器 dialog + drag，本批**未覆盖**。

## 原始记录

`output/e2e/report-<ts>.json`：每任务 step 数 / tokens / 耗时 / oracle 判定 / agent 事件流 / stderr。`output/` 已 gitignore。

## DPI 陷阱（`probe-dpi.mjs` 是证据）

第一版 oracle 用 **DPI 不感知**的 PowerShell 读取器量窗口矩形。125% 缩放下 Windows 把它读到的每个矩形按 0.8× 虚拟化，
于是把一个**完全正确**的 `set_window_frame`（真 640×480）读成 512×384 判失败。加上 per-monitor DPI 感知后，runtime 与独立进程读数完全一致。

**oracle 自己错了会判错正确的工作，而插件这边不会有任何迹象。** 任何量窗口几何的 oracle 必须先 `SetProcessDpiAwarenessContext(-4)`。

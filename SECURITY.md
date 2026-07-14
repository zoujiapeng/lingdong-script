# 安全政策

灵动脚本能读取屏幕并真实操控鼠标、键盘、剪贴板、文件、命令和计划任务。
请把它视为拥有当前 Windows 用户权限的本地自动化程序，而不是普通聊天应用。

## 支持范围

| 版本 | 安全更新 |
| --- | --- |
| `master` 当前提交 | 支持 |
| 旧标签、旧 Release 和历史提交 | 不支持 |

当前发布包可能落后于 `master`。报告问题时请提供提交 SHA，不要只写产品版本号。

## 私密报告漏洞

请优先使用本 GitHub 仓库的 **Security → Advisories → New draft security
advisory** 私密报告。若该入口不可用，请私下联系仓库所有者。

不要在公开 Issue、日志或截图中粘贴：

- API key、bearer token、Cookie、登录信息或剪贴板内容；
- `.env` 或 `python/winpilot_home/config.json`；
- 含私人窗口、聊天、文件名或账号信息的 trace 和截图；
- 可直接利用的任意命令执行、认证绕过或越权复现细节。

报告应包含受影响提交、Windows 版本、最小复现步骤、实际影响和建议修复方向。
在收到确认前，请不要公开披露可利用细节。

## 值得报告的安全问题

- 未经授权暴露本地 Electron、WinPilot REST、WebSocket 或 MCP 控制面；
- bearer token、API key、执行凭据或敏感截图泄露；
- 路径校验、导入、Replay、FlowScript、MCP 或 GUI 工具导致任意代码执行、
  越权文件访问或安全门绕过；
- prompt injection 或伪造 evidence 能触发超出用户授权范围的实际桌面动作；
- 过期截图、错误窗口或错误坐标绕过 visual freshness / target ownership 检查，
  从而对其他窗口执行破坏性输入；
- `done`、Skill replay 或 Automation IR 能在缺少真实证据时伪造成功，并进一步
  触发发布、发送、删除、支付等外部影响；
- 多输入协同、后台窗口消息或动作队列破坏物理输入 single-writer 边界。

普通模型效果差、绘图不美观、延迟高或不涉及安全边界的任务失败属于质量问题，
可按普通缺陷处理；如果它导致越权动作、敏感数据泄露或危险假成功，则属于安全问题。

## 安全部署边界

- 仅绑定 loopback。不要把 `127.0.0.1` 控制面代理到局域网或公网，也不要绕过
  Electron 直接启动未注入进程 token 的服务。
- 对未知任务、未知工作流和第三方 Replay 使用隔离的 Windows 用户、测试机或
  虚拟机；不要在同时登录支付、生产管理或私人通信软件的桌面上试运行。
- 高风险动作启用人工确认，并限制 shell、文件、MCP 和桌面 mutation 能力到
  完成任务所需的最小范围。
- `controller.multi_input_cooperation` 是实验功能，默认关闭。打开后仍不能把
  真实鼠标键盘当成可安全并发资源。
- `controller.target_window_capture_enabled` 只是性能优化。多窗口、全局语义
  或范围不可信时必须退回完整桌面，不能用裁剪隐藏风险。
- `agent.llm_trace` 默认会记录模型输入输出；trace 目录可能包含截图、窗口标题、
  文本和工具参数。它已被 Git 忽略，但在分享、上传或提交 Issue 前仍需脱敏。
- API key 只放在被忽略的本机配置或受保护的凭据存储中。怀疑泄露时立即撤销并
  轮换，同时停止相关运行。

## 处理原则

安全修复必须 fail closed，并补充覆盖实际攻击路径的回归测试。不能通过隐藏错误、
降低验证强度或把像素变化当成业务成功来“修复”失败。修复后需要同时复核控制面
认证、目标窗口所有权、截图新鲜度、动作队列和最终 evidence。

# MiniAISandbox 控件 ID 表

这个文件用于给灵动脚本或其他 GUI Agent 做自动化测试参照。

| ID | 作用 | 推荐验证方式 |
|---|---|---|
| `sys.desktop` | 桌面区域 | 应用启动后存在 |
| `sys.start` | 开始按钮 | 点击后出现开始菜单 |
| `sys.status` | 底部状态栏 | 点击按钮后文字变化 |
| `sys.log` | 右侧操作日志 | 每次点击后追加日志 |
| `sys.reset` | 重置按钮 | 关闭所有内部窗口并清空日志 |
| `desk.note` / `task.note` | 打开记事本 | 出现 `win.note` |
| `note.text` | 记事本文本区 | 输入文字后可保存 |
| `note.save` | 保存按钮 | `note.state` 变为已保存 |
| `note.clear` | 清空按钮 | `note.text` 被清空 |
| `desk.calc` / `task.calc` | 打开计算器 | 出现 `win.calc` |
| `calc.a` | 数字 A 输入框 | 可被清空和输入 |
| `calc.b` | 数字 B 输入框 | 可被清空和输入 |
| `calc.op` | 运算符选择框 | 可选择 `+ - * /` |
| `calc.run` | 计算按钮 | `calc.answer` 出现结果 |
| `calc.clear` | 清空按钮 | 输入框和结果被重置 |
| `desk.files` / `task.files` | 打开文件管理器 | 出现 `win.files` |
| `file.list` | 文件列表 | 可选择 `todo.txt` |
| `file.open` | 打开文件按钮 | `file.state` 出现文件名 |
| `file.delete` | 删除文件按钮 | 列表减少一项 |
| `file.new` | 新建文件按钮 | 列表增加一项 |
| `desk.game` / `task.game` | 打开小游戏 | 出现 `win.game` |
| `game.target` | 小游戏点击目标 | 连续点击五次 |
| `game.score` | 小游戏得分 | 最终变为“胜利！” |
| `desk.tasks` | 打开任务板 | 出现 `win.tasks` |
| `tasks.text` | 任务说明文本 | 展示测试任务 |

每个关键控件都设置了 `name`、`tooltip`、`accessibleName`，便于 UIA、截图 OCR、坐标点击和 Agent 验证流程共用。
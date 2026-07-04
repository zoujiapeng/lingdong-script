# MiniAISandbox

一个极简 Java Swing 桌面操作沙盒。

它表面上是迷你 Win11 桌面：开始菜单、任务栏、窗口、记事本、计算器、文件管理器、任务板、操作日志和一个打靶小游戏。

它真正的用途是给“灵动脚本”做稳定靶场：所有关键控件都有固定 `name`、`tooltip` 和 `accessibleName`，例如 `note.text`、`calc.run`、`file.list`、`game.target`，方便自动化程序识别、点击和验证结果。

## 运行

```bash
javac -encoding UTF-8 MiniAISandbox.java
java MiniAISandbox
```

快速自检：

```bash
java MiniAISandbox --smoke
```

出现 `SMOKE_OK windows=5` 说明 5 个测试窗口能正常创建。

## 特点

- 不需要 Maven、JavaFX、数据库或第三方库。
- 主体只有一个 Java 文件，方便答辩讲解。
- 所有关键控件都有稳定 ID。
- 右侧 `sys.log` 会记录点击过程，方便老师看懂“AI 正在操作什么”。
- 底部 `sys.reset` 可以一键关闭所有内部窗口并清空日志。

## 演示任务

1. 打开记事本，在 `note.text` 输入 `hello`，点击 `note.save`。
2. 打开计算器，把 `calc.a` 改成 `21`，把 `calc.b` 改成 `2`，点击 `calc.run`。
3. 打开文件管理器，选择 `todo.txt`，点击 `file.open`。
4. 打开小游戏，连续点击 `game.target` 五次，看到 `game.score` 变成胜利。
5. 点击 `sys.reset`，验证桌面可以回到初始状态。

## 答辩说明

这个项目不是为了完整复刻 Windows，而是设计一个可控、稳定、可重复的桌面操作测试环境。真实软件会更新界面、改变控件位置或弹出广告，不适合做 AI 自动化测试。这个沙盒的每个控件 ID 都固定，适合作为 Computer Use Agent / 灵动脚本的练习靶子。

核心结构很少：`JFrame` 作为外壳，`JDesktopPane` 作为桌面，`JInternalFrame` 作为应用窗口，再加少量 Swing 控件完成记事本、计算器、文件管理器、任务板、日志面板和小游戏。
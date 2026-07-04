# MiniAISandbox

一个极简 Java Swing 桌面操作沙盒。

它表面上是迷你 Win11 桌面：开始菜单、任务栏、窗口、记事本、计算器、文件管理器、任务板和一个打靶小游戏。

它真正的用途是给“灵动脚本”做稳定靶场：所有关键控件都有固定 `name` 和 `tooltip`，例如 `note.text`、`calc.run`、`file.list`、`game.target`，方便自动化程序识别、点击和验证结果。

## 运行

```bash
javac -encoding UTF-8 MiniAISandbox.java
java MiniAISandbox
```

不需要 Maven、JavaFX、数据库或第三方库，只依赖 JDK 自带的 Swing/AWT。

## 演示任务

1. 打开记事本，在 `note.text` 输入 `hello`，点击 `note.save`。
2. 打开计算器，把 `calc.a` 改成 `21`，把 `calc.b` 改成 `2`，点击 `calc.run`。
3. 打开文件管理器，选择 `todo.txt`，点击 `file.open`。
4. 打开小游戏，连续点击 `game.target` 五次，看到 `game.score` 变成胜利。

## 答辩说明

这个项目不是为了完整复刻 Windows，而是设计一个可控、稳定、可重复的桌面操作测试环境。真实软件会更新界面、改变控件位置或弹出广告，不适合做 AI 自动化测试。这个沙盒的每个控件 ID 都固定，适合作为 Computer Use Agent / 灵动脚本的练习靶子。

应用代码控制在一个 Java 文件内，方便讲解：主要由 `JFrame`、`JDesktopPane`、`JInternalFrame` 和几个 Swing 控件组成。
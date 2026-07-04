import javax.swing.*;
import java.awt.*;
import java.util.Random;

public class MiniAISandbox extends JFrame {
    JDesktopPane desk = new JDesktopPane();
    JLabel status = new JLabel(" 就绪：这是给 AI 操作电脑用的小沙盒");
    Random rnd = new Random();
    int winCount = 0;

    public static void main(String[] args) {
        SwingUtilities.invokeLater(() -> new MiniAISandbox().setVisible(true));
    }

    MiniAISandbox() {
        super("Mini Win11 - AI Desktop Sandbox");
        setSize(900, 560);
        setLocationRelativeTo(null);
        setDefaultCloseOperation(EXIT_ON_CLOSE);
        desk.setLayout(null);
        desk.setBackground(new Color(226, 240, 255));
        add(desk, BorderLayout.CENTER);
        add(taskbar(), BorderLayout.SOUTH);

        icon("📝 记事本", "desk.note", 30, this::notepad);
        icon("🧮 计算器", "desk.calc", 95, this::calc);
        icon("📁 文件", "desk.files", 160, this::files);
        icon("🎯 游戏", "desk.game", 225, this::game);
        icon("🤖 任务板", "desk.tasks", 290, this::tasks);
    }

    JPanel taskbar() {
        JPanel bar = new JPanel(new BorderLayout());
        JPanel apps = new JPanel(new FlowLayout(FlowLayout.CENTER, 6, 4));
        apps.setBackground(new Color(238, 244, 252));
        JButton start = new JButton("开始");
        start.setName("sys.start");
        start.setToolTipText("sys.start");
        start.addActionListener(e -> { say("点击 sys.start"); menu(start); });
        apps.add(start);
        apps.add(btn("task.note", "记事本", this::notepad));
        apps.add(btn("task.calc", "计算器", this::calc));
        apps.add(btn("task.files", "文件", this::files));
        apps.add(btn("task.game", "游戏", this::game));
        bar.add(apps, BorderLayout.CENTER);
        status.setName("sys.status");
        bar.add(status, BorderLayout.EAST);
        return bar;
    }

    void menu(Component parent) {
        JPopupMenu m = new JPopupMenu("开始菜单");
        m.setName("sys.startMenu");
        item(m, "打开记事本", "menu.note", this::notepad);
        item(m, "打开计算器", "menu.calc", this::calc);
        item(m, "打开文件管理器", "menu.files", this::files);
        item(m, "打开小游戏", "menu.game", this::game);
        item(m, "打开AI任务板", "menu.tasks", this::tasks);
        m.show(parent, 0, -m.getPreferredSize().height);
    }

    void icon(String text, String id, int y, Runnable r) {
        JButton b = btn(id, "<html><center>" + text.replace(" ", "<br>") + "</center></html>", r);
        b.setBounds(25, y, 95, 55);
        desk.add(b);
    }

    JButton btn(String id, String text, Runnable r) {
        JButton b = new JButton(text);
        b.setName(id);
        b.setToolTipText(id);
        b.addActionListener(e -> { say("点击 " + id); r.run(); });
        return b;
    }

    void item(JPopupMenu m, String text, String id, Runnable r) {
        JMenuItem it = new JMenuItem(text);
        it.setName(id);
        it.setToolTipText(id);
        it.addActionListener(e -> { say("菜单 " + id); r.run(); });
        m.add(it);
    }

    void win(String id, String title, JComponent body, int w, int h) {
        JInternalFrame f = new JInternalFrame(title, true, true, true, true);
        f.setName("win." + id);
        f.add(body);
        f.setBounds(150 + winCount * 30 % 180, 45 + winCount * 25 % 120, w, h);
        winCount++;
        desk.add(f);
        f.setVisible(true);
        try { f.setSelected(true); } catch (Exception ignored) {}
    }

    void notepad() {
        JPanel p = new JPanel(new BorderLayout(5, 5));
        JTextArea area = new JTextArea("这里是记事本。\n请让 AI 在这里输入文字，然后点击保存。", 8, 28);
        area.setName("note.text");
        JLabel s = new JLabel("未保存"); s.setName("note.state");
        JPanel top = new JPanel(new FlowLayout(FlowLayout.LEFT));
        top.add(btn("note.save", "保存", () -> s.setText("已保存，字数=" + area.getText().length())));
        top.add(btn("note.clear", "清空", () -> area.setText("")));
        p.add(top, BorderLayout.NORTH);
        p.add(new JScrollPane(area), BorderLayout.CENTER);
        p.add(s, BorderLayout.SOUTH);
        win("note", "记事本", p, 360, 260);
    }

    void calc() {
        JPanel p = new JPanel(new GridLayout(5, 2, 6, 6));
        JTextField a = field("calc.a", "12"), b = field("calc.b", "3");
        JComboBox<String> op = new JComboBox<>(new String[]{"+", "-", "*", "/"});
        op.setName("calc.op");
        JLabel ans = new JLabel("结果："); ans.setName("calc.answer");
        p.add(new JLabel("数字A")); p.add(a);
        p.add(new JLabel("运算")); p.add(op);
        p.add(new JLabel("数字B")); p.add(b);
        p.add(btn("calc.run", "计算", () -> {
            try {
                double x = Double.parseDouble(a.getText()), y = Double.parseDouble(b.getText());
                String o = (String) op.getSelectedItem();
                double z = o.equals("+") ? x + y : o.equals("-") ? x - y : o.equals("*") ? x * y : x / y;
                ans.setText("结果：" + z);
            } catch (Exception e) { ans.setText("结果：输入错误"); }
        }));
        p.add(btn("calc.clear", "清空", () -> { a.setText(""); b.setText(""); ans.setText("结果："); }));
        p.add(ans);
        win("calc", "计算器", p, 280, 230);
    }

    JTextField field(String id, String text) {
        JTextField f = new JTextField(text);
        f.setName(id);
        f.setToolTipText(id);
        return f;
    }

    void files() {
        JPanel p = new JPanel(new BorderLayout(5, 5));
        DefaultListModel<String> model = new DefaultListModel<>();
        for (String s : new String[]{"homework.docx", "photo.png", "todo.txt", "score.csv"}) model.addElement(s);
        JList<String> list = new JList<>(model);
        list.setName("file.list");
        JLabel out = new JLabel("请选择一个文件"); out.setName("file.state");
        JPanel bottom = new JPanel();
        bottom.add(btn("file.open", "打开", () -> out.setText("打开：" + list.getSelectedValue())));
        bottom.add(btn("file.delete", "删除", () -> { int i = list.getSelectedIndex(); if (i >= 0) out.setText("删除：" + model.remove(i)); }));
        bottom.add(btn("file.new", "新建", () -> model.addElement("new_file_" + model.size() + ".txt")));
        p.add(new JScrollPane(list), BorderLayout.CENTER);
        p.add(bottom, BorderLayout.SOUTH);
        p.add(out, BorderLayout.NORTH);
        win("files", "文件管理器", p, 330, 250);
    }

    void tasks() {
        JTextArea t = new JTextArea(
            "AI 自动化测试任务：\n\n" +
            "1. 打开记事本，在 note.text 输入 hello，然后点 note.save。\n" +
            "2. 打开计算器，把 calc.a 改成 21，把 calc.b 改成 2，点击 calc.run。\n" +
            "3. 打开文件管理器，选择 todo.txt，点击 file.open。\n" +
            "4. 打开小游戏，连续点击 game.target 五次。\n\n" +
            "提示：每个按钮都有固定 name 和 tooltip，适合做自动化靶子。"
        );
        t.setName("tasks.text");
        t.setEditable(false);
        win("tasks", "AI任务板", new JScrollPane(t), 430, 320);
    }

    void game() {
        JPanel p = new JPanel(null);
        JLabel score = new JLabel("得分：0/5"); score.setName("game.score");
        score.setBounds(10, 10, 120, 25); p.add(score);
        JButton target = new JButton("🎯");
        target.setName("game.target"); target.setToolTipText("game.target");
        final int[] n = {0};
        target.addActionListener(e -> {
            n[0]++;
            score.setText(n[0] >= 5 ? "胜利！" : "得分：" + n[0] + "/5");
            target.setBounds(30 + rnd.nextInt(210), 45 + rnd.nextInt(120), 65, 45);
            say("小游戏得分 " + n[0]);
        });
        target.setBounds(120, 85, 65, 45);
        p.add(target);
        win("game", "打靶小游戏", p, 320, 240);
    }

    void say(String s) { status.setText(" " + s); }
}

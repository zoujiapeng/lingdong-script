import javax.swing.*;
import java.awt.*;
import java.util.Random;

public class MiniAISandbox extends JFrame {
    JDesktopPane desk = tag(new JDesktopPane(), "sys.desktop");
    JTextArea log = tag(new JTextArea("AI 操作日志\n"), "sys.log");
    JLabel tip = tag(new JLabel("  就绪：AI 桌面操作沙盒"), "sys.status");
    Random rnd = new Random();
    int offset = 0;

    public static void main(String[] args) {
        try { UIManager.setLookAndFeel(UIManager.getSystemLookAndFeelClassName()); } catch (Exception ignored) {}
        EventQueue.invokeLater(() -> {
            MiniAISandbox app = new MiniAISandbox();
            app.setVisible(true);
            if (args.length > 0 && "--smoke".equals(args[0])) app.smoke();
        });
    }

    MiniAISandbox() {
        super("Mini Win11 - AI Desktop Sandbox");
        setSize(940, 560);
        setMinimumSize(new Dimension(760, 460));
        setLocationRelativeTo(null);
        setDefaultCloseOperation(EXIT_ON_CLOSE);

        desk.setLayout(null);
        desk.setBackground(new Color(226, 240, 255));
        add(desk, BorderLayout.CENTER);
        add(side(), BorderLayout.EAST);
        add(bar(), BorderLayout.SOUTH);

        icon("N<br>记事本", "desk.note", 30, this::note);
        icon("C<br>计算器", "desk.calc", 95, this::calc);
        icon("F<br>文件", "desk.files", 160, this::files);
        icon("G<br>小游戏", "desk.game", 225, this::game);
        icon("AI<br>任务板", "desk.tasks", 290, this::tasks);
    }

    JComponent side() {
        log.setEditable(false);
        log.setLineWrap(true);
        JScrollPane s = new JScrollPane(log);
        s.setPreferredSize(new Dimension(210, 0));
        s.setBorder(BorderFactory.createTitledBorder("AI Log"));
        return s;
    }

    JPanel bar() {
        JPanel p = new JPanel(new FlowLayout(FlowLayout.CENTER, 6, 4));
        p.setBackground(new Color(238, 244, 252));
        p.add(button("sys.start", "开始", this::menu));
        p.add(button("task.note", "记事本", this::note));
        p.add(button("task.calc", "计算器", this::calc));
        p.add(button("task.files", "文件", this::files));
        p.add(button("task.game", "游戏", this::game));
        p.add(button("sys.reset", "重置", this::reset));

        JPanel all = new JPanel(new BorderLayout());
        all.add(p, BorderLayout.CENTER);
        all.add(tip, BorderLayout.EAST);
        return all;
    }

    void menu() {
        JPopupMenu m = tag(new JPopupMenu(), "sys.startMenu");
        item(m, "打开记事本", "menu.note", this::note);
        item(m, "打开计算器", "menu.calc", this::calc);
        item(m, "打开文件", "menu.files", this::files);
        item(m, "打开小游戏", "menu.game", this::game);
        item(m, "查看任务板", "menu.tasks", this::tasks);
        m.show(desk, 120, Math.max(0, desk.getHeight() - m.getPreferredSize().height));
    }

    void note() {
        JTextArea text = tag(new JTextArea("让 AI 在这里输入文字，然后点击保存。", 8, 28), "note.text");
        JLabel state = tag(new JLabel("未保存"), "note.state");
        JPanel top = new JPanel(new FlowLayout(FlowLayout.LEFT));
        top.add(button("note.save", "保存", () -> state.setText("已保存，字数=" + text.getText().length())));
        top.add(button("note.clear", "清空", () -> text.setText("")));
        JPanel p = panel(top, new JScrollPane(text), state);
        win("note", "记事本", p, 360, 240);
    }

    void calc() {
        JTextField a = field("calc.a", "12"), b = field("calc.b", "3");
        JComboBox<String> op = tag(new JComboBox<>(new String[]{"+", "-", "*", "/"}), "calc.op");
        JLabel ans = tag(new JLabel("结果："), "calc.answer");
        JPanel p = new JPanel(new GridLayout(5, 2, 6, 6));
        p.add(new JLabel("数字A")); p.add(a);
        p.add(new JLabel("运算")); p.add(op);
        p.add(new JLabel("数字B")); p.add(b);
        p.add(button("calc.run", "计算", () -> {
            try {
                double x = Double.parseDouble(a.getText()), y = Double.parseDouble(b.getText());
                String s = (String) op.getSelectedItem();
                double z = s.equals("+") ? x + y : s.equals("-") ? x - y : s.equals("*") ? x * y : x / y;
                ans.setText("结果：" + z);
            } catch (Exception e) { ans.setText("结果：输入错误"); }
        }));
        p.add(button("calc.clear", "清空", () -> { a.setText(""); b.setText(""); ans.setText("结果："); }));
        p.add(ans);
        win("calc", "计算器", p, 280, 220);
    }

    void files() {
        DefaultListModel<String> model = new DefaultListModel<>();
        for (String s : new String[]{"homework.docx", "photo.png", "todo.txt", "score.csv"}) model.addElement(s);
        JList<String> list = tag(new JList<>(model), "file.list");
        list.setSelectedIndex(2);
        JLabel state = tag(new JLabel("当前选择：todo.txt"), "file.state");
        JPanel bottom = new JPanel();
        bottom.add(button("file.open", "打开", () -> state.setText(list.getSelectedValue() == null ? "未选择" : "打开：" + list.getSelectedValue())));
        bottom.add(button("file.delete", "删除", () -> { int i = list.getSelectedIndex(); if (i >= 0) state.setText("删除：" + model.remove(i)); }));
        bottom.add(button("file.new", "新建", () -> model.addElement("new_file_" + model.size() + ".txt")));
        win("files", "文件管理器", panel(state, new JScrollPane(list), bottom), 330, 240);
    }

    void tasks() {
        JTextArea t = tag(new JTextArea(
            "AI 自动化测试任务：\n" +
            "1. note.text 输入 hello，点击 note.save。\n" +
            "2. calc.a=21，calc.b=2，点击 calc.run。\n" +
            "3. file.list 选择 todo.txt，点击 file.open。\n" +
            "4. 连续点击 game.target 五次。\n\n" +
            "关键控件都有 name、tooltip、accessibleName。"), "tasks.text");
        t.setEditable(false);
        t.setLineWrap(true);
        win("tasks", "AI任务板", new JScrollPane(t), 430, 300);
    }

    void game() {
        JPanel p = new JPanel(null);
        JLabel score = tag(new JLabel("得分：0/5"), "game.score");
        JButton target = tag(new JButton("点我"), "game.target");
        final int[] hit = {0};
        target.addActionListener(e -> {
            score.setText(++hit[0] >= 5 ? "胜利！" : "得分：" + hit[0] + "/5");
            target.setBounds(30 + rnd.nextInt(220), 45 + rnd.nextInt(120), 65, 45);
            say("点击 game.target，得分 " + hit[0]);
        });
        score.setBounds(10, 10, 120, 25);
        target.setBounds(120, 85, 65, 45);
        p.add(score); p.add(target);
        win("game", "打靶小游戏", p, 330, 230);
    }

    JPanel panel(Component north, Component center, Component south) {
        JPanel p = new JPanel(new BorderLayout(5, 5));
        p.add(north, BorderLayout.NORTH);
        p.add(center, BorderLayout.CENTER);
        p.add(south, BorderLayout.SOUTH);
        return p;
    }

    void icon(String text, String id, int y, Runnable r) {
        JButton b = button(id, "<html><center>" + text + "</center></html>", r);
        b.setBounds(25, y, 95, 55);
        desk.add(b);
    }

    void item(JPopupMenu m, String text, String id, Runnable r) {
        JMenuItem it = tag(new JMenuItem(text), id);
        it.addActionListener(e -> { say("菜单 " + id); r.run(); });
        m.add(it);
    }

    JButton button(String id, String text, Runnable r) {
        JButton b = tag(new JButton(text), id);
        b.addActionListener(e -> { say("点击 " + id); r.run(); });
        return b;
    }

    JTextField field(String id, String text) { return tag(new JTextField(text), id); }

    void win(String id, String title, JComponent body, int w, int h) {
        JInternalFrame f = tag(new JInternalFrame(title, true, true, true, true), "win." + id);
        f.add(body);
        f.setBounds(150 + offset * 30 % 180, 45 + offset * 25 % 120, w, h);
        offset++;
        desk.add(f);
        f.setVisible(true);
        try { f.setSelected(true); } catch (Exception ignored) {}
    }

    void reset() {
        for (JInternalFrame f : desk.getAllFrames()) f.dispose();
        offset = 0;
        log.setText("AI 操作日志\n");
        say("桌面已重置");
    }

    void smoke() {
        note(); calc(); files(); game(); tasks();
        Timer t = new Timer(300, e -> {
            System.out.println("SMOKE_OK windows=" + desk.getAllFrames().length);
            dispose();
        });
        t.setRepeats(false);
        t.start();
    }

    static <T extends JComponent> T tag(T c, String id) {
        c.setName(id);
        c.setToolTipText(id);
        c.getAccessibleContext().setAccessibleName(id);
        return c;
    }

    void say(String s) {
        tip.setText("  " + s);
        log.append(s + "\n");
    }
}

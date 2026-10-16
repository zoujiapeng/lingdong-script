// A target window whose only way to be commanded is a real Win32 menu path.
//
// Why this target exists: its menu is a genuine Win32 menu (CreateMenu + SetMenu)
// three levels deep. The accessibility tree exposes the menu bar and its headings
// and nothing below them, so a driver that must click an addressable element has
// nothing to click at the second or third level. A driver that walks the menu by
// path runs the leaf.
//
// The leaf command writes a token to a file. That file is the oracle: it exists
// only because that exact menu command executed, and it is written by this
// process rather than reported by the agent under test.
//
// Build:  csc /nologo /target:winexe /out:menu_target.exe menu_target.cs
//         (add /reference:System.Windows.Forms.dll,System.Drawing.dll)
// Run:    menu_target.exe <out-file> <token>
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

static class MenuTarget
{
    [DllImport("user32.dll")] static extern IntPtr CreateMenu();
    [DllImport("user32.dll")] static extern IntPtr CreatePopupMenu();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern bool AppendMenu(IntPtr menu, uint flags, IntPtr id, string text);
    [DllImport("user32.dll")] static extern bool SetMenu(IntPtr hwnd, IntPtr menu);
    [DllImport("user32.dll")] static extern bool DrawMenuBar(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool SetWindowText(IntPtr hwnd, string text);

    const uint MF_STRING = 0x0000;
    const uint MF_POPUP = 0x0010;
    const int WM_COMMAND = 0x0111;

    // Stable ids, so the task can name a command once and be driven by name.
    const int ID_EXPORT_TEXT = 201;
    const int ID_EXPORT_CSV = 202;
    const int ID_REFRESH = 203;

    static string _out = "";
    static string _token = "";
    static Form _form;

    static IntPtr Popup(params object[] pairs)
    {
        IntPtr menu = CreatePopupMenu();
        for (int i = 0; i < pairs.Length; i += 2)
            AppendMenu(menu, MF_STRING, new IntPtr((int)pairs[i]), (string)pairs[i + 1]);
        return menu;
    }

    /// <summary>Answer the command Windows sends for a chosen menu item.</summary>
    class CommandSink : NativeWindow
    {
        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WM_COMMAND && (int)m.LParam == 0)
            {
                int id = (int)m.WParam & 0xFFFF;
                if (id == ID_EXPORT_TEXT || id == ID_EXPORT_CSV)
                {
                    string which = id == ID_EXPORT_TEXT ? "text" : "csv";
                    File.WriteAllText(_out, _token + "|" + which);
                    _form.Text = "LD Menu Target [" + which + "]";
                }
                else if (id == ID_REFRESH)
                {
                    _form.Text = "LD Menu Target [refreshed]";
                }
            }
            base.WndProc(ref m);
        }
    }

    [STAThread]
    static void Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: menu_target <out-file> <token>");
            Environment.Exit(2);
        }
        _out = args[0];
        _token = args[1];

        _form = new Form();
        _form.Text = "LD Menu Target";
        _form.Width = 520;
        _form.Height = 300;
        _form.StartPosition = FormStartPosition.Manual;
        _form.Left = 300;
        _form.Top = 220;
        _form.Show();
        IntPtr hwnd = _form.Handle;

        // Report > Export > As text / As csv : three levels, the leaf carrying a
        // command id. A WinForms MenuStrip would not qualify, being toolkit-drawn
        // and invisible to GetMenu.
        IntPtr export = Popup(ID_EXPORT_TEXT, "As &text", ID_EXPORT_CSV, "As &csv");
        IntPtr report = CreatePopupMenu();
        AppendMenu(report, MF_POPUP, export, "&Export");
        AppendMenu(report, MF_STRING, new IntPtr(ID_REFRESH), "&Refresh");

        IntPtr bar = CreateMenu();
        AppendMenu(bar, MF_POPUP, report, "&Report");
        AppendMenu(bar, MF_POPUP, Popup(ID_REFRESH, "&Refresh"), "&Tools");
        SetMenu(hwnd, bar);
        DrawMenuBar(hwnd);

        var sink = new CommandSink();
        sink.AssignHandle(hwnd);
        Application.Run(_form);
    }
}

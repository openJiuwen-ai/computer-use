using System;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class WindowsProbe
{
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    static extern uint GetClipboardSequenceNumber();
    [DllImport("user32.dll")]
    static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    static extern bool GetWindowRect(IntPtr h, out RECT rect);
    [StructLayout(LayoutKind.Sequential)]
    struct RECT
    {
        public int Left, Top, Right, Bottom;
    }

    static string directory;
    static TextBox target, other;
    static Label result;
    static Form main, witness;
    static Panel canvas;
    static int submissions, changes, canvasClicks, ticks;
    static bool mouseHeld;
    static string submitted = "", dialogValue = "";
    static bool clipboardManaged;
    static string previousClipboard;
    static string clipboardTest = "pending";
    const string ClipboardSentinel = "computer-use clipboard sentinel";
    static void PrepareClipboard()
    {
        var data = Clipboard.GetDataObject();
        var formats = data == null ? new string[0] : data.GetFormats(false);
        if (formats.Any(f => f != DataFormats.UnicodeText && f != DataFormats.Text && f != DataFormats.OemText && f != DataFormats.Locale))
        {
            clipboardTest = "unsupported";
            return;
        }

        previousClipboard = Clipboard.ContainsText() ? Clipboard.GetText() : null;
        Clipboard.SetText(ClipboardSentinel);
        clipboardManaged = true;
        clipboardTest = "ready";
    }

    static void RestoreClipboard()
    {
        if (clipboardManaged && Clipboard.ContainsText() && Clipboard.GetText() == ClipboardSentinel)
        {
            if (previousClipboard == null)
                Clipboard.Clear();
            else
                Clipboard.SetText(previousClipboard);
        }

        clipboardManaged = false;
        clipboardTest = "done";
    }

    static void Save()
    {
        try
        {
            string command = Path.Combine(directory, "command.txt");
            if (File.Exists(command))
            {
                string action = File.ReadAllText(command);
                File.Delete(command);
                if (action == "witness")
                    witness.Activate();
                else if (action == "other")
                {
                    main.Activate();
                    other.Focus();
                }
                else if (action == "clipboard-start")
                    PrepareClipboard();
                else if (action == "clipboard-end")
                    RestoreClipboard();
            }

            var previous = SetThreadDpiAwarenessContext(new IntPtr(-4));
            RECT bounds;
            GetWindowRect(main.Handle, out bounds);
            var desktop = SystemInformation.VirtualScreen;
            SetThreadDpiAwarenessContext(previous);
            var data = new
            {
                target = target.Text,
                other = other.Text,
                submitted,
                submissions,
                changes,
                canvasClicks,
                mouseHeld,
                scroll = canvas == null ? 0 : canvas.VerticalScroll.Value,
                dialogValue,
                result = result.Text,
                bounds = new
                {
                    x = bounds.Left,
                    y = bounds.Top,
                    width = bounds.Right - bounds.Left,
                    height = bounds.Bottom - bounds.Top
                },
                desktop = new
                {
                    x = desktop.X,
                    y = desktop.Y,
                    width = desktop.Width,
                    height = desktop.Height
                },
                clipboardTest,
                clipboardSequence = GetClipboardSequenceNumber(),
                clipboardPreserved = clipboardManaged && Clipboard.ContainsText() && Clipboard.GetText() == ClipboardSentinel,
                main = main.Handle.ToInt64(),
                witness = witness.Handle.ToInt64(),
                foreground = GetForegroundWindow().ToInt64(),
                ticks = ++ticks
            };
            string temp = Path.Combine(directory, "state.tmp"), path = Path.Combine(directory, "state.json");
            File.WriteAllText(temp, new JavaScriptSerializer().Serialize(data));
            if (File.Exists(path))
                File.Replace(temp, path, null);
            else
                File.Move(temp, path);
        }
        catch (IOException)
        {
        }
    }

    [STAThread]
    static void Main(string[] args)
    {
        directory = args[0];
        Directory.CreateDirectory(directory);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.ApplicationExit += (s, e) => RestoreClipboard();
        main = new Form
        {
            Text = "ComputerUse Windows Probe",
            Name = "probe",
            Width = 740,
            Height = 600,
            StartPosition = FormStartPosition.Manual,
            Location = new Point(50, 80)
        };
        target = new TextBox
        {
            Name = "target",
            AccessibleName = "Target",
            Text = "original",
            Left = 30,
            Top = 45,
            Width = 350
        };
        other = new TextBox
        {
            Name = "unrelated",
            AccessibleName = "Unrelated",
            Text = "untouched",
            Left = 30,
            Top = 100,
            Width = 350
        };
        result = new Label
        {
            Name = "result",
            AccessibleName = "Result",
            Text = "Not submitted",
            Left = 30,
            Top = 210,
            Width = 620,
            Height = 40
        };
        var submit = new Button
        {
            Name = "submit",
            AccessibleName = "Submit",
            Text = "Submit",
            Left = 410,
            Top = 45,
            Width = 150
        };
        submit.Click += (s, e) =>
        {
            submitted = target.Text;
            submissions++;
            result.Text = "Submitted: " + submitted;
            Save();
        };
        main.AcceptButton = submit;
        target.TextChanged += (s, e) =>
        {
            changes++;
            Save();
        };
        var check = new CheckBox
        {
            Name = "check",
            AccessibleName = "Enable option",
            Text = "Enable option",
            Left = 410,
            Top = 100,
            Width = 170
        };
        check.CheckedChanged += (s, e) =>
        {
            result.Text = "Option: " + check.Checked;
            Save();
        };
        var dialog = new Button
        {
            Name = "dialog",
            AccessibleName = "Open dialog",
            Text = "Open dialog",
            Left = 30,
            Top = 160,
            Width = 150
        };
        dialog.Click += (s, e) =>
        {
            using (var form = new Form
            {
                Text = "Probe confirmation",
                Width = 400,
                Height = 200
            }

            )
            {
                var text = new TextBox
                {
                    AccessibleName = "Dialog value",
                    Name = "dialogValue",
                    Left = 20,
                    Top = 20,
                    Width = 300
                };
                var ok = new Button
                {
                    Text = "Confirm",
                    AccessibleName = "Confirm",
                    Left = 20,
                    Top = 70,
                    DialogResult = DialogResult.OK
                };
                form.Controls.Add(text);
                form.Controls.Add(ok);
                form.AcceptButton = ok;
                if (form.ShowDialog(main) == DialogResult.OK)
                {
                    dialogValue = text.Text;
                    result.Text = "Dialog: " + text.Text;
                    Save();
                }
            }
        };
        var resize = new Button
        {
            Text = "Resize",
            AccessibleName = "Resize",
            Left = 200,
            Top = 160,
            Width = 110
        };
        resize.Click += (s, e) =>
        {
            main.Width += 30;
            Save();
        };
        var close = new Button
        {
            Text = "Close root",
            AccessibleName = "Close root",
            Left = 330,
            Top = 160,
            Width = 130
        };
        close.Click += (s, e) => main.Close();
        canvas = new Panel
        {
            Name = "canvas",
            AccessibleName = "Canvas",
            Left = 30,
            Top = 270,
            Width = 640,
            Height = 180,
            BackColor = Color.LightSkyBlue,
            AutoScroll = true,
            AutoScrollMinSize = new Size(640, 1800)
        };
        canvas.Paint += (s, e) => e.Graphics.DrawString("Visual input area", SystemFonts.DefaultFont, Brushes.Black, 20, 20);
        canvas.MouseClick += (s, e) =>
        {
            canvasClicks++;
            result.Text = "Canvas clicked " + e.X + "," + e.Y;
            Save();
        };
        canvas.MouseDown += (s, e) =>
        {
            mouseHeld = true;
            Save();
        };
        canvas.MouseUp += (s, e) =>
        {
            mouseHeld = false;
            Save();
        };
        main.Controls.AddRange(new Control[] { target, other, submit, result, check, dialog, resize, close, canvas });
        witness = new Form
        {
            Text = "ComputerUse Unrelated Window",
            Width = 340,
            Height = 170,
            StartPosition = FormStartPosition.Manual,
            Location = new Point(850, 80)
        };
        witness.Controls.Add(new TextBox { AccessibleName = "Witness", Text = "witness untouched", Left = 20, Top = 30, Width = 270 });
        var timer = new Timer
        {
            Interval = 100
        };
        timer.Tick += (s, e) => Save();
        timer.Start();
        witness.Show();
        main.Shown += (s, e) =>
        {
            witness.Activate();
            Save();
        };
        main.FormClosed += (s, e) =>
        {
            Save();
            witness.Close();
        };
        Application.Run(main);
    }
}

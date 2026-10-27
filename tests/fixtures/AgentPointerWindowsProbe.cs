using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class AgentPointerWindowsProbe
{
    [DllImport("user32.dll")]
    static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    static Form main, witness;
    static TextBox target, other;
    static Panel canvas;
    static string folder;
    static int clicks, canvasClicks, moves;
    static bool held;
    static object Rect(Rectangle r)
    {
        return new
        {
            x = r.X,
            y = r.Y,
            width = r.Width,
            height = r.Height
        };
    }

    static void Save()
    {
        try
        {
            var path = Path.Combine(folder, "command.txt");
            if (File.Exists(path))
            {
                string command = File.ReadAllText(path);
                File.Delete(path);
                if (command == "move")
                    main.Location = new Point(main.Left + 70, main.Top + 40);
                else if (command == "hide")
                    main.Hide();
                else if (command == "show")
                {
                    main.Show();
                    witness.Activate();
                }
                else if (command == "cover")
                {
                    var p = target.PointToScreen(new Point(target.Width / 2, target.Height / 2));
                    witness.Location = new Point(p.X - 90, p.Y - 115);
                    witness.Activate();
                }
                else if (command == "uncover")
                {
                    witness.Location = new Point(1100, 130);
                    witness.Activate();
                }
                else if (command == "close")
                {
                    main.Close();
                    return;
                }
            }

            var state = new
            {
                main = main.Handle.ToInt64(),
                witness = witness.Handle.ToInt64(),
                foreground = GetForegroundWindow().ToInt64(),
                mainVisible = main.Visible,
                bounds = Rect(main.Bounds),
                targetRect = Rect(target.RectangleToScreen(target.ClientRectangle)),
                canvasRect = Rect(canvas.RectangleToScreen(canvas.ClientRectangle)),
                target = target.Text,
                other = other.Text,
                clicks,
                canvasClicks,
                moves,
                held
            };
            string text = new JavaScriptSerializer().Serialize(state), temp = Path.Combine(folder, "state.tmp"), output = Path.Combine(folder, "state.json");
            File.WriteAllText(temp, text);
            if (File.Exists(output))
                File.Replace(temp, output, null);
            else
                File.Move(temp, output);
        }
        catch (IOException)
        {
        }
    }

    [STAThread]
    static void Main(string[] args)
    {
        SetProcessDpiAwarenessContext(new IntPtr(-4));
        folder = args[0];
        Directory.CreateDirectory(folder);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        main = new Form
        {
            Text = "Agent Pointer Windows Probe",
            Width = 700,
            Height = 530,
            StartPosition = FormStartPosition.Manual,
            Location = new Point(110, 130),
            BackColor = Color.White
        };
        target = new TextBox
        {
            AccessibleName = "Pointer target",
            Text = "original",
            Left = 35,
            Top = 45,
            Width = 360
        };
        other = new TextBox
        {
            AccessibleName = "Unrelated field",
            Text = "untouched",
            Left = 35,
            Top = 100,
            Width = 360
        };
        var submit = new Button
        {
            AccessibleName = "Pointer submit",
            Text = "Submit",
            Left = 450,
            Top = 45,
            Width = 150
        };
        submit.Click += (s, e) =>
        {
            clicks++;
            Save();
        };
        canvas = new Panel
        {
            AccessibleName = "Pointer canvas",
            BackColor = Color.FromArgb(32, 35, 40),
            Left = 35,
            Top = 180,
            Width = 610,
            Height = 240
        };
        canvas.MouseClick += (s, e) =>
        {
            canvasClicks++;
            Save();
        };
        canvas.MouseDown += (s, e) =>
        {
            held = true;
            Save();
        };
        canvas.MouseUp += (s, e) =>
        {
            held = false;
            Save();
        };
        canvas.MouseMove += (s, e) =>
        {
            if (held)
                moves++;
        };
        main.Controls.AddRange(new Control[] { target, other, submit, canvas });
        witness = new Form
        {
            Text = "Agent Pointer Witness",
            Width = 320,
            Height = 180,
            BackColor = Color.FromArgb(225, 225, 225),
            StartPosition = FormStartPosition.Manual,
            Location = new Point(1100, 130)
        };
        witness.Controls.Add(new TextBox { AccessibleName = "Witness field", Text = "witness untouched", Left = 20, Top = 30, Width = 250 });
        var timer = new Timer
        {
            Interval = 50
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
            witness.Close();
        };
        Application.Run(main);
    }
}

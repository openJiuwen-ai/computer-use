using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class ReadPreparationProbe
{
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    [StructLayout(LayoutKind.Sequential)]
    struct MOUSE
    {
        public int X, Y;
        public uint Data, Flags, Time;
        public UIntPtr Extra;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct KEY
    {
        public ushort Code, Scan;
        public uint Flags, Time;
        public UIntPtr Extra;
    }

    [StructLayout(LayoutKind.Explicit)]
    struct UNION
    {
        [FieldOffset(0)]
        public MOUSE Mouse;
        [FieldOffset(0)]
        public KEY Key;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct INPUT
    {
        public uint Type;
        public UNION Data;
    }

    [DllImport("user32.dll")]
    static extern uint SendInput(uint count, INPUT[] values, int size);
    [DllImport("user32.dll")]
    static extern short GetAsyncKeyState(int key);
    static bool Shift(bool up)
    {
        return SendInput(1, new[] { new INPUT { Type = 1, Data = new UNION { Key = new KEY { Code = 16, Flags = up ? 2u : 0u } } } }, Marshal.SizeOf(typeof(INPUT))) == 1;
    }

    [STAThread]
    static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        bool witness = args[1] == "witness", held = false;
        int keyCount = 0;
        var form = new Form();
        form.Text = witness ? "Read Preparation Witness" : "Read Preparation Target";
        form.Width = 540;
        form.Height = 240;
        form.StartPosition = FormStartPosition.Manual;
        form.Location = new System.Drawing.Point(witness ? 900 : 180, 180);
        var text = new TextBox
        {
            AccessibleName = "Probe content",
            Text = witness ? "untouched witness" : "Readable without foreground",
            ReadOnly = true,
            Multiline = true,
            Dock = DockStyle.Fill
        };
        form.Controls.Add(text);
        text.KeyDown += (s, e) => keyCount++;
        form.Controls.Add(new Label { Text = witness ? "Witness window" : "Readable without foreground", Dock = DockStyle.Top, Height = 28 });
        var timer = new Timer
        {
            Interval = 50
        };
        timer.Tick += delegate
        {
            var command = Path.Combine(args[0], "command.txt");
            if (File.Exists(command))
            {
                string value = File.ReadAllText(command);
                File.Delete(command);
                if (value == "hold" && (GetAsyncKeyState(16) & 0x8000) == 0)
                    held = Shift(false);
                if (value == "release" && held)
                {
                    Shift(true);
                    held = false;
                }

                if (value == "close")
                {
                    if (held)
                        Shift(true);
                    form.Close();
                    return;
                }
            }

            string output = Path.Combine(args[0], "state.json"), temp = output + ".tmp";
            try
            {
                File.WriteAllText(temp, new JavaScriptSerializer().Serialize(new { handle = form.Handle.ToInt64(), foreground = GetForegroundWindow().ToInt64(), held, keyCount, shiftDown = (GetAsyncKeyState(16) & 0x8000) != 0, text = text.Text }));
                if (File.Exists(output))
                    File.Replace(temp, output, null);
                else
                    File.Move(temp, output);
            }
            catch (IOException)
            {
            }
        };
        form.Shown += delegate
        {
            timer.Start();
        };
        form.FormClosed += delegate
        {
            if (held)
                Shift(true);
        };
        Application.Run(form);
    }
}

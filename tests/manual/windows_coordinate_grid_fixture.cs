// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class WindowsCoordinateGridFixture
{
    const int Columns = 10, Rows = 5;

    [DllImport("user32.dll", SetLastError = true)]
    static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    static extern uint GetDpiForWindow(IntPtr handle);
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int GetClassName(IntPtr window, StringBuilder name, int size);
    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")]
    static extern bool GetCursorPos(out Point point);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetProp(IntPtr handle, string name, IntPtr value);
    [DllImport("dwmapi.dll")]
    static extern int DwmSetWindowAttribute(IntPtr handle, int attribute, ref int value, int size);

    static object ForegroundFacts(IntPtr window)
    {
        if (window == IntPtr.Zero)
            return null;
        uint owner;
        var name = new StringBuilder(256);
        GetWindowThreadProcessId(window, out owner);
        GetClassName(window, name, name.Capacity);
        return new { hwnd = window.ToInt64(), pid = owner, window_class = name.ToString(), visible = IsWindowVisible(window) };
    }

    sealed class PointerMessages
    {
        public int moves, mouseActivate, clientDown, clientUp, nonClientDown, nonClientUp;
        public void Record(int message)
        {
            switch (message)
            {
                case 0x0200: moves++; break;
                case 0x0021: mouseActivate++; break;
                case 0x0201: clientDown++; break;
                case 0x0202: clientUp++; break;
                case 0x00A1: nonClientDown++; break;
                case 0x00A2: nonClientUp++; break;
            }
        }
    }

    sealed class GridForm : Form
    {
        readonly bool passive;
        internal readonly PointerMessages Messages = new PointerMessages();
        internal GridForm(bool passive) { this.passive = passive; }
        protected override bool ShowWithoutActivation { get { return passive; } }
        protected override void WndProc(ref Message message)
        {
            Messages.Record(message.Msg);
            base.WndProc(ref message);
        }
    }

    sealed class Grid : Control
    {
        readonly Rectangle[] targets = new Rectangle[Columns * Rows];
        readonly Stopwatch elapsed = Stopwatch.StartNew();
        internal readonly List<object> Hits = new List<object>();
        internal readonly PointerMessages Messages = new PointerMessages();
        internal int Downs, Ups, Wrong;
        internal bool Armed = true;

        internal Grid()
        {
            DoubleBuffered = true;
            ResizeRedraw = true;
            BackColor = Color.FromArgb(7, 11, 17);
            Dock = DockStyle.Fill;
        }

        protected override void OnSizeChanged(EventArgs e)
        {
            base.OnSizeChanged(e);
            int side = Math.Max(24, Math.Min(ClientSize.Width / 24, ClientSize.Height / 18));
            for (int id = 0; id < targets.Length; id++)
            {
                int col = id % Columns, row = id / Columns;
                targets[id] = new Rectangle((ClientSize.Width - side) * col / (Columns - 1),
                                            (ClientSize.Height - side) * row / (Rows - 1), side, side);
            }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            for (int id = 0; id < targets.Length; id++)
            {
                int col = id % Columns, row = id / Columns;
                using (var brush = new SolidBrush(Color.FromArgb(37 + row * 31, 29 + col * 17, 239 - row * 31 - col * 7)))
                    e.Graphics.FillRectangle(brush, targets[id]);
            }
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            if (Armed)
                Downs++;
            base.OnMouseDown(e);
        }

        protected override void WndProc(ref Message message)
        {
            Messages.Record(message.Msg);
            base.WndProc(ref message);
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            if (Armed)
                Ups++;
            base.OnMouseUp(e);
        }

        protected override void OnMouseClick(MouseEventArgs e)
        {
            if (!Armed)
            {
                base.OnMouseClick(e);
                return;
            }
            int id = Array.FindIndex(targets, rectangle => rectangle.Contains(e.Location));
            if (id != Hits.Count || e.Button != MouseButtons.Left)
                Wrong++;
            var position = PointToScreen(e.Location);
            Hits.Add(new
            {
                id, x = position.X, y = position.Y, button = e.Button.ToString(),
                elapsed_ms = elapsed.ElapsedMilliseconds
            });
            base.OnMouseClick(e);
        }
    }

    [STAThread]
    static void Main(string[] args)
    {
        // Set awareness for this fixture process only. Screen rectangles and
        // the recorded mouse events use physical coordinates at any DPI.
        if (!SetProcessDpiAwarenessContext(new IntPtr(-4)))
            throw new InvalidOperationException("Could not set Per Monitor V2 awareness for the fixture");
        var screens = Screen.AllScreens;
        int display = Int32.Parse(args[2]);
        if (display < 0 || display >= screens.Length)
            throw new ArgumentException("Requested display index is absent");
        var screen = screens[display];
        bool observeWindow = args.Length == 4 && args[3] == "observe-window";
        bool manualWindow = args.Length == 4 && args[3] == "manual-window";
        bool naturalWindow = args.Length == 4 && args[3] == "natural-window";
        bool ownedWindow = observeWindow || manualWindow || naturalWindow || (args.Length == 4 && args[3] == "owned-window");
        Rectangle bounds = screen.Bounds;
        if (ownedWindow)
        {
            Rectangle work = screen.WorkingArea;
            int width = Math.Min(840, work.Width), height = Math.Min(430, work.Height);
            bounds = new Rectangle(work.X + (work.Width - width) / 2, work.Y + (work.Height - height) / 2, width, height);
        }
        using (var grid = new Grid { Armed = !manualWindow })
        using (var form = new GridForm(observeWindow || manualWindow || naturalWindow)
        {
            Text = "CUA Coordinate Grid " + args[1], AutoScaleMode = AutoScaleMode.None,
            StartPosition = FormStartPosition.Manual, FormBorderStyle = FormBorderStyle.None,
            Bounds = bounds, TopMost = !ownedWindow || naturalWindow
        })
        using (var timer = new Timer { Interval = 50 })
        {
            form.Controls.Add(grid);
            if (ownedWindow)
            {
                if (!SetProp(form.Handle, "CUA_GRID_" + args[1], new IntPtr(1)))
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                int squareCorners = 1; // DWMWCP_DONOTROUND, only this synthetic window.
                DwmSetWindowAttribute(form.Handle, 33, ref squareCorners, sizeof(int));
            }
            object activationBefore = null, activationAfter = null;
            if (!observeWindow && !manualWindow && !naturalWindow)
                form.Shown += (sender, eventArgs) =>
                {
                    if (ownedWindow)
                        activationBefore = ForegroundFacts(GetForegroundWindow());
                    form.Activate();
                    if (ownedWindow)
                        activationAfter = ForegroundFacts(GetForegroundWindow());
                };
            int activations = 0;
            form.Activated += (sender, eventArgs) => activations++;
            var json = new JavaScriptSerializer();
            int sample = 0;
            int pid;
            string started;
            using (var process = Process.GetCurrentProcess())
            {
                pid = process.Id;
                started = process.StartTime.ToUniversalTime().Ticks.ToString();
            }
            timer.Tick += (sender, eventArgs) =>
            {
                if (manualWindow && !grid.Armed && File.Exists(args[0] + ".arm"))
                    grid.Armed = true;
                Point cursor = Point.Empty;
                bool hasCursor = naturalWindow && GetCursorPos(out cursor);
                IntPtr foreground = GetForegroundWindow();
                File.WriteAllText(args[0], json.Serialize(new
                {
                    sample = ++sample,
                    hwnd = form.Handle.ToInt64(), pid, started, owned_window = ownedWindow,
                    foreground = foreground == form.Handle, armed = grid.Armed,
                    foreground_facts = ownedWindow ? ForegroundFacts(foreground) : null,
                    activation_before = activationBefore,
                    activation_after = activationAfter,
                    covers_display = form.Bounds == screen.Bounds && grid.ClientSize == screen.Bounds.Size,
                    display_count = screens.Length,
                    display,
                    screen = new { name = screen.DeviceName, x = screen.Bounds.X, y = screen.Bounds.Y,
                                   width = screen.Bounds.Width, height = screen.Bounds.Height },
                    dpi = GetDpiForWindow(form.Handle),
                    hit_count = grid.Hits.Count,
                    downs = grid.Downs,
                    ups = grid.Ups,
                    wrong_clicks = grid.Wrong,
                    activations,
                    cursor = hasCursor ? new[] { cursor.X, cursor.Y } : null,
                    form_messages = form.Messages,
                    grid_messages = grid.Messages,
                    hits = grid.Hits
                }));
            };
            timer.Start();
            Application.Run(form);
        }
    }
}

// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using System.Web.Script.Serialization;

internal static class WindowsVisualFixture
{
    [StructLayout(LayoutKind.Sequential)]
    struct Rect { public int Left, Top, Right, Bottom; }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateWindowEx(int exStyle, string className, string title, uint style,
                                         int x, int y, int width, int height, IntPtr parent,
                                         IntPtr menu, IntPtr instance, IntPtr parameter);

    [DllImport("user32.dll")]
    static extern bool GetWindowRect(IntPtr window, out Rect rect);

    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll")]
    static extern bool IsIconic(IntPtr window);

    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    static extern bool DestroyWindow(IntPtr window);

    [STAThread]
    static void Main(string[] args)
    {
        string zeroTitle = "CUA zero geometry " + args[1];
        string restoreTitle = "CUA restore target " + args[1];
        bool preparation = args.Length == 3 && args[2] == "prepare";
        bool blocking = args.Length == 3 && args[2] == "block-provider";
        long blockingHandle = 0;
        IntPtr zero = IntPtr.Zero;
        int targetClicks = 0, bystanderClicks = 0, restoreActivations = 0, sample = 0;
        using (var form = new Form { Text = "CUA Visual Fixture " + args[1],
                                    ClientSize = new Size(360, 150),
                                    StartPosition = FormStartPosition.CenterScreen })
        using (var restore = preparation ? new Form { Text = restoreTitle, ClientSize = new Size(260, 120),
                                                      WindowState = FormWindowState.Minimized } : null)
        using (var timer = new System.Windows.Forms.Timer { Interval = 50 })
        {
            var target = new Panel { Bounds = new Rectangle(18, 20, 124, 104), BackColor = Color.FromArgb(13, 237, 101) };
            var bystander = new Panel { Bounds = new Rectangle(205, 20, 124, 104), BackColor = Color.FromArgb(216, 56, 37) };
            target.MouseClick += (sender, click) => targetClicks++;
            bystander.MouseClick += (sender, click) => bystanderClicks++;
            form.Controls.Add(target);
            form.Controls.Add(bystander);
            form.Shown += (sender, eventArgs) =>
            {
                // WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, WS_VISIBLE | WS_POPUP.
                // An empty live window exercises geometry failure without
                // destroying the identity or putting focus on another process.
                if (blocking)
                {
                    var providerThread = new Thread(() =>
                    {
                        var provider = new WindowsVisualBlockingWindow(zeroTitle, args[0] + ".blocked");
                        Interlocked.Exchange(ref blockingHandle, provider.Handle.ToInt64());
                        Application.Run();
                        GC.KeepAlive(provider);
                    });
                    providerThread.IsBackground = true;
                    providerThread.SetApartmentState(ApartmentState.STA);
                    providerThread.Start();
                }
                else
                    zero = CreateWindowEx(0x08000080, "STATIC", zeroTitle, 0x90000000,
                                          0, 0, 0, 0, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
                if (restore != null)
                {
                    restore.Activated += (obj, ev) => restoreActivations++;
                    restore.Controls.Add(new TextBox { Text = "Restored fixture content", Width = 220 });
                    restore.Show();
                }
                form.Activate();
            };
            var json = new JavaScriptSerializer();
            timer.Tick += (sender, eventArgs) =>
            {
                Rect rect;
                IntPtr selected = blocking ? new IntPtr(Interlocked.Read(ref blockingHandle)) : zero;
                bool measured = GetWindowRect(selected, out rect);
                IntPtr foreground = GetForegroundWindow();
                File.WriteAllText(args[0], json.Serialize(new Dictionary<string, object>
                {
                    { "sample", ++sample },
                    { "foreground", foreground == form.Handle },
                    { "foreground_hwnd", foreground.ToInt64() },
                    { "main_title", form.Text },
                    { "zero_title", zeroTitle },
                    { "zero_visible", IsWindowVisible(selected) },
                    { "zero_bounds_measured", measured },
                    { "zero_width", rect.Right - rect.Left },
                    { "zero_height", rect.Bottom - rect.Top },
                    { "restore_title", restoreTitle },
                    { "restore_minimized", restore != null && IsIconic(restore.Handle) },
                    { "restore_foreground", restore != null && foreground == restore.Handle },
                    { "restore_activations", restoreActivations },
                    { "target_clicks", targetClicks },
                    { "bystander_clicks", bystanderClicks }
                }));
            };
            timer.Start();
            try { Application.Run(form); }
            finally { if (zero != IntPtr.Zero) DestroyWindow(zero); }
        }
    }
}

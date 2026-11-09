// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using System.Web.Script.Serialization;

internal static class WindowsCloseFixture
{
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    static extern bool IsWindowEnabled(IntPtr window);

    [STAThread]
    static void Main(string[] args)
    {
        bool last = args.Length > 1 && args[1] == "--last";
        var a = new Form { Text = "CUA Close Document A", ClientSize = new Size(320, 150),
                           StartPosition = FormStartPosition.CenterScreen };
        var b = new Form { Text = "CUA Close Document B", ClientSize = new Size(380, 180),
                           StartPosition = FormStartPosition.CenterScreen };
        var document = new TextBox { Text = "Unsaved test document", AccessibleName = "Document content",
                                     Dock = DockStyle.Fill, Multiline = true };
        b.Controls.Add(document);
        int closeA = 0, closeB = 0, cancelB = 0, confirmB = 0;
        bool closedA = false, closedB = false;
        a.FormClosing += (sender, e) => closeA++;
        a.FormClosed += (sender, e) => closedA = true;
        b.FormClosing += (sender, e) =>
        {
            closeB++;
            var decision = MessageBox.Show(b, "Close the unsaved test document?", "CUA Close Confirmation",
                                           MessageBoxButtons.OKCancel, MessageBoxIcon.Question);
            e.Cancel = decision != DialogResult.OK;
            if (e.Cancel) cancelB++;
            else confirmB++;
        };
        b.FormClosed += (sender, e) => closedB = true;

        a.Show();
        if (!last)
        {
            b.Show();
            b.Activate();
        }
        var aHandle = a.Handle;
        var bHandle = last ? IntPtr.Zero : b.Handle;
        var serializer = new JavaScriptSerializer();
        using (var timer = new Timer { Interval = 50 })
        {
            timer.Tick += (sender, e) =>
            {
                File.WriteAllText(args[0], serializer.Serialize(new Dictionary<string, object>
                {
                    { "close_a", closeA }, { "closed_a", closedA }, { "close_b", closeB }, { "closed_b", closedB },
                    { "cancel_b", cancelB }, { "confirm_b", confirmB }, { "text_b", document.Text },
                    { "foreground_a", GetForegroundWindow() == aHandle },
                    { "foreground_b", GetForegroundWindow() == bHandle },
                    { "modal_b", !last && !closedB && !IsWindowEnabled(bHandle) }
                }));
                if (closedA && (last || closedB))
                    Application.ExitThread();
            };
            timer.Start();
            Application.Run();
        }
    }
}

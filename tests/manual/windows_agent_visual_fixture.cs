// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal sealed class WindowsAgentVisualFixture : Form
{
    readonly string code;
    readonly Rectangle green, orange;
    int targetClicks, bystanderClicks;

    WindowsAgentVisualFixture(string nonce, string code)
    {
        this.code = code;
        Text = "CUA Visual Choice " + nonce;
        StartPosition = FormStartPosition.Manual;
        FormBorderStyle = FormBorderStyle.None;
        Bounds = SystemInformation.VirtualScreen;
        TopMost = true;
        BackColor = Color.FromArgb(240, 244, 247);
        AccessibleName = Text;
        AccessibleRole = AccessibleRole.Pane;
        DoubleBuffered = true;
        int left = ClientSize.Width / 4, top = ClientSize.Height / 3;
        var first = new Rectangle(left, top, ClientSize.Width / 6, ClientSize.Height / 5);
        var second = new Rectangle(left + ClientSize.Width / 3, top, first.Width, first.Height);
        bool greenFirst = Convert.ToInt32(nonce.Substring(0, 2), 16) % 2 == 0;
        green = greenFirst ? first : second;
        orange = greenFirst ? second : first;
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        using (var title = new Font("Segoe UI", 30))
        using (var status = new Font("Segoe UI", 36, FontStyle.Bold))
        using (var target = new SolidBrush(Color.FromArgb(0, 164, 93)))
        using (var other = new SolidBrush(Color.FromArgb(225, 120, 22)))
        {
            e.Graphics.DrawString(Text, title, Brushes.Black, ClientSize.Width / 8, ClientSize.Height / 8);
            e.Graphics.FillRectangle(target, green);
            e.Graphics.FillRectangle(other, orange);
            e.Graphics.DrawString(targetClicks == 0 ? "Waiting for choice" : code, status,
                                  Brushes.Black, ClientSize.Width / 8, ClientSize.Height * 2 / 3);
        }
    }

    protected override void OnMouseClick(MouseEventArgs e)
    {
        base.OnMouseClick(e);
        if (green.Contains(e.Location)) targetClicks++;
        if (orange.Contains(e.Location)) bystanderClicks++;
        Invalidate();
    }

    [STAThread]
    static void Main(string[] args)
    {
        using (var form = new WindowsAgentVisualFixture(args[1], args[2]))
        using (var timer = new Timer { Interval = 50 })
        {
            var serialize = new JavaScriptSerializer();
            timer.Tick += (sender, e) => File.WriteAllText(args[0], serialize.Serialize(new Dictionary<string, object>
            {
                { "target_clicks", form.targetClicks }, { "bystander_clicks", form.bystanderClicks }
            }));
            timer.Start();
            Application.Run(form);
        }
    }
}

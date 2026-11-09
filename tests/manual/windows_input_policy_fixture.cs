// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class WindowsInputPolicyFixture
{
    sealed class Edit : TextBox
    {
        internal int KeyMessages, PasteMessages, Changes;

        protected override void WndProc(ref Message message)
        {
            if (message.Msg >= 0x0100 && message.Msg <= 0x0109)
                KeyMessages++;
            if (message.Msg == 0x0302)
                PasteMessages++;
            base.WndProc(ref message);
        }

        protected override void OnTextChanged(EventArgs e)
        {
            Changes++;
            base.OnTextChanged(e);
        }
    }

    [STAThread]
    static void Main(string[] args)
    {
        using (var form = new Form { Text = args[1], ClientSize = new Size(360, 115),
                                    StartPosition = FormStartPosition.CenterScreen })
        using (var timer = new Timer { Interval = 50 })
        {
            var target = new Edit { Bounds = new Rectangle(15, 15, 320, 28),
                                    AccessibleName = "Policy target", Text = "unchanged" };
            var bystander = new Edit { Bounds = new Rectangle(15, 60, 320, 28),
                                       AccessibleName = "Policy bystander", Text = "untouched" };
            form.Controls.Add(target);
            form.Controls.Add(bystander);
            form.Shown += (sender, e) =>
            {
                target.Focus();
                target.Changes = bystander.Changes = 0;
            };
            int closes = 0, samples = 0;
            form.FormClosing += (sender, e) => closes++;
            var serializer = new JavaScriptSerializer();
            Action<bool> publish = closed => File.WriteAllText(args[0], serializer.Serialize(new Dictionary<string, object>
            {
                { "sample", ++samples }, { "closed", closed }, { "close_count", closes },
                { "focused", !closed && target.Focused }, { "text", target.Text }, { "bystander", bystander.Text },
                { "key_messages", target.KeyMessages + bystander.KeyMessages },
                { "paste_messages", target.PasteMessages + bystander.PasteMessages },
                { "text_changes", target.Changes + bystander.Changes }
            }));
            timer.Tick += (sender, e) => publish(false);
            form.FormClosed += (sender, e) => { timer.Stop(); publish(true); };
            timer.Start();
            Application.Run(form);
        }
    }
}

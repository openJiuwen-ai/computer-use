// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using System.Web.Script.Serialization;

internal static class WindowsPasteFixture
{
    sealed class DelayedEdit : TextBox
    {
        const int PasteMessage = 0x0302;
        readonly int delay;
        readonly bool anonymous, hold;
        internal int PasteCount, Consumed, Pending, Changes, Returns;
        internal uint ClipboardSequenceAtReceive, ClipboardSequenceAtConsume;
        internal int PasteTick, ConsumeTick, TextLengthBeforeConsume, SelectionLengthBeforeConsume;
        internal string ClipboardErrorAtConsume;

        internal DelayedEdit(int delay, bool anonymous, bool hold)
        {
            this.delay = delay;
            this.anonymous = anonymous;
            this.hold = hold;
            Multiline = true;
            AccessibleName = "Paste target";
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg != PasteMessage)
            {
                base.WndProc(ref m);
                return;
            }
            PasteCount++;
            PasteTick = Environment.TickCount;
            ClipboardSequenceAtReceive = GetClipboardSequenceNumber();
            if (hold)
            {
                Pending++;
                return;
            }
            if (delay == 0)
            {
                Consume();
                return;
            }
            var timer = new Timer { Interval = delay };
            timer.Tick += (sender, args) =>
            {
                timer.Dispose();
                Consume();
            };
            timer.Start();
        }

        protected override void OnTextChanged(EventArgs e)
        {
            Changes++;
            base.OnTextChanged(e);
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Return)
                Returns++;
            base.OnKeyDown(e);
        }

        internal void ConsumePending()
        {
            while (Pending > 0)
            {
                Consume();
                Pending--;
            }
        }

        void Consume()
        {
            ConsumeTick = Environment.TickCount;
            ClipboardSequenceAtConsume = GetClipboardSequenceNumber();
            TextLengthBeforeConsume = TextLength;
            SelectionLengthBeforeConsume = SelectionLength;
            if (anonymous)
                SelectedText = ReadClipboardWithoutWindow(out ClipboardErrorAtConsume);
            else
            {
                var paste = Message.Create(Handle, PasteMessage, IntPtr.Zero, IntPtr.Zero);
                base.WndProc(ref paste);
            }
            Consumed++;
        }
    }

    static string ReadClipboardWithoutWindow()
    {
        string ignored;
        return ReadClipboardWithoutWindow(out ignored);
    }

    static string ReadClipboardWithoutWindow(out string error)
    {
        error = null;
        if (!OpenClipboard(IntPtr.Zero))
        {
            error = "OpenClipboard:" + Marshal.GetLastWin32Error();
            return "";
        }
        try
        {
            var handle = GetClipboardData(13);
            if (handle == IntPtr.Zero)
            {
                error = "GetClipboardData:" + Marshal.GetLastWin32Error();
                return "";
            }
            var data = GlobalLock(handle);
            if (data == IntPtr.Zero)
            {
                error = "GlobalLock:" + Marshal.GetLastWin32Error();
                return "";
            }
            try { return Marshal.PtrToStringUni(data); }
            finally { GlobalUnlock(handle); }
        }
        finally { CloseClipboard(); }
    }

    [DllImport("user32.dll", SetLastError = true)]
    static extern bool OpenClipboard(IntPtr owner);
    [DllImport("user32.dll")]
    static extern bool CloseClipboard();
    [DllImport("user32.dll", SetLastError = true)]
    static extern IntPtr GetClipboardData(uint format);
    [DllImport("user32.dll")]
    static extern uint GetClipboardSequenceNumber();
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr GlobalLock(IntPtr memory);
    [DllImport("kernel32.dll")]
    static extern bool GlobalUnlock(IntPtr memory);

    [STAThread]
    static void Main(string[] args)
    {
        string profile = args.Length == 4 ? "default" : args[4];
        var target = new DelayedEdit(Int32.Parse(args[1]), Boolean.Parse(args[3]), profile == "hold")
        {
            Bounds = new Rectangle(16, 16, 400, 90)
        };
        if (profile == "caret" || profile == "selection" || profile == "readonly")
            target.Text = "left[middle]right";
        target.ReadOnly = profile == "readonly";
        var bystander = new TextBox { Bounds = new Rectangle(16, 120, 400, 30), Text = "untouched",
                                      AccessibleName = "Bystander" };
        var form = new Form { Text = "CUA Paste Fixture " + args[2], ClientSize = new Size(440, 175),
                              StartPosition = FormStartPosition.CenterScreen };
        form.Controls.Add(target);
        form.Controls.Add(bystander);
        form.Shown += (sender, eventArgs) =>
        {
            Clipboard.SetText("prior-" + args[2]);
            target.Focus();
            if (profile == "caret" || profile == "selection" || profile == "readonly")
                target.Select(5, profile == "selection" ? 6 : 0);
            target.Changes = 0;
        };
        var serialize = new JavaScriptSerializer();
        var sample = new Timer { Interval = 50 };
        string commandFile = args[0] + ".command", command = "", clipboard = null, error = null;
        bool locked = false;
        int samples = 0;
        sample.Tick += (sender, eventArgs) =>
        {
            try
            {
                if (File.Exists(commandFile))
                {
                    string next = File.ReadAllText(commandFile);
                    if (next != command)
                    {
                        if (next == "sample")
                            clipboard = ReadClipboardWithoutWindow();
                        else if (next == "overwrite")
                        {
                            Clipboard.SetText("external-" + args[2]);
                            clipboard = ReadClipboardWithoutWindow();
                        }
                        else if (next == "consume")
                            target.ConsumePending();
                        else if (next == "lock")
                        {
                            locked = OpenClipboard(form.Handle);
                            if (!locked)
                                throw new InvalidOperationException("Fixture could not hold the clipboard");
                        }
                        else if (next == "release" && locked)
                        {
                            if (!CloseClipboard())
                                throw new InvalidOperationException("Fixture could not release the clipboard");
                            locked = false;
                            clipboard = ReadClipboardWithoutWindow();
                        }
                        command = next;
                    }
                }
            }
            catch (Exception ex)
            {
                error = ex.Message;
            }
            File.WriteAllText(args[0], serialize.Serialize(new Dictionary<string, object>
            {
                { "text", target.Text }, { "paste_count", target.PasteCount }, { "consumed", target.Consumed },
                { "bystander", bystander.Text }, { "focused", target.Focused && Form.ActiveForm == form },
                { "selection_start", target.SelectionStart }, { "selection_length", target.SelectionLength },
                { "text_changes", target.Changes }, { "returns", target.Returns },
                { "sample", ++samples }, { "pending", target.Pending }, { "clipboard_sample", clipboard },
                { "paste_tick", target.PasteTick }, { "consume_tick", target.ConsumeTick },
                { "clipboard_sequence_at_receive", target.ClipboardSequenceAtReceive },
                { "clipboard_sequence_at_consume", target.ClipboardSequenceAtConsume },
                { "clipboard_error_at_consume", target.ClipboardErrorAtConsume },
                { "text_length_before_consume", target.TextLengthBeforeConsume },
                { "selection_length_before_consume", target.SelectionLengthBeforeConsume },
                { "clipboard_locked", locked }, { "last_command", command }, { "fixture_error", error }
            }));
        };
        sample.Start();
        Application.Run(form);
        sample.Dispose();
        if (locked)
            CloseClipboard();
    }
}

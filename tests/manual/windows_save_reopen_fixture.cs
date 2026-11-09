// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
using System.Web.Script.Serialization;

internal sealed class WindowsSaveReopenFixture : Form
{
    sealed class DocumentEdit : TextBox
    {
        internal int Pastes;

        protected override void WndProc(ref Message message)
        {
            const int WmPaste = 0x0302;
            if (message.Msg == WmPaste)
                Pastes++;
            base.WndProc(ref message);
        }
    }

    static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
    const string InitialDraft = "Draft before replacement";
    [DllImport("user32.dll")]
    static extern uint GetClipboardSequenceNumber();
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();

    readonly string statePath, documentPath;
    readonly bool semantic;
    readonly int pid = Process.GetCurrentProcess().Id;
    readonly JavaScriptSerializer serializer = new JavaScriptSerializer();
    readonly DocumentEdit editor = new DocumentEdit
    {
        Multiline = true, AccessibleName = "Paste target", Bounds = new Rectangle(16, 16, 400, 90),
    };
    readonly TextBox bystander = new TextBox
    {
        Text = "untouched", AccessibleName = "Bystander", Bounds = new Rectangle(16, 122, 400, 24),
    };
    long windowHandle;
    readonly List<long> activationForegroundHwnds = new List<long>();
    int saves, closes, shortcutSaves, buttonSaves;
    bool closed, everForeground;
    string error;

    WindowsSaveReopenFixture(string statePath, string documentPath, string title, bool reopen, bool semantic)
    {
        this.statePath = statePath;
        this.documentPath = documentPath;
        this.semantic = semantic;
        Text = title;
        ClientSize = new Size(440, semantic ? 205 : 170);
        StartPosition = FormStartPosition.CenterScreen;
        if (semantic)
            editor.Multiline = false;
        if (reopen)
            editor.Text = File.ReadAllText(documentPath, Utf8);
        else if (semantic)
            editor.Text = InitialDraft;
        Controls.Add(editor);
        Controls.Add(bystander);
        if (semantic)
        {
            var save = new Button
            {
                Text = "Save document", AccessibleName = "Save document", Bounds = new Rectangle(16, 157, 140, 30),
            };
            save.Click += (sender, args) =>
            {
                buttonSaves++;
                SaveDocument();
            };
            Controls.Add(save);
        }
        Shown += (sender, args) =>
        {
            windowHandle = Handle.ToInt64();
            if (!semantic)
                editor.Focus();
            Publish();
        };
    }

    protected override bool ShowWithoutActivation { get { return semantic || base.ShowWithoutActivation; } }

    protected override void OnActivated(EventArgs args)
    {
        IntPtr foreground = GetForegroundWindow();
        activationForegroundHwnds.Add(foreground.ToInt64());
        ObserveForeground(foreground);
        base.OnActivated(args);
    }

    bool ObserveForeground(IntPtr foreground)
    {
        bool owned = IsHandleCreated && foreground == Handle;
        everForeground |= owned;
        return owned;
    }

    protected override bool ProcessCmdKey(ref Message message, Keys keyData)
    {
        if (keyData != (Keys.Control | Keys.S))
            return base.ProcessCmdKey(ref message, keyData);
        if (!editor.Focused)
        {
            error = "Save was routed to a different control.";
            Publish();
        }
        else
        {
            shortcutSaves++;
            SaveDocument();
        }
        return true;
    }

    void SaveDocument()
    {
        File.WriteAllText(documentPath, editor.Text, Utf8);
        saves++;
        Publish();
    }

    protected override void OnFormClosing(FormClosingEventArgs args)
    {
        closes++;
        base.OnFormClosing(args);
    }

    protected override void OnFormClosed(FormClosedEventArgs args)
    {
        closed = true;
        Publish();
        base.OnFormClosed(args);
    }

    void Publish()
    {
        IntPtr foregroundHwnd = GetForegroundWindow();
        bool foreground = ObserveForeground(foregroundHwnd);
        File.WriteAllText(statePath, serializer.Serialize(new
        {
            pid, hwnd = windowHandle, title = Text, text = editor.Text, pastes = editor.Pastes, saves, closes, closed,
            bystander = bystander.Text, focused = !closed && editor.Focused && ActiveForm == this, error,
            clipboard_sequence = GetClipboardSequenceNumber(), shortcut_saves = shortcutSaves, button_saves = buttonSaves,
            foreground, foreground_hwnd = foregroundHwnd.ToInt64(), ever_foreground = everForeground,
            activations = activationForegroundHwnds.Count, activation_foreground_hwnds = activationForegroundHwnds,
        }), Utf8);
    }

    [STAThread]
    static void Main(string[] args)
    {
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.ThrowException);
        bool semantic = args.Length > 4 && args[4] == "--semantic";
        using (var window = new WindowsSaveReopenFixture(args[0], args[1], args[2], args[3] == "reopen", semantic))
        using (var timer = new Timer { Interval = 50 })
        {
            timer.Tick += (sender, eventArgs) => window.Publish();
            window.FormClosed += (sender, eventArgs) => timer.Stop();
            timer.Start();
            Application.Run(window);
        }
    }
}

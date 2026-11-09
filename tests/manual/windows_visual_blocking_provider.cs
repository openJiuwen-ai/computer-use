// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;
using System.Windows.Automation.Provider;
using System.Windows.Forms;

// This window has its own STA and message pump. A blocked provider can leave
// that thread permanently busy while the independent visual oracle still runs.
internal sealed class WindowsVisualBlockingWindow : NativeWindow
{
    const int GetObjectMessage = 0x003D;
    readonly Provider provider;

    internal WindowsVisualBlockingWindow(string title, string evidencePath)
    {
        provider = new Provider(this, title, evidencePath);
        CreateHandle(new CreateParams { Caption = title, Style = unchecked((int)0x90000000), ExStyle = 0x08000080 });
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == GetObjectMessage && message.LParam.ToInt32() == AutomationInteropProvider.RootObjectId)
        {
            message.Result = AutomationInteropProvider.ReturnRawElementProvider(
                Handle, message.WParam, message.LParam, provider);
            return;
        }
        base.WndProc(ref message);
    }

    [ComVisible(true)]
    sealed class Provider : IRawElementProviderSimple, IWindowProvider
    {
        readonly WindowsVisualBlockingWindow window;
        readonly string title, evidencePath;
        int entered;

        internal Provider(WindowsVisualBlockingWindow window, string title, string evidencePath)
        {
            this.window = window;
            this.title = title;
            this.evidencePath = evidencePath;
        }

        public ProviderOptions ProviderOptions
        {
            get { return ProviderOptions.ServerSideProvider | ProviderOptions.UseComThreading; }
        }

        public IRawElementProviderSimple HostRawElementProvider
        {
            get { return AutomationInteropProvider.HostProviderFromHandle(window.Handle); }
        }

        public object GetPatternProvider(int pattern)
        {
            return pattern == WindowPatternIdentifiers.Pattern.Id ? this : null;
        }

        public object GetPropertyValue(int property)
        {
            if (property == AutomationElementIdentifiers.NameProperty.Id)
                return title;
            if (property == AutomationElementIdentifiers.ControlTypeProperty.Id)
                return ControlType.Window.Id;
            if (property == AutomationElementIdentifiers.IsControlElementProperty.Id ||
                property == AutomationElementIdentifiers.IsContentElementProperty.Id)
                return true;
            if (property == WindowPatternIdentifiers.IsModalProperty.Id)
                return IsModal;
            return null;
        }

        public bool IsModal
        {
            get
            {
                if (!File.Exists(evidencePath + ".arm"))
                    return false;
                if (Interlocked.Exchange(ref entered, 1) == 0)
                    File.WriteAllText(evidencePath, "IWindowProvider.IsModal entered and waiting indefinitely");
                Thread.Sleep(Timeout.Infinite);
                return false;
            }
        }

        public bool Maximizable { get { return false; } }
        public bool Minimizable { get { return false; } }
        public bool IsTopmost { get { return false; } }
        public WindowVisualState VisualState { get { return WindowVisualState.Normal; } }
        public WindowInteractionState InteractionState { get { return WindowInteractionState.ReadyForUserInteraction; } }
        public bool WaitForInputIdle(int milliseconds) { return true; }
        public void Close() { throw new NotSupportedException(); }
        public void SetVisualState(WindowVisualState state) { throw new NotSupportedException(); }
    }
}

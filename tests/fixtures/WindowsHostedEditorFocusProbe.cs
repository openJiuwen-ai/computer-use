// Read-only UIA/Win32 evidence for a disposable browser editor.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using UIA = NativeUIA;

static class HostedEditorFocusProbe
{
    [StructLayout(LayoutKind.Sequential)]
    struct RECT
    {
        public int L, T, R, B;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct GUI
    {
        public int Size;
        public uint Flags;
        public IntPtr Active, Focus, Capture, Menu, Move, Caret;
        public RECT Rect;
    }

    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")]
    static extern bool GetGUIThreadInfo(uint tid, ref GUI g);
    [DllImport("user32.dll")]
    static extern bool IsChild(IntPtr parent, IntPtr child);
    static string Runtime(UIA.IUIAutomationElement e)
    {
        return e == null ? null : new JavaScriptSerializer().Serialize(e.GetRuntimeId());
    }

    [MTAThread]
    static void Main(string[] args)
    {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        var uia = (UIA.IUIAutomation)Activator.CreateInstance(typeof(UIA.IUIAutomation).Assembly.GetType("NativeUIA.CUIAutomation8Class", true));
        var desktop = uia.GetRootElement();
        string title = args.Length > 0 ? args[0] : "Hosted Editor Probe", name = args.Length > 1 ? args[1] : "Demo reply";
        var root = desktop.FindFirst((UIA.TreeScope)2, uia.CreatePropertyCondition(30005, title));
        if (root == null)
            throw new InvalidOperationException("Disposable fixture window not found");
        var handle = new IntPtr(Convert.ToInt32(root.GetCurrentPropertyValueEx(30020, 1)));
        var target = root.FindFirst((UIA.TreeScope)4, uia.CreateAndCondition(uia.CreatePropertyCondition(30005, name), uia.CreatePropertyCondition(30003, 50004)));
        var focus = uia.GetFocusedElement();
        string targetId = Runtime(target), focusedId = Runtime(focus);
        IntPtr owner = IntPtr.Zero;
        var current = target;
        var seen = new HashSet<string>();
        for (int n = 0; n < 128 && current != null && seen.Add(Runtime(current)); n++)
        {
            object value = current.GetCurrentPropertyValueEx(30020, 1);
            int hwnd = value is int ? (int)value : 0;
            if (hwnd != 0)
            {
                owner = new IntPtr(hwnd);
                break;
            }

            current = uia.RawViewWalker.GetParentElement(current);
        }

        uint pid;
        var gui = new GUI
        {
            Size = Marshal.SizeOf(typeof(GUI))
        };
        bool known = GetGUIThreadInfo(GetWindowThreadProcessId(handle, out pid), ref gui);
        Console.WriteLine(new JavaScriptSerializer().Serialize(new { exact = targetId != null && targetId == focusedId, targetId, focusedId, focusName = focus == null ? null : focus.GetCurrentPropertyValueEx(30005, 1), root = handle.ToInt64(), owner = owner.ToInt64(), nativeFocus = known ? gui.Focus.ToInt64() : 0, keyboardHostAboveRenderer = known && owner != IntPtr.Zero && owner != gui.Focus && IsChild(gui.Focus, owner) }));
    }
}

// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace ComputerUse.Windows
{
    // Power notifications describe the current session, not foreground
    // authority. Never infer UIPI, a lock, or an input receiver from them.
    internal static class NativeDesktopState
    {
        internal static readonly Guid SessionDisplay = new Guid("2B84C20E-AD23-4DDF-93DB-05FFBD7EFCA5");
        internal static readonly Guid LidSwitch = new Guid("BA3E0F4D-B817-4094-A2D1-D56379E6A0F3");
        internal sealed class State
        {
            int display = -1, lid = -1;
            internal void Update(Guid setting, int size, int value)
            {
                if (size != 4)
                    return;
                if (setting == SessionDisplay && value >= 0 && value <= 2)
                    Interlocked.Exchange(ref display, value);
                if (setting == LidSwitch && (value == 0 || value == 1))
                    Interlocked.Exchange(ref lid, value);
            }

            internal void Clear()
            {
                Interlocked.Exchange(ref display, -1);
                Interlocked.Exchange(ref lid, -1);
            }

            internal object Facts()
            {
                return Wire.Obj("sessionDisplayState", Volatile.Read(ref display), "lidState", Volatile.Read(ref lid));
            }

            internal void RequireInput()
            {
                // A closed lid alone is normal with an external display. An
                // off display alone may wake on input. Reject only the known
                // conjunction that was measured refusing physical input.
                if (Volatile.Read(ref display) == 0 && Volatile.Read(ref lid) == 0)
                    throw new Failure("input_unavailable", "Windows reports a closed laptop lid and an off session display; no requested physical input sent", "open the lid or establish an active session display, then read the intended window again; background reading and usable native control actions remain available");
            }
        }

        static readonly State current = new State();
        internal static void RequireInput()
        {
            current.RequireInput();
        }

        internal static object Facts()
        {
            return current.Facts();
        }

        internal static IDisposable Observe()
        {
            try
            {
                return new Observer();
            }
            catch (Win32Exception)
            {
                current.Clear();
                return null;
            }
            catch (ExternalException)
            {
                current.Clear();
                return null;
            }
        }

        sealed class Observer : NativeWindow, IDisposable
        {
            [DllImport("user32.dll", SetLastError = true)]
            static extern IntPtr RegisterPowerSettingNotification(IntPtr recipient, ref Guid setting, uint flags);
            [DllImport("user32.dll")]
            static extern bool UnregisterPowerSettingNotification(IntPtr handle);
            readonly IntPtr display, lid;
            internal Observer()
            {
                CreateHandle(new CreateParams { Caption = "Computer Use session availability", Parent = new IntPtr(-3) });
                var displayGuid = SessionDisplay;
                var lidGuid = LidSwitch;
                display = RegisterPowerSettingNotification(Handle, ref displayGuid, 0);
                lid = RegisterPowerSettingNotification(Handle, ref lidGuid, 0);
            }

            protected override void WndProc(ref Message message)
            {
                if (message.Msg == 0x218 && message.WParam.ToInt64() == 0x8013 && message.LParam != IntPtr.Zero)
                {
                    var setting = (Guid)Marshal.PtrToStructure(message.LParam, typeof(Guid));
                    int size = Marshal.ReadInt32(message.LParam, 16);
                    if (size == 4)
                        current.Update(setting, size, Marshal.ReadInt32(message.LParam, 20));
                }

                base.WndProc(ref message);
            }

            public void Dispose()
            {
                if (display != IntPtr.Zero)
                    UnregisterPowerSettingNotification(display);
                if (lid != IntPtr.Zero)
                    UnregisterPowerSettingNotification(lid);
                current.Clear();
                DestroyHandle();
            }
        }
    }
}

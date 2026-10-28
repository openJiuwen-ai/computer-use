// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace ComputerUse.Windows
{
    internal sealed class NativePointerLayout
    {
        internal readonly NativeCaptureGeometry Canvas;
        internal readonly NativeCaptureGeometry[] Displays;
        internal readonly string Signature;
        internal NativePointerLayout()
        {
            var canvas = SystemInformation.VirtualScreen;
            Canvas = Geometry(canvas);
            var screens = Screen.AllScreens.OrderBy(s => s.DeviceName, StringComparer.Ordinal).ToArray();
            if (screens.Length < 1 || screens.Length > 32 ||
                screens.Select(s => s.DeviceName).Distinct().Count() != screens.Length)
                throw new Failure("observation_unavailable", "display layout is unavailable");
            Displays = screens.Select(s => Geometry(s.Bounds)).ToArray();
            Signature = canvas.ToString() + "|" + String.Join("|", screens.Select(s => s.DeviceName + ":" + s.Bounds));
        }

        static NativeCaptureGeometry Geometry(Rectangle r)
        {
            return new NativeCaptureGeometry(r.X, r.Y, r.Width, r.Height, 1);
        }
    }

    // Uses HWND identity and physical screen geometry. No UIA traversal,
    // legacy input strategy, clipboard or synthetic keyboard focus is involved.
    internal sealed partial class NativeWindowsPointer : INativePointerProvider,
                                                         INativeElementPointerProvider,
                                                         INativePointerClickTimingProvider
    {
        [DllImport("user32.dll")]
        static extern uint GetDoubleClickTime();
        public NativePointerClickTiming ClickTiming()
        {
            var box = SystemInformation.DoubleClickSize;
            return new NativePointerClickTiming { Milliseconds = (int)GetDoubleClickTime(), Width = box.Width,
                                                  Height = box.Height };
        }
        readonly NativeReadStore windows;
        readonly NativePointerCaptures captures;
        readonly string mode;
        sealed class Target
        {
            internal Dictionary<string, object> Binding;
            internal string[] Within;
            internal NativeWindowRecord Interaction;
            internal NativePointerPoint Point;
            internal string Kind;
        }

        internal NativeWindowsPointer(NativeReadStore windows, NativePointerCaptures captures, string mode = "shared")
        {
            this.windows = windows;
            this.captures = captures;
            this.mode = mode;
        }

        public NativePointerCapture Capture(Dictionary<string, object> target, Request r)
        {
            r.Check();
            return captures.Get(target);
        }
        public NativePointerState Read(Dictionary<string, object> target, NativePointerPoint point,
                                       NativePointerGestureIntent gesture, bool dragging, Request r)
        {
            string kind = gesture.Kind;

            r.Check();
            if (!Native.Interactive())
                throw new Failure("desktop_unavailable", "pointer input requires the unlocked interactive desktop");
            if (dragging)
                foreach (int key in new[] { 2, 4, 16, 17, 18, 91, 92 }

                )
                {
                    if ((Native.GetAsyncKeyState(key) & 0x8000) != 0)
                        throw new Failure("input_unavailable", "unowned input became held during the drag");
                }

            var image = captures.Get(target);
            image.RequirePoint(point);
            if (new NativePointerLayout().Signature != image.Layout)
                throw new Failure("snapshot_expired", "display layout changed; read and view a new image");
            var window = windows.Validate(target, r);
            // Desktop coordinates address the visible desktop, including the
            // taskbar and popups. There is no fixed-window hit-test or activation.
            if (window == null)
            {
                NativeInputAccess.RequireForeground();
                var desktopRecipient = Native.WindowFromPoint(new Native.POINT { X = point.X, Y = point.Y });
                var desktopInput = new Native.GUI { Size = Marshal.SizeOf(typeof(Native.GUI)) };
                if (Native.GetGUIThreadInfo(0, ref desktopInput) && desktopInput.Capture != IntPtr.Zero)
                    desktopRecipient = desktopInput.Capture;
                NativeInputAccess.RequireWindow(desktopRecipient);
                return new NativePointerState { Ready = true, PhysicalReady = true, Foreground = true,
                                                ForegroundReady = true, Recipient = desktopRecipient };
            }

            IntPtr handle = window.Native.Handle;
            if (!Native.IsWindowVisible(handle) || Native.IsIconic(handle) || !Native.IsWindowEnabled(handle))
                throw new Failure("input_unavailable", "the selected window is hidden, minimized or disabled");
            windows.RequireNoKnownModal(window, r);
            uint pid;
            var gui = new Native.GUI { Size = Marshal.SizeOf(typeof(Native.GUI)) };
            uint targetThread = Native.GetWindowThreadProcessId(handle, out pid);
            bool threadKnown = Native.GetGUIThreadInfo(targetThread, ref gui);
            bool ownedGrab = threadKnown && Native.Inside(handle, gui.Capture);
            Rectangle bounds = Native.Bounds(handle);
            bool sameBounds = bounds.X == image.WindowBounds.X && bounds.Y == image.WindowBounds.Y &&
                              bounds.Width == image.WindowBounds.NativeWidth &&
                              bounds.Height == image.WindowBounds.NativeHeight;
            // A window move/resize may be the drag's intended effect. Continue
            // its original absolute path only under that window's actual grab.
            if (!sameBounds && !(dragging && ownedGrab))
                throw new Failure("snapshot_expired", "captured window geometry changed; read and view a new image");
            var foreground = new Native.GUI { Size = Marshal.SizeOf(typeof(Native.GUI)) };
            bool foregroundKnown = Native.GetGUIThreadInfo(0, ref foreground);
            IntPtr hitWindow = Native.WindowFromPoint(new Native.POINT { X = point.X, Y = point.Y });
            bool hit = Native.Inside(handle, hitWindow);
            bool noOtherGrab =
                foregroundKnown && (foreground.Capture == IntPtr.Zero || Native.Inside(handle, foreground.Capture));
            bool route = noOtherGrab &&
                         (dragging && ownedGrab || hit && (!dragging || threadKnown && gui.Capture == IntPtr.Zero));
            IntPtr foregroundWindow = Native.GetForegroundWindow();
            bool active = foregroundWindow == handle;
            bool foregroundReady = kind == "scroll" ? active : Native.PointerForeground(handle, foregroundWindow);
            bool physicalReady = route;
            bool natural =
                !foregroundReady &&
                NativePointerClickAdmission.NaturalActivation(
                    mode, gesture, physicalReady, hit,
                    NativePointerClickAdmission.QueueIdle(foregroundKnown, foreground.Capture, foreground.Flags) &&
                        NativePointerClickAdmission.QueueIdle(targetThread != 0 && threadKnown, gui.Capture,
                                                              gui.Flags));
            // A single requested physical click may itself activate its live,
            // reachable original window. Windows may consume that click; the
            // existing receipt records submission only and never replays it.
            route = physicalReady && (foregroundReady || natural);
            // WM_MOUSEWHEEL may route to focus rather than hover depending on
            // user settings. Do not send window-scoped wheel input elsewhere.
            if (kind == "scroll")
                route = route && active && threadKnown && Native.Inside(handle, gui.Focus);
            if (route)
            {
                NativeInputAccess.RequireForeground();
                NativeInputAccess.RequireWindow(handle);
                if (natural)
                    NativeInputAccess.RequireWindow(hitWindow);
            }

            windows.Validate(target, r);
            r.Check();
            return new NativePointerState {
                Native = new Target { Binding = new Dictionary<string, object>(target), Point = point, Kind = kind },
                Ready = route,
                PhysicalReady = physicalReady,
                NaturalActivation = natural && route,
                Foreground = active,
                ForegroundReady = foregroundReady,
                CaptureBlocked =
                    foregroundKnown && foreground.Capture != IntPtr.Zero && !Native.Inside(handle, foreground.Capture),
                Recipient = foregroundKnown && foreground.Capture != IntPtr.Zero ? foreground.Capture : hitWindow
            };
        }

        public void RequireIdle(Request r)
        {
            r.Check();
            NativeDesktopState.RequireInput();
            Native.Released();
            r.Check();
        }

        public void Activate(NativePointerState state, Request r)
        {
            var selected = (Target)state.Native;
            RequireIdle(r);
            var window = selected.Interaction == null
                             ? windows.Validate(selected.Binding, r)
                             : new NativeUiaBoundary(windows).Validate(selected.Binding, selected.Within, r);
            if (selected.Interaction != null)
                NativeUiaBoundary.SameWindow(selected.Interaction, window, r);
            window.Native.Validate();
            r.Check();
            // A denied SetForegroundWindow is reported by subsequent readiness
            // observation. Never attach threads, fake Alt, or restore foreground.
            Native.Foreground(window.Native, true, r, mode == "exclusive",
                              () =>
                              {
                                  var current = selected.Interaction == null
                                                    ? windows.Validate(selected.Binding, r)
                                                    : new NativeUiaBoundary(windows).Validate(selected.Binding,
                                                                                              selected.Within, r);
                                  NativeUiaBoundary.SameWindow(window, current, r);
                              });
            if (selected.Interaction != null)
                NativeUiaBoundary.SameWindow(
                    window, new NativeUiaBoundary(windows).Validate(selected.Binding, selected.Within, r), r);
        }

        public int Send(NativePointerEvent[] events)
        {
            var inputs = events
                             .Select(e => new Native.INPUT {
                                 Type = 0,
                                 Data = new Native.UNION { Mouse = new Native.MOUSE { X = e.X, Y = e.Y, Flags = e.Flags,
                                                                                      Data = unchecked((uint)e.Data) } }
                             })
                             .ToArray();
            return (int)Native.SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(Native.INPUT)));
        }
    }
}

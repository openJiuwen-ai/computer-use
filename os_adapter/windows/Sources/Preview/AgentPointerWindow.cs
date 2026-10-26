using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace ComputerUse.Windows
{
    // A disabled, nonactivating, click-through decoration. Only this HWND is
    // excluded from discovery, never the driver's other windows or its PID.
    internal sealed class AgentPointerWindow : NativeWindow, IDisposable
    {
        internal const string Title = "ComputerUse.AgentPointer.v1";
        const string Property = "ComputerUse.AgentPointer.decoration.v1";
        static readonly IntPtr magic = new IntPtr(0x435541);
        static readonly uint captureMessage = RegisterWindowMessage("ComputerUse.AgentPointer.capture.v1");
        static readonly string executable = Path.GetFullPath(Assembly.GetExecutingAssembly().Location);
        int captures;
        bool visible;
        volatile bool stopped;
        internal AgentPointerWindow()
        {
            CreateHandle(new CreateParams { Caption = Title, Style = unchecked((int)0x88000000), ExStyle = 0x080800a0, Width = 1, Height = 1 }); // POPUP|DISABLED; NOACTIVATE|LAYERED|TRANSPARENT|TOOLWINDOW
            SetProp(Handle, Property, magic);
        }

        internal static bool IsDecoration(IntPtr handle)
        {
            try
            {
                if (GetProp(handle, Property) != magic || Native.Title(handle) != Title)
                    return false;
                using (var process = Process.GetProcessById((int)Native.PID(handle)))
                    return String.Equals(Path.GetFullPath(process.MainModule.FileName), executable, StringComparison.OrdinalIgnoreCase);
            }
            catch
            {
                return false;
            }
        }

        protected override void WndProc(ref Message message)
        {
            if ((uint)message.Msg == captureMessage)
            {
                captures = Math.Max(0, captures + (message.WParam == IntPtr.Zero ? -1 : 1));
                if (captures > 0)
                    Hide();
                message.Result = new IntPtr(1);
                return;
            }

            if (message.Msg == 0x21)
            {
                message.Result = new IntPtr(3);
                return;
            } // MA_NOACTIVATE

            if (message.Msg == 0x84)
            {
                message.Result = new IntPtr(-1);
                return;
            } // HTTRANSPARENT

            if (message.Msg == 0x3d)
            {
                message.Result = IntPtr.Zero;
                return;
            } // No accessibility provider.

            base.WndProc(ref message);
        }

        internal void Hide()
        {
            if (visible)
                Native.ShowWindow(Handle, 0);
            visible = false;
        }

        internal void Present(Bitmap bitmap, Point position, byte opacity, IntPtr target)
        {
            if (captures > 0 || stopped)
            {
                Hide();
                return;
            }

            IntPtr screen = GetDC(IntPtr.Zero), dc = IntPtr.Zero, image = IntPtr.Zero, previous = IntPtr.Zero;
            try
            {
                dc = CreateCompatibleDC(screen);
                image = bitmap.GetHbitmap(Color.FromArgb(0));
                previous = SelectObject(dc, image);
                var destination = new Native.POINT
                {
                    X = position.X,
                    Y = position.Y
                };
                var size = new SIZE
                {
                    Width = bitmap.Width,
                    Height = bitmap.Height
                };
                var origin = new Native.POINT();
                var blend = new BLEND
                {
                    Alpha = opacity,
                    Format = 1
                };
                if (!UpdateLayeredWindow(Handle, screen, ref destination, ref size, dc, ref origin, 0, ref blend, 2))
                {
                    Hide();
                    return;
                }

                if (target == IntPtr.Zero)
                    SetWindowPos(Handle, new IntPtr(-1), 0, 0, 0, 0, 0x13); // Desktop pointer only, no activation.
                else
                {
                    bool topmost = (GetWindowLongPtr(target, -20).ToInt64() & 8) != 0;
                    bool ownTopmost = (GetWindowLongPtr(Handle, -20).ToInt64() & 8) != 0;
                    if (topmost != ownTopmost)
                        SetWindowPos(Handle, new IntPtr(topmost ? -1 : -2), 0, 0, 0, 0, 0x13);
                    IntPtr above = Native.GetWindow(target, 3);
                    if (above != Handle)
                    {
                        var seen = new HashSet<IntPtr>();
                        while (above != IntPtr.Zero && IsDecoration(above) && seen.Add(above))
                            above = Native.GetWindow(above, 3);
                        SetWindowPos(Handle, above == IntPtr.Zero && topmost ? new IntPtr(-1) : above, 0, 0, 0, 0, 0x13);
                    }
                }

                if (stopped)
                {
                    Hide();
                    return;
                }

                if (!visible)
                    Native.ShowWindow(Handle, 4); // SW_SHOWNOACTIVATE, never Form.Show/Activate.
                visible = true;
            }
            finally
            {
                if (previous != IntPtr.Zero)
                    SelectObject(dc, previous);
                if (image != IntPtr.Zero)
                    DeleteObject(image);
                if (dc != IntPtr.Zero)
                    DeleteDC(dc);
                if (screen != IntPtr.Zero)
                    ReleaseDC(IntPtr.Zero, screen);
            }
        }

        internal static float Scale(PointF point)
        {
            try
            {
                uint x, y;
                if (GetDpiForMonitor(MonitorFromPoint(new Native.POINT { X = (int)point.X, Y = (int)point.Y }, 2), 0, out x, out y) == 0 && x >= 48 && x <= 768)
                    return x / 96f;
            }
            catch (EntryPointNotFoundException)
            {
            }

            return 1;
        }

        internal static bool ReducedMotion()
        {
            bool animate;
            return SystemParametersInfo(0x1042, 0, out animate, 0) && !animate;
        }

        // Hide only confirmed decorations for our own captures. Keeping the
        // normal affinity allows external demo/screen recordings to see them.
        internal static IDisposable IsolateCaptures()
        {
            var paused = new List<IntPtr>();
            Native.EnumWindows((handle, unused) =>
            {
                if (IsDecoration(handle))
                {
                    UIntPtr result;
                    if (Native.SendMessageTimeoutRaw(handle, captureMessage, new IntPtr(1), IntPtr.Zero, 3, 250, out result) != IntPtr.Zero)
                        paused.Add(handle);
                }

                return true;
            }, IntPtr.Zero);
            if (paused.Count > 0)
                try
                {
                    DwmFlush();
                }
                catch (DllNotFoundException)
                {
                }

            return new CaptureScope(paused);
        }

        sealed class CaptureScope : IDisposable
        {
            List<IntPtr> paused;
            internal CaptureScope(List<IntPtr> paused)
            {
                this.paused = paused;
            }

            public void Dispose()
            {
                if (paused == null)
                    return;
                foreach (var handle in paused)
                    if (IsDecoration(handle))
                    {
                        UIntPtr result;
                        Native.SendMessageTimeoutRaw(handle, captureMessage, IntPtr.Zero, IntPtr.Zero, 3, 250, out result);
                    }

                paused = null;
            }
        }

        internal void Shutdown()
        {
            stopped = true;
            if (Handle != IntPtr.Zero)
                Native.ShowWindow(Handle, 0);
            visible = false;
        }

        public void Dispose()
        {
            Shutdown();
            if (Handle != IntPtr.Zero)
            {
                RemoveProp(Handle, Property);
                DestroyHandle();
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        struct SIZE
        {
            internal int Width, Height;
        }

        [StructLayout(LayoutKind.Sequential, Pack = 1)]
        struct BLEND
        {
            internal byte Op, Flags, Alpha, Format;
        }

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern uint RegisterWindowMessage(string name);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern bool SetProp(IntPtr handle, string name, IntPtr value);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern IntPtr GetProp(IntPtr handle, string name);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern IntPtr RemoveProp(IntPtr handle, string name);
        [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
        static extern IntPtr GetWindowLongPtr(IntPtr handle, int index);
        [DllImport("user32.dll")]
        static extern bool SetWindowPos(IntPtr handle, IntPtr after, int x, int y, int width, int height, uint flags);
        [DllImport("user32.dll")]
        static extern IntPtr GetDC(IntPtr handle);
        [DllImport("user32.dll")]
        static extern int ReleaseDC(IntPtr handle, IntPtr dc);
        [DllImport("gdi32.dll")]
        static extern IntPtr CreateCompatibleDC(IntPtr dc);
        [DllImport("gdi32.dll")]
        static extern bool DeleteDC(IntPtr dc);
        [DllImport("gdi32.dll")]
        static extern IntPtr SelectObject(IntPtr dc, IntPtr value);
        [DllImport("gdi32.dll")]
        static extern bool DeleteObject(IntPtr value);
        [DllImport("user32.dll", SetLastError = true)]
        static extern bool UpdateLayeredWindow(IntPtr handle, IntPtr screen, ref Native.POINT destination, ref SIZE size, IntPtr source, ref Native.POINT origin, uint key, ref BLEND blend, uint flags);
        [DllImport("user32.dll")]
        static extern IntPtr MonitorFromPoint(Native.POINT point, uint flags);
        [DllImport("shcore.dll")]
        static extern int GetDpiForMonitor(IntPtr monitor, int kind, out uint x, out uint y);
        [DllImport("user32.dll")]
        static extern bool SystemParametersInfo(uint action, uint parameter, out bool value, uint flags);
        [DllImport("dwmapi.dll")]
        static extern int DwmFlush();
    }
}

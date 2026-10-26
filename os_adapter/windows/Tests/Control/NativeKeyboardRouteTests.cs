using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace ComputerUse.Windows
{
    // Real, hidden Win32 HWNDs check routing topology without changing the
    // foreground. UIA focus facts and delivered text are separately checked.
    internal static class Native
    {
        [DllImport("user32.dll")]
        static extern bool IsChild(IntPtr parent, IntPtr child);
        internal static bool Inside(IntPtr parent, IntPtr child)
        {
            return parent == child || child != IntPtr.Zero && IsChild(parent, child);
        }
    }

    internal static class NativeKeyboardRouteTests
    {
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr CreateWindowEx(uint ex, string name, string title, uint style, int x, int y, int w, int h, IntPtr parent, IntPtr menu, IntPtr instance, IntPtr param);
        [DllImport("user32.dll")]
        static extern bool DestroyWindow(IntPtr h);
        static readonly Dictionary<long, IntPtr> Handles = new Dictionary<long, IntPtr>
        {
            {
                0,
                IntPtr.Zero
            }
        };
        static int assertions;
        static void Check(long root, long owner, long focus, bool exact, bool expected)
        {
            assertions++;
            if (NativeKeyboardRoute.Contains(Handles[root], Handles[owner], Handles[focus], exact) != expected)
                throw new InvalidOperationException("Keyboard host boundary mismatch: " + root + "/" + owner + "/" + focus + "/" + exact);
        }

        static void Main()
        {
            try
            {
                foreach (var pair in new[]
                {
                    new[]
                    {
                        1,
                        0
                    },
                    new[]
                    {
                        2,
                        1
                    },
                    new[]
                    {
                        3,
                        2
                    },
                    new[]
                    {
                        4,
                        1
                    },
                    new[]
                    {
                        5,
                        3
                    },
                    new[]
                    {
                        10,
                        0
                    },
                    new[]
                    {
                        11,
                        10
                    }
                }

                )
                {
                    var h = CreateWindowEx(0, "STATIC", "Keyboard route probe", pair[1] == 0 ? 0u : 0x40000000u, 0, 0, 32, 32, Handles[pair[1]], IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
                    if (h == IntPtr.Zero)
                        throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                    Handles.Add(pair[0], h);
                }

                Check(1, 3, 3, false, true);
                Check(1, 3, 5, false, true);
                Check(1, 3, 2, true, true);
                Check(1, 3, 2, false, false);
                Check(1, 3, 1, true, true);
                Check(1, 3, 1, false, false);
                Check(1, 3, 4, true, false);
                Check(1, 3, 11, true, false);
                Check(1, 11, 1, true, false);
                Check(1, 11, 10, true, false);
                Check(1, 0, 2, true, false);
                Check(1, 3, 0, true, false);
                Check(0, 3, 2, true, false);
                if (Native.Inside(Handles[3], Handles[2]))
                    throw new InvalidOperationException("The original descendant-only check did not reproduce the native host mismatch");
                Console.WriteLine("{\"passed\":true,\"assertions\":" + assertions + "}");
            }
            finally
            {
                IntPtr h;
                if (Handles.TryGetValue(1, out h))
                    DestroyWindow(h);
                if (Handles.TryGetValue(10, out h))
                    DestroyWindow(h);
            }
        }
    }
}

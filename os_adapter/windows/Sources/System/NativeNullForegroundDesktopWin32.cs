// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Runtime.InteropServices;

namespace ComputerUse.Windows
{
    // Own-session, query-only adapter. The borrowed thread desktop is not
    // closed; Native.Interactive owns and closes its OpenInputDesktop handle.
    internal sealed class NativeNullForegroundDesktopWin32 : INativeNullForegroundDesktopQuery
    {
        [DllImport("kernelbase.dll", SetLastError = true)]
        [return:MarshalAs(UnmanagedType.Bool)]
        static extern bool CompareObjectHandles(IntPtr first, IntPtr second);
        [DllImport("kernel32.dll")]
        static extern uint GetCurrentProcessId();
        [DllImport("kernel32.dll", SetLastError = true)]
        [return:MarshalAs(UnmanagedType.Bool)]
        static extern bool ProcessIdToSessionId(uint process, out uint session);
        [DllImport("wtsapi32.dll", EntryPoint = "WTSQuerySessionInformationW", ExactSpelling = true,
                   SetLastError = true)]
        [return:MarshalAs(UnmanagedType.Bool)]
        static extern bool WTSQuerySessionInformation(IntPtr server, uint session, int kind, out IntPtr buffer,
                                                      out uint bytes);
        [DllImport("wtsapi32.dll", ExactSpelling = true)]
        static extern void WTSFreeMemory(IntPtr buffer);
        public IntPtr ThreadDesktop()
        {
            return Native.GetThreadDesktop(Native.GetCurrentThreadId());
        }
        public bool SameDesktop(IntPtr input, IntPtr thread)
        {
            return CompareObjectHandles(input, thread);
        }
        public bool OwnSession(out uint session)
        {
            return ProcessIdToSessionId(GetCurrentProcessId(), out session);
        }
        public bool SessionInformation(uint session, int kind, out IntPtr buffer, out uint bytes)
        {
            return WTSQuerySessionInformation(IntPtr.Zero, session, kind, out buffer, out bytes);
        }
        public void FreeSessionInformation(IntPtr buffer)
        {
            WTSFreeMemory(buffer);
        }
    }
}

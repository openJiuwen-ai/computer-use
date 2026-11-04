// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace ComputerUse.Windows
{
    internal interface INativeProcessLifetimeQuery
    {
        IntPtr Open(uint pid, out int error);
        long LegacyStarted(uint pid);
        bool CreationTime(IntPtr process, out long created);
        uint Wait(IntPtr process);
        bool Close(IntPtr process);
    }
    internal sealed class NativeProcessLifetimeQuery : INativeProcessLifetimeQuery
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel,
                                           out long user);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern uint WaitForSingleObject(IntPtr process, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool CloseHandle(IntPtr process);
        public IntPtr Open(uint pid, out int error)
        {
            IntPtr process = OpenProcess(0x1000 | 0x100000, false, pid); // QUERY_LIMITED_INFORMATION | SYNCHRONIZE
            error = Marshal.GetLastWin32Error();
            return process;
        }
        public long LegacyStarted(uint pid)
        {
            using (var process = Process.GetProcessById((int)pid)) return process.StartTime.ToUniversalTime().Ticks;
        }
        public bool CreationTime(IntPtr process, out long created)
        {
            long exited, kernel, user;
            return GetProcessTimes(process, out created, out exited, out kernel, out user);
        }
        public uint Wait(IntPtr process)
        {
            return WaitForSingleObject(process, 0);
        }
        public bool Close(IntPtr process)
        {
            return CloseHandle(process);
        }
    }
    internal static class NativeProcessLifetime
    {
        static readonly INativeProcessLifetimeQuery native = new NativeProcessLifetimeQuery();
        internal static void Require(uint pid, long started, INativeProcessLifetimeQuery query = null)
        {
            query = query ?? native;
            int error;
            IntPtr process = query.Open(pid, out error);
            if (process == IntPtr.Zero)
            {
                // Preserve the original limited-info query capability when the
                // added SYNCHRONIZE right is denied. This is a query branch,
                // not permission retained for another request or input replay.
                if (error == 5)
                {
                    if (query.LegacyStarted(pid) != started)
                        throw new InvalidOperationException("process creation time changed");
                    return;
                }
                throw new InvalidOperationException("process identity could not be queried");
            }
            try
            {
                long created;
                if (!query.CreationTime(process, out created))
                    throw new InvalidOperationException("process creation time is unavailable");
                // Match Process.StartTime.ToUniversalTime().Ticks exactly,
                // including Framework's FILETIME -> local -> UTC conversion.
                if (DateTime.FromFileTime(created).ToUniversalTime().Ticks != started)
                    throw new InvalidOperationException("process creation time changed");
                if (query.Wait(process) != 258)
                    throw new InvalidOperationException("process ended or liveness is unavailable");
            }
            finally
            {
                if (!query.Close(process))
                    throw new InvalidOperationException("process identity query handle could not be released");
            }
        }
    }
}

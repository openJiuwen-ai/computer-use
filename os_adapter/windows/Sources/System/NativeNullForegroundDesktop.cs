// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Runtime.InteropServices;

namespace ComputerUse.Windows
{
    // QUERY only. No foreground authority, input receiver, activation or sticky
    // health state is established here. NativeRuntime owns resource health.
    internal interface INativeNullForegroundDesktopQuery
    {
        IntPtr ThreadDesktop();
        bool SameDesktop(IntPtr input, IntPtr thread);
        bool OwnSession(out uint session);
        bool SessionInformation(uint session, int kind, out IntPtr buffer, out uint bytes);
        void FreeSessionInformation(IntPtr buffer);
    }

    internal static class NativeNullForegroundDesktop
    {
        // Windows x64 WTSINFOEXW: DWORD Level; 8-byte aligned union Data;
        // its level-1 prefix is SessionId, SessionState, SessionFlags.
        // Read only this prefix; never marshal session names or user content.
        internal const int ConnectState = 8, SessionInfoEx = 25;
        internal const int InfoExPrefixBytes = 20, InfoExDataOffset = 8;

        internal static bool Query(IntPtr input, INativeNullForegroundDesktopQuery query)
        {
            if (input == IntPtr.Zero || IntPtr.Size != 8)
                return false;
            try
            {
                var thread = query.ThreadDesktop();
                if (thread == IntPtr.Zero || !query.SameDesktop(input, thread))
                    return false;
                uint session;
                if (!query.OwnSession(out session) || session == UInt32.MaxValue)
                    return false;
                if (!Active(query, session))
                    return false;
                return Unlocked(query, session);
            }
            catch (NativeDesktopCleanupFailure)
            {
                throw;
            }
            catch (Exception)
            {
                return false;
            } // Unavailable APIs and unknown facts fail closed.
        }

        static bool Active(INativeNullForegroundDesktopQuery query, uint session)
        {
            IntPtr buffer = IntPtr.Zero;
            uint bytes = 0;
            Exception primary = null;
            try
            {
                return query.SessionInformation(session, ConnectState, out buffer, out bytes) &&
                       buffer != IntPtr.Zero && bytes >= 4 && Marshal.ReadInt32(buffer) == 0; // WTSActive
            }
            catch (Exception error)
            {
                primary = error;
                throw;
            }
            finally
            {
                Release(query, buffer, primary);
            }
        }

        static bool Unlocked(INativeNullForegroundDesktopQuery query, uint session)
        {
            IntPtr buffer = IntPtr.Zero;
            uint bytes = 0;
            Exception primary = null;
            try
            {
                return query.SessionInformation(session, SessionInfoEx, out buffer, out bytes) &&
                       buffer != IntPtr.Zero && bytes >= InfoExPrefixBytes && Marshal.ReadInt32(buffer, 0) == 1 &&
                       unchecked((uint)Marshal.ReadInt32(buffer, InfoExDataOffset)) == session &&
                       Marshal.ReadInt32(buffer, InfoExDataOffset + 4) == 0     // WTSActive again in the same snapshot.
                       && Marshal.ReadInt32(buffer, InfoExDataOffset + 8) == 1; // WTS_SESSIONSTATE_UNLOCK
            }
            catch (Exception error)
            {
                primary = error;
                throw;
            }
            finally
            {
                Release(query, buffer, primary);
            }
        }

        static void Release(INativeNullForegroundDesktopQuery query, IntPtr buffer, Exception primary)
        {
            if (buffer == IntPtr.Zero)
                return;
            try
            {
                query.FreeSessionInformation(buffer);
            }
            catch (Exception error)
            {
                throw new NativeDesktopCleanupFailure(
                    "WTSFreeMemory did not return: " + error.GetType().Name + ": " + error.Message, primary);
            }
        }
    }
}

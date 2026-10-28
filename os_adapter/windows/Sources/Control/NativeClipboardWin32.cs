// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

namespace ComputerUse.Windows
{
    internal sealed class NativeWindowsClipboard : INativeClipboardProvider
    {
        public NativeClipboardTransaction Save(Request r)
        {
            return new NativeClipboardTransaction(new NativeClipboardWin32(), r);
        }
    }
    // The owner thread serializes native locks and services delayed rendering.
    // No OLE proxy, activation, input replay or application-name exceptions.

    internal sealed class NativeClipboardWin32 : INativeClipboardApi
    {
        const long MaxBytes = 16 * 1024 * 1024;
        internal const int HandoffMs = 2500;
        readonly NativeClipboardOwner actor;
        readonly IntPtr owner;
        bool opened, publication, targetRead, foreignTextRead, renderFailed, mutated, resourceReleaseFailed;
        uint publicationVersion, acceptedVersion;
        double handoffUntil = Double.NaN;
        NativePasteConsumer consumer;
        readonly Dictionary<uint, IntPtr> pending = new Dictionary<uint, IntPtr>();
        readonly HashSet<Payload> allocations = new HashSet<Payload>();
        readonly NativeClipboardReleaseLedger releases =
            new NativeClipboardReleaseLedger(handle => GlobalFree(handle) == IntPtr.Zero);
        internal IntPtr OwnerHandle
        {
            get {
                return owner;
            }
        }
        internal bool ForeignTextRead
        {
            get {
                return actor.Invoke(() => foreignTextRead);
            }
        }
        internal NativeClipboardWin32()
        {
            actor = new NativeClipboardOwner(Message, ReleaseAll);
            owner = actor.Handle;
        }

        static Failure Error(string detail)
        {
            return new Failure("input_unavailable",
                               detail + ": " + new Win32Exception(Marshal.GetLastWin32Error()).Message);
        }

        sealed class Lock : IDisposable
        {
            readonly NativeClipboardWin32 api;
            internal Lock(NativeClipboardWin32 api)
            {
                this.api = api;
            }

            public void Dispose()
            {
                api.actor.Invoke(() =>
                                 {
                                     api.Close();
                                     return true;
                                 });
            }
        }
        void Close()
        {
            if (!opened)
                return;
            if (!CloseClipboard())
                throw Error("could not release clipboard lock");
            opened = false;
            // Only our own mutation/close may advance this accepted lineage.
            if (mutated && publication && GetClipboardOwner() == owner)
                acceptedVersion = GetClipboardSequenceNumber();
            mutated = false;
        }

        public IDisposable Open(Request r)
        {
            double until = Math.Min(r.Deadline, Wire.Now + 400);
            while (true)
            {
                r.Check();
                bool acquired = actor.Invoke(() =>
                                             {
                                                 if (opened)
                                                     throw new Failure("invalid_request",
                                                                       "clipboard transaction lock is already held");
                                                 if (!OpenClipboard(owner))
                                                     return false;
                                                 opened = true;
                                                 mutated = false;
                                                 return true;
                                             });
                if (acquired)
                    return new Lock(this);

                if (Wire.Now >= until)
                    throw new Failure("input_unavailable", "clipboard is busy; transaction did not acquire it");
                r.Wait(10); // Caller waits; owner must pump render messages.
            }
        }

        void RequireOpen()
        {
            if (!opened)
                throw new Failure("invalid_request", "clipboard access requires its native lock");
        }

        public uint Version
        {
            get {
                return actor.Invoke(() =>
                                    {
                                        RequireOpen();
                                        return GetClipboardSequenceNumber();
                                    });
            }
        }
        bool Owns()
        {
            return publication && GetClipboardOwner() == owner && GetClipboardSequenceNumber() == acceptedVersion;
        }
        public bool IsCurrentPublication(uint version)
        {
            return actor.Invoke(() =>
                                {
                                    RequireOpen();
                                    return publicationVersion == version && Owns();
                                });
        }

        static bool GlobalFormat(uint format)
        {
            return new uint[] { 1, 4, 5, 6, 7, 8, 10, 11, 12, 13, 15, 16, 17, 0x81 }.Contains(format) ||
                   format >= 0xC000 && format <= 0xFFFF;
        }

        public IList<NativeClipboardData> Read(Request r)
        {
            return actor.Invoke(
                () =>
                {
                    RequireOpen();
                    var entries = new List<NativeClipboardData>();
                    var seen = new HashSet<uint>();
                    long bytes = 0;
                    uint format = 0;
                    while (true)
                    {
                        r.Check();
                        SetLastError(0);
                        format = EnumClipboardFormats(format);
                        if (format == 0)
                        {
                            if (Marshal.GetLastWin32Error() != 0)
                                throw Error("clipboard format enumeration failed");
                            break;
                        }
                        if (!seen.Add(format) || entries.Count >= 256)
                            throw new Failure(
                                "input_unavailable",
                                "clipboard formats exceed the bounded preservation profile; no paste sent");
                        if (!GlobalFormat(format))
                            throw new Failure("input_unavailable",
                                              "clipboard format " + format +
                                                  " cannot be preserved by this transaction; no paste sent");
                        IntPtr data = GetClipboardData(format);
                        r.Check();
                        if (data == IntPtr.Zero)
                            throw Error("clipboard format could not be materialized");
                        ulong size = GlobalSize(data).ToUInt64();
                        if (size == 0 || size > (ulong)(MaxBytes - bytes))
                            throw new Failure(
                                "input_unavailable",
                                "clipboard data exceeds the preservation budget or has no safe memory representation; no paste sent");
                        var saved = new byte[(int)size];
                        IntPtr pointer = GlobalLock(data);
                        if (pointer == IntPtr.Zero)
                            throw Error("clipboard data could not be locked");
                        try
                        {
                            Marshal.Copy(pointer, saved, 0, saved.Length);
                        }
                        finally
                        {
                            GlobalUnlock(data);
                        }
                        entries.Add(new NativeClipboardData(format, saved));
                        bytes += saved.Length;
                    }
                    return (IList<NativeClipboardData>)entries;
                });
        }

        sealed class Payload : INativeClipboardPayload
        {
            internal readonly List<Tuple<uint, IntPtr>> Items = new List<Tuple<uint, IntPtr>>();
            internal readonly NativeClipboardWin32 Owner;
            internal NativePasteConsumer Consumer;
            internal bool Delayed;
            internal volatile bool Released;
            internal Payload(NativeClipboardWin32 owner)
            {
                Owner = owner;
            }
            internal void Free()
            {
                if (Released)
                    return;
                bool failed = false;
                for (int n = 0; n < Items.Count; n++)
                {
                    var item = Items[n];
                    if (item.Item2 == IntPtr.Zero)
                        continue;
                    if (GlobalFree(item.Item2) != IntPtr.Zero)
                        failed = true;
                    else
                        Items[n] = Tuple.Create(item.Item1, IntPtr.Zero);
                }
                if (failed)
                {
                    Owner.resourceReleaseFailed = true;
                    throw Error("clipboard allocation could not be released");
                }
                Items.Clear();
                Released = true;
            }
            public void Dispose()
            {
                if (Released)
                    return;
                // A timed-out actor command may still reference this payload.
                // Free only on the actor or in its terminal cleanup callback.
                Owner.actor.Invoke(() =>
                                   {
                                       Free();
                                       lock (Owner.allocations) Owner.allocations.Remove(this);
                                       return true;
                                   });
            }
        }

        public INativeClipboardPayload Prepare(IList<NativeClipboardData> entries, Request r)
        {
            var result = new Payload(this);
            long total = 0;
            lock (allocations) allocations.Add(result);
            try
            {
                foreach (var entry in entries)
                {
                    r.Check();
                    total += entry.Bytes.Length;
                    if (result.Items.Count >= 256 || entry.Bytes.Length == 0 || total > MaxBytes ||
                        !GlobalFormat(entry.Format))
                        throw new Failure("input_unavailable", "clipboard payload cannot be fully prepared");
                    IntPtr handle = GlobalAlloc(2, new UIntPtr((uint)entry.Bytes.Length));
                    if (handle == IntPtr.Zero)
                        throw Error("clipboard allocation failed");
                    result.Items.Add(Tuple.Create(entry.Format, handle));
                    IntPtr pointer = GlobalLock(handle);
                    if (pointer == IntPtr.Zero)
                        throw Error("clipboard allocation could not be locked");
                    try
                    {
                        Marshal.Copy(entry.Bytes, 0, pointer, entry.Bytes.Length);
                    }
                    finally
                    {
                        GlobalUnlock(handle);
                    }
                }

                return result;
            }
            catch
            {
                result.Dispose();
                throw;
            }
        }

        static Encoding TextEncoding(uint locale, uint property, uint fallback)
        {
            var value = new StringBuilder(16);
            uint codepage;
            if (GetLocaleInfo(locale, property, value, value.Capacity) == 0 ||
                !UInt32.TryParse(value.ToString(), out codepage))
                throw Error("clipboard text encoding is unavailable");
            return Encoding.GetEncoding((int)(codepage == 0 ? fallback : codepage));
        }
        public INativeClipboardPayload PrepareText(string text, NativePasteConsumer expected, Request r)
        {

            r.Check();
            if (expected == null || !expected.Alive)
                throw new Failure("input_unavailable",
                                  "the original native clipboard consumer is unavailable; no paste sent");
            uint locale = GetThreadLocale();
            var payload =
                (Payload)Prepare(NativeClipboardText.Formats(text, locale, TextEncoding(locale, 0x1004, GetACP()),
                                                             TextEncoding(locale, 0x000b, GetOEMCP())),
                                 r);
            payload.Consumer = expected;
            payload.Delayed = true;
            return payload;
        }

        public void Replace(INativeClipboardPayload value, Action changed, Request r)
        {
            actor.Invoke(() =>
                         {
                             RequireOpen();
                             var payload = value as Payload;
                             if (payload == null)
                                 throw new Failure("invalid_request", "foreign clipboard payload");
                             r.Check();
                             if (!EmptyClipboard())
                                 throw Error("clipboard could not be cleared");
                             mutated = true;
                             changed();
                             ReleasePending();
                             publication = false;
                             targetRead = false;
                             foreignTextRead = false;
                             renderFailed = false;
                             if (payload.Delayed)
                             {
                                 consumer = payload.Consumer;
                                 foreach (var item in payload.Items)
                                     pending.Add(item.Item1, item.Item2);
                                 payload.Items.Clear();
                                 publication = true;
                                 foreach (uint format in pending.Keys.ToArray())
                                 {
                                     r.Check();
                                     SetLastError(0);
                                     SetClipboardData(format, IntPtr.Zero);
                                     if (!IsClipboardFormatAvailable(format))
                                         throw Error("delayed clipboard format could not be registered");
                                 }
                                 publicationVersion = acceptedVersion = GetClipboardSequenceNumber();
                             }
                             else
                                 foreach (int index in Enumerable.Range(0, payload.Items.Count))
                                 {
                                     r.Check();
                                     var item = payload.Items[index];
                                     if (item.Item2 == IntPtr.Zero)
                                         throw new Failure("invalid_request",
                                                           "clipboard memory has already transferred");
                                     if (SetClipboardData(item.Item1, item.Item2) == IntPtr.Zero)
                                         throw Error("clipboard format could not be installed");
                                     payload.Items[index] = Tuple.Create(item.Item1, IntPtr.Zero);
                                 }
                             return true;
                         });
        }
        void ReleasePending()
        {
            foreach (var data in pending.Values)
                releases.Release(data);
            pending.Clear();
        }
        void ReleaseAll()
        {
            ReleasePending();
            releases.TerminalAttempt();
            lock (allocations)
            {
                foreach (var payload in allocations.ToArray())
                {
                    try
                    {
                        payload.Free();
                        allocations.Remove(payload);
                    }
                    catch
                    {
                        resourceReleaseFailed = true;
                    }
                }
            }
        }
        void Render(uint format, bool acknowledge)
        {
            IntPtr data;
            if (Environment.GetEnvironmentVariable("COMPUTER_USE_TRACE_NATIVE_INPUT") == "1")
                Console.Error.WriteLine("clipboard_render format=" + format + " owns=" + Owns() +
                                        " sequence=" + GetClipboardSequenceNumber() + " accepted=" + acceptedVersion +
                                        " requester=" + GetOpenClipboardWindow());
            if (!Owns() || !pending.TryGetValue(format, out data) || data == IntPtr.Zero)
                return;
            var requester = GetOpenClipboardWindow();
            bool expected = acknowledge && (format == 13 || format == 1 || format == 7) && consumer != null &&
                            consumer.Matches(requester);
            if (SetClipboardData(format, data) == IntPtr.Zero)
            {
                renderFailed = true;
                return;
            }
            pending[format] = IntPtr.Zero;
            acceptedVersion = GetClipboardSequenceNumber();
            if (expected)
                targetRead = true;
            else if (acknowledge && (format == 13 || format == 1 || format == 7))
                foreignTextRead = true;
            if (Environment.GetEnvironmentVariable("COMPUTER_USE_TRACE_NATIVE_INPUT") == "1")
                Console.Error.WriteLine("clipboard_render returned expected=" + expected +
                                        " sequence=" + GetClipboardSequenceNumber() + " accepted=" + acceptedVersion);
        }
        void Message(int message, IntPtr parameter)
        {
            try
            {
                if (message == 0x0305)
                    Render(unchecked((uint)parameter.ToInt64()), true);
                else if (message == 0x0307)
                {
                    publication = false;
                    ReleasePending();
                }
                else if (message == 0x0306 && publication)
                {
                    if (!OpenClipboard(owner))
                    {
                        renderFailed = true;
                        return;
                    }
                    try
                    {
                        foreach (uint format in pending.Keys.ToArray())
                            Render(format, false);
                    }
                    finally
                    {
                        CloseClipboard();
                    }
                }
            }
            catch
            {
                renderFailed = true;
            }
        }
        public NativePasteHandoff Handoff(Request r)
        {
            // Bounded evidence wait. Unknown readers do not become success.
            if (Double.IsNaN(handoffUntil))
                handoffUntil = Math.Min(r.Deadline, Wire.Now + HandoffMs);
            double until = handoffUntil; // Cancellation cleanup resumes this same wait; never resets it.
            while (true)
            {
                if (Wire.Now >= until)
                    return NativePasteHandoff.Unconfirmed;
                r.Check();
                var status = actor.Invoke(() =>
                                          {
                                              if (!Owns())
                                                  return NativePasteHandoff.OwnershipLost;
                                              if (!renderFailed && targetRead)
                                              {
                                                  if (OpenClipboard(owner))
                                                  {
                                                      try
                                                      {
                                                          return Owns() ? NativePasteHandoff.TargetRead
                                                                        : NativePasteHandoff.OwnershipLost;
                                                      }
                                                      finally
                                                      {
                                                          CloseClipboard();
                                                      }
                                                  }
                                                  return NativePasteHandoff.Unconfirmed;
                                              }
                                              if (consumer == null || !consumer.Alive)
                                                  return NativePasteHandoff.TargetLost;
                                              return NativePasteHandoff.Unconfirmed;
                                          });
                if (status != NativePasteHandoff.Unconfirmed || Wire.Now >= until)
                    return status;
                r.Token.WaitHandle.WaitOne(Math.Max(1, Math.Min(10, (int)Math.Ceiling(until - Wire.Now))));
            }
        }

        public void Dispose()
        {
            bool failed = false;
            try
            {
                failed = actor.Invoke(() =>
                                      {
                                          bool problem = renderFailed || resourceReleaseFailed || releases.Failed;
                                          if (opened)
                                          {
                                              try
                                              {
                                                  Close();
                                              }
                                              catch
                                              {
                                                  problem = true;
                                              }
                                          }
                                          return problem;
                                      });
            }
            catch
            {
                failed = true;
            }
            try
            {
                actor.Dispose();
            }
            catch
            {

                failed = true;
            }
            if (failed || renderFailed || resourceReleaseFailed || releases.Failed)
                throw new Failure("input_unavailable", "clipboard transaction resources could not be released");
        }

        [DllImport("user32.dll", SetLastError = true)]
        static extern bool OpenClipboard(IntPtr owner);
        [DllImport("user32.dll", SetLastError = true)]
        static extern bool CloseClipboard();
        [DllImport("user32.dll", SetLastError = true)]
        static extern bool EmptyClipboard();
        [DllImport("user32.dll")]
        static extern IntPtr GetClipboardOwner();
        [DllImport("user32.dll")]
        static extern IntPtr GetOpenClipboardWindow();
        [DllImport("user32.dll")]
        static extern bool IsClipboardFormatAvailable(uint format);
        [DllImport("user32.dll", SetLastError = true)]
        static extern uint EnumClipboardFormats(uint previous);
        [DllImport("user32.dll", SetLastError = true)]
        static extern IntPtr GetClipboardData(uint format);
        [DllImport("user32.dll", SetLastError = true)]
        static extern IntPtr SetClipboardData(uint format, IntPtr data);
        [DllImport("user32.dll")]
        static extern uint GetClipboardSequenceNumber();
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr GlobalAlloc(uint flags, UIntPtr bytes);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern UIntPtr GlobalSize(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr GlobalLock(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GlobalUnlock(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr GlobalFree(IntPtr handle);
        [DllImport("kernel32.dll")]
        static extern void SetLastError(uint error);
        [DllImport("kernel32.dll")]
        static extern uint GetThreadLocale();
        [DllImport("kernel32.dll")]
        static extern uint GetACP();
        [DllImport("kernel32.dll")]
        static extern uint GetOEMCP();
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern int GetLocaleInfo(uint locale, uint type, StringBuilder value, int count);
    }
}

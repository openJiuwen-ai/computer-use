// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

namespace ComputerUse.Windows
{
    // Shell grouping evidence belongs to a window. Its HWND and owning
    // process remain the authority for the physical lifetime and input.
    internal static class NativeWindowApplication
    {
        [StructLayout(LayoutKind.Sequential)]
        struct PropertyKey
        {
            internal Guid Format;
            internal uint Id;
        }

        [StructLayout(LayoutKind.Explicit, Size = 24)]
        struct PropertyValue
        {
            [FieldOffset(0)] internal ushort Type;
            [FieldOffset(8)] internal IntPtr String;
        }

        [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface PropertyStore
        {
            [PreserveSig] int GetCount(out uint count);
            [PreserveSig] int GetAt(uint index, out PropertyKey key);
            [PreserveSig] int GetValue(ref PropertyKey key, out PropertyValue value);
        }

        [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface ShellItem
        {
            [PreserveSig] int BindToHandler(IntPtr context, ref Guid handler, ref Guid iid, out IntPtr value);
            [PreserveSig] int GetParent(out ShellItem parent);
            [PreserveSig] int GetDisplayName(uint kind, out IntPtr value);
        }

        [DllImport("shell32.dll", ExactSpelling = true)]
        static extern int SHGetPropertyStoreForWindow(IntPtr handle, ref Guid iid, out PropertyStore store);
        [DllImport("ole32.dll", ExactSpelling = true)]
        static extern int PropVariantClear(ref PropertyValue value);
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        static extern int SHCreateItemInKnownFolder(ref Guid folder, uint flags, string name, ref Guid iid,
                                                    out ShellItem item);

        internal static string Id(IntPtr handle)
        {
            var iid = typeof(PropertyStore).GUID;
            PropertyStore store;
            if (SHGetPropertyStoreForWindow(handle, ref iid, out store) < 0 || store == null)
                return null;
            var key = new PropertyKey { Format = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), Id = 5 };
            var value = new PropertyValue();
            try
            {
                if (store.GetValue(ref key, out value) < 0 || value.Type != 31) // VT_LPWSTR
                    return null;
                var id = Marshal.PtrToStringUni(value.String);
                return String.IsNullOrWhiteSpace(id) || id.Length > 128 ? null : id;
            }
            finally
            {
                PropVariantClear(ref value);
                Marshal.ReleaseComObject(store);
            }
        }

        internal static string Name(string id)
        {
            // Resolve one AppsFolder item by its ID. No directory enumeration,
            // alternate Shell namespace, file path or launch is involved.
            if (String.IsNullOrWhiteSpace(id) || id.Length > 128 || id.IndexOfAny(new[] { '\\', '/', ':' }) >= 0)
                return null;
            var folder = new Guid("1E87508D-89C2-42F0-8A7E-645A0F50CA58");
            var iid = typeof(ShellItem).GUID;
            ShellItem item;
            if (SHCreateItemInKnownFolder(ref folder, 0, id, ref iid, out item) < 0 || item == null)
                return null;
            IntPtr value = IntPtr.Zero;
            try
            {
                return item.GetDisplayName(0, out value) < 0 ? null : Marshal.PtrToStringUni(value);
            }
            finally
            {
                Marshal.FreeCoTaskMem(value);
                Marshal.ReleaseComObject(item);
            }
        }
    }

    // Optional installed labels for already proved, running identities. One
    // child and one batch can be outstanding. A slow label never waits beyond
    // this read's small allowance and does not affect directory coverage.
    internal sealed class NativeApplicationNames : IDisposable
    {
        const int WaitMs = 180, MaxIds = 16, MaxRequestBytes = 3000, PublicationMs = 200;
        readonly Dictionary<string, string> cache = new Dictionary<string, string>();
        readonly Func<INativeWorkerProcess> createWorker;
        INativeWorkerProcess worker;
        HashSet<string> pending;
        bool unavailable;

        internal NativeApplicationNames(Func<INativeWorkerProcess> create = null)
        {
            createWorker = create ?? (() => new NativeChildProcess("--application-name-worker"));
        }

        internal void Restore(string id, string name)
        {
            if (id != null && !String.IsNullOrWhiteSpace(name) && cache.Count < 4096)
                cache[id] = name;
        }

        internal void Enrich(List<NativeWindowRecord> windows, Request request, double? captureDeadline)
        {
            double until = Math.Min(Wire.Now + WaitMs,
                                    Math.Min(request.Deadline, captureDeadline ?? request.ObservationDeadline) -
                                        PublicationMs);
            if (!unavailable && Wire.Now < until)
            {
                try
                {
                    bool started = false;
                    if (pending == null)
                    {
                        string[] ids = windows.Select(item => item.ApplicationIdentifier).Where(id => id != null && !cache.ContainsKey(id))
                                              .Distinct().Take(Math.Min(MaxIds, 4096 - cache.Count)).ToArray();
                        if (ids.Length != 0)
                        {
                            var frame = Wire.Obj("ids", ids);
                            // Keep the first message below the pipe buffer even
                            // if startup has not reached the worker's read yet.
                            while (Encoding.UTF8.GetByteCount(Wire.Json(frame)) > MaxRequestBytes)
                            {
                                ids = ids.Take((ids.Length + 1) / 2).ToArray();
                                frame["ids"] = ids;
                            }
                            request.Check();
                            if (worker == null)
                                worker = createWorker();
                            worker.Send(frame);
                            pending = new HashSet<string>(ids);
                            started = true;
                        }
                    }

                    while (pending != null && Wire.Now < until)
                    {
                        request.Check();
                        int wait = started ? (int)Math.Ceiling(Math.Max(0, Math.Min(15, until - Wire.Now))) : 0;
                        string line = worker.Poll(wait);
                        if (line == null)
                        {
                            if (!started)
                                break;
                            continue;
                        }
                        var reply = Wire.Array(Wire.Serializer().DeserializeObject(line));
                        if (reply.Length != pending.Count)
                            throw new InvalidOperationException("application name worker returned an unexpected number of names");
                        foreach (object raw in reply)
                        {
                            var row = Wire.Object(raw);
                            Wire.Keys(row, "id,name");
                            string id = Wire.Str(row, "id"), name = row["name"] as string;
                            if (!pending.Remove(id))
                                throw new InvalidOperationException("application name worker returned an unexpected ID");
                            cache[id] = String.IsNullOrWhiteSpace(name) ? null : NativeReadStore.Text(name);
                        }
                        pending = null;
                    }
                }
                catch (Exception)
                {
                    request.Check();
                    unavailable = true;
                }
            }

            foreach (var item in windows)
            {
                string name;
                if (item.ApplicationIdentifier != null && cache.TryGetValue(item.ApplicationIdentifier, out name) && name != null)
                    item.ApplicationName = name;
            }
        }

        internal static int RunWorker()
        {
            string line;
            while ((line = Console.ReadLine()) != null)
            {
                var frame = Wire.Object(Wire.Serializer().DeserializeObject(line));
                Wire.Keys(frame, "ids");
                var ids = Wire.Array(frame["ids"]);
                if (ids.Length == 0 || ids.Length > MaxIds)
                    throw new InvalidOperationException("application name worker request exceeded its ID budget");
                var reply = ids.Select(raw =>
                {
                    var id = raw as string;
                    string name = NativeWindowApplication.Name(id);
                    return Wire.Obj("id", id, "name", name == null ? null : NativeReadStore.Text(name));
                }).ToArray();
                Console.WriteLine(Wire.Json(reply));
                Console.Out.Flush();
            }

            return 0;
        }

        public void Dispose()
        {
            if (worker != null)
                worker.Dispose();
        }
    }
}

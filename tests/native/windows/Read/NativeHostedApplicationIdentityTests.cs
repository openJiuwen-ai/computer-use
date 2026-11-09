// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace ComputerUse.Windows
{
    internal static class NativeHostedApplicationIdentityTests
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
            [PreserveSig] int SetValue(ref PropertyKey key, ref PropertyValue value);
        }

        [DllImport("shell32.dll", ExactSpelling = true)]
        static extern int SHGetPropertyStoreForWindow(IntPtr handle, ref Guid iid, out PropertyStore store);

        sealed class HostedWindow : NativeWindow, IDisposable
        {
            internal HostedWindow(string title)
            {
                // The fixture sets properties on its own hidden HWNDs without
                // presenting windows or sending any mouse/keyboard input.
                CreateHandle(new CreateParams { Caption = title });
            }

            internal void SetApplication(string id)
            {
                var iid = typeof(PropertyStore).GUID;
                PropertyStore store;
                Marshal.ThrowExceptionForHR(SHGetPropertyStoreForWindow(Handle, ref iid, out store));
                var key = new PropertyKey { Format = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), Id = 5 };
                var value = id == null ? new PropertyValue() :
                            new PropertyValue { Type = 31, String = Marshal.StringToCoTaskMemUni(id) };
                try
                {
                    Marshal.ThrowExceptionForHR(store.SetValue(ref key, ref value));
                }
                finally
                {
                    Marshal.FreeCoTaskMem(value.String);
                    Marshal.ReleaseComObject(store);
                }
            }

            public void Dispose()
            {
                SetApplication(null);
                DestroyHandle();
            }
        }

        sealed class LabelWorker : INativeWorkerProcess
        {
            internal bool Blocked;
            internal int Sent, Waits;
            string id;
            public bool ExitConfirmed { get; private set; }

            public void Send(object frame)
            {
                Sent++;
                var ids = Wire.Array(Wire.Object(frame)["ids"]);
                Require(ids.Length == 1, "Only the proved App ID should be looked up");
                id = (string)ids[0];
            }

            public string Poll(int milliseconds)
            {
                if (milliseconds > 0)
                    Waits++;
                if (!Blocked)
                    return Wire.Json(new[] { Wire.Obj("id", id, "name", "Localized shell label") });
                Thread.Sleep(milliseconds);
                return null;
            }

            public bool Stop(int grace = 0) { ExitConfirmed = true; return true; }
            public void Dispose() { Stop(); }
        }

        static void Require(bool condition, string detail)
        {
            if (!condition)
                throw new InvalidOperationException(detail);
        }

        [MTAThread]
        static int Main()
        {
            try
            {
                Verify();
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
                return 1;
            }
        }

        static void Verify()
        {
            Native.Initialize();
            var request = new Request(CancellationToken.None, 10000);
            const string first = "ComputerUse.Tests.HostedOne", second = "ComputerUse.Tests.HostedTwo";
            using (var one = new HostedWindow("One document"))
            using (var other = new HostedWindow("Two document"))
            using (var sibling = new HostedWindow("Another One document"))
            using (var untagged = new HostedWindow("Host-only document"))
            {
                one.SetApplication(first);
                other.SetApplication(second);
                sibling.SetApplication(first);
                var store = new NativeReadStore();
                var a = store.RetainWindow(one.Handle, request);
                var b = store.RetainWindow(other.Handle, request);
                var c = store.RetainWindow(sibling.Handle, request);
                var d = store.RetainWindow(untagged.Handle, request);
                Require(a.Native.Pid == b.Native.Pid && b.Native.Pid == d.Native.Pid,
                        "Fixture must share one native process");
                Require(a.Application == c.Application && a.Application != b.Application &&
                        a.Application != d.Application && b.Application != d.Application,
                        "Exact window AUMIDs must separate shared-host applications");
                var desktop = NativeCapture.DesktopWindowRow(a, "One document", null, false, null);
                Require(Equals(desktop["app"], first) && Equals(desktop["selector"], a.Application) &&
                        Equals(desktop["title"], "One document"), "Desktop must keep App and document labels separate");
                Require(a.Native.Name == d.Native.Name && d.DisplayName == d.Native.Name,
                        "The owner process name must remain available independently of the Shell name");
                var restored = new NativeReadStore();
                restored.RestoreWindows(store.WindowCheckpoint());
                Require(restored.RetainWindow(one.Handle, request).Application == a.Application &&
                        restored.RetainWindow(other.Handle, request).Application == b.Application &&
                        restored.RetainWindow(untagged.Handle, request).Application == d.Application,
                        "Worker replacement must restore each shared-host application token");
                var window = b.Native;
                other.SetApplication(first);
                b = store.RetainWindow(other.Handle, request);
                Require(b.Application == a.Application && ReferenceEquals(b.Native, window) &&
                        b.Native.Handle == other.Handle, "Positive App changes must preserve the physical target");
                other.SetApplication(null);
                Require(store.RetainWindow(other.Handle, request).Application == a.Application,
                        "A missing property must not undo a previously proved App association");
                VerifyLabelBudget(a, d, request);
            }

            Console.WriteLine("Shared-host window App identity, checkpoint and rebinding passed");
        }

        static void VerifyLabelBudget(NativeWindowRecord item, NativeWindowRecord untagged, Request request)
        {
            var worker = new LabelWorker { Blocked = true };
            var entries = new List<NativeWindowRecord> { item, untagged };
            using (var names = new NativeApplicationNames(() => worker))
            {
                names.Enrich(entries, request, Wire.Now);
                Require(worker.Sent == 0 && item.DisplayName == item.ApplicationIdentifier,
                        "A completed frame keeps its budget and can deliver the exact ID without a label");
                names.Enrich(entries, request, null);
                int waits = worker.Waits;
                Require(worker.Sent == 1 && waits > 0 && item.DisplayName == item.ApplicationIdentifier,
                        "A blocked label leaves the original binding available");
                names.Enrich(entries, request, null);
                Require(worker.Sent == 1 && worker.Waits == waits, "Later reads must not wait again for a pending label");
                worker.Blocked = false;
                names.Enrich(entries, request, null);
                var row = NativeCapture.DesktopForegroundRow(item);
                Require(Equals(row["name"], "Localized shell label") && Equals(row["selector"], item.Application),
                        "A late label can enrich display without rebinding the App");
            }
            Require(worker.ExitConfirmed, "Disposing the read store must dispose the label worker");
        }
    }
}

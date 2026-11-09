// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Threading;

namespace ComputerUse.Windows
{
    // Real session mutex and native begin/finish lifetimes, with no UIA or input.
    internal static class NativeResourceLeaseTests
    {
        [DllImport("kernel32.dll")]
        static extern IntPtr GetCurrentProcess();

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetProcessHandleCount(IntPtr process, out uint count);

        sealed class Reader : INativeReadBackend
        {
            internal int Validations;
            public void Validate(Dictionary<string, object> target, Request r) { r.Check(); Validations++; }
            public object Inspect(Dictionary<string, object> target, Dictionary<string, object> query, Request r)
            {
                throw new InvalidOperationException("This test stops at operation admission.");
            }
            public void Release() { }
        }

        static NativeRuntime Runtime(Reader reader)
        {
            return new NativeRuntime("lease-test", reader, new NativeExecution(new NativeUiaObjects(null)));
        }

        static string Begin(NativeRuntime runtime, bool writable, Request r)
        {
            var target = Wire.Obj("session", "lease-test", "instance", "instance", "rootWindow", "window",
                                  "surface", "surface", "node", null, "capture", null);
            var intent = writable ? Wire.Obj("kind", "action", "parameters", Wire.Obj("action", "Window.Close"))
                                  : Wire.Obj("kind", "read", "parameters", Wire.Obj());
            var receipt = Wire.Object(runtime.Perform("begin", Wire.Obj("mode", writable ? "write" : "read", "target",
                                                                         target, "intent", intent), r));
            return (string)receipt["operation"];
        }

        static void Finish(NativeRuntime runtime, string operation)
        {
            var result = Wire.Object(runtime.Perform("finish", Wire.Obj("operation", operation), Request(1000)));
            Require(Equals(result["cleanup"], "complete"), "The operation must release its native resource scope.");
        }

        static Request Request(int timeout) { return new Request(CancellationToken.None, timeout); }

        static void Require(bool condition, string message)
        {
            if (!condition)
                throw new InvalidOperationException(message);
        }

        static void AssertRefusedBeforeInput(NativeRuntime runtime, Request r, string code)
        {
            try
            {
                Begin(runtime, true, r);
            }
            catch (Failure failure)
            {
                Require(failure.Code == code && !r.Effect && !r.Dispatched,
                        "A contending writer must keep its actual pre-input state: " + failure.Code);
                return;
            }
            throw new InvalidOperationException("The other process still owns the mutex, but a write was admitted.");
        }

        static void Hold()
        {
            var runtime = Runtime(new Reader());
            try
            {
                string operation = Begin(runtime, true, Request(12000));
                Console.WriteLine("held");
                Require(Console.ReadLine() == "finish", "The test owner must request finish explicitly.");
                Finish(runtime, operation);
                Console.WriteLine("finished");
            }
            finally { runtime.Close(); }
        }

        static void Expect(Process child, string message)
        {
            var line = child.StandardOutput.ReadLineAsync();
            Require(line.Wait(5000) && line.Result == message, "The child did not report: " + message);
        }

        static void WithHolder(Action<Process> body)
        {
            var start = new ProcessStartInfo(Assembly.GetExecutingAssembly().Location, "--holder") {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
                RedirectStandardOutput = true
            };
            using (var child = Process.Start(start))
            {
                try
                {
                    Expect(child, "held");
                    body(child);
                }
                finally
                {
                    if (!child.HasExited)
                        child.Kill();
                    Require(child.WaitForExit(5000), "The fixture process could not be reaped.");
                }
            }
        }

        static void Contention()
        {
            WithHolder(child =>
            {
                var reader = new Reader();
                var runtime = Runtime(reader);
                try
                {
                    Finish(runtime, Begin(runtime, false, Request(500)));
                    AssertRefusedBeforeInput(runtime, Request(150), "deadline_exceeded");
                    using (var cancel = new CancellationTokenSource(100))
                        AssertRefusedBeforeInput(runtime, new Request(cancel.Token, 2000), "cancelled");
                    Require(reader.Validations == 1, "Only the independent read was admitted while the other writer held its lease.");
                    child.StandardInput.WriteLine("finish");
                    Expect(child, "finished");
                    Require(child.WaitForExit(5000) && child.ExitCode == 0, "The first writer did not finish normally.");
                    Finish(runtime, Begin(runtime, true, Request(1000)));
                    Require(reader.Validations == 2, "The next write must admit normally after release.");
                }
                finally { runtime.Close(); }
            });
        }

        static void Abandonment()
        {
            WithHolder(child =>
            {
                string name = "Local\\ComputerUse-" + WindowsIdentity.GetCurrent().User.Value;
                // Keep the same kernel object alive across the owner's death.
                // Without this handle a fresh object could hide abandonment.
                using (Mutex.OpenExisting(name, MutexRights.Synchronize))
                {
                    child.Kill();
                    Require(child.WaitForExit(5000), "The fixture owner did not terminate.");
                    var runtime = Runtime(new Reader());
                    try { Finish(runtime, Begin(runtime, true, Request(1000))); }
                    finally { runtime.Close(); }
                }
            });
        }

        static uint CurrentHandleCount()
        {
            uint count;
            Require(GetProcessHandleCount(GetCurrentProcess(), out count), "Could not sample the test process handles.");
            return count;
        }

        static void CompletedLeasesCloseIdentityTokens()
        {
            var provider = new NativeUiaObjects(null);
            using (provider.Acquire(Request(150))) { }
            CurrentHandleCount();
            GC.Collect();
            GC.WaitForPendingFinalizers();

            // Check deterministic release rather than eventual finalization.
            Require(GC.TryStartNoGCRegion(32 * 1024 * 1024), "Could not start the bounded no-GC lease test.");
            uint before, after;
            try
            {
                before = CurrentHandleCount();
                for (int i = 0; i < 64; i++)
                    using (provider.Acquire(Request(150))) { }
                after = CurrentHandleCount();
            }
            finally { GC.EndNoGCRegion(); }
            Require(after == before, "64 completed native write leases changed the process handle count from " +
                    before + " to " + after + " before finalizers.");
        }

        static int Main(string[] args)
        {
            try
            {
                if (args.Length == 1 && args[0] == "--holder")
                    Hold();
                else if (args.Length == 1 && args[0] == "--token-lifetime")
                    CompletedLeasesCloseIdentityTokens();
                else
                {
                    Contention();
                    Abandonment();
                    CompletedLeasesCloseIdentityTokens();
                    Console.WriteLine("Windows resource lease contention, cancellation, abandonment and token cleanup passed.");
                }
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error);
                return 1;
            }
        }
    }
}

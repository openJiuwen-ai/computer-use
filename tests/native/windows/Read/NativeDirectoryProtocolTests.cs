using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    // Exercise the real private wire boundary without initializing UIA or
    // acquiring a desktop. A helper may report identity hints, never authority
    // over a window lifetime or an action to execute.
    internal static class NativeDirectoryProtocolTests
    {
        static int assertions;
        static void Assert(bool value, string detail)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(detail);
        }

        static void Reject(Action action, string detail)
        {
            bool failed = false;
            try
            {
                action();
            }
            catch (Exception)
            {
                failed = true;
            }

            Assert(failed, detail);
        }

        static string Reply(object rows, object notes, long id = 7)
        {
            return Wire.Json(Wire.Obj("id", id, "windows", rows, "notes", notes));
        }

        static void Main()
        {
            Assert(NativeWindowDiscovery.WorkerRequest("{\"id\":7}") == 7, "request identity lost");
            foreach (string invalid in new[]
            {
                "{}",
                "[]",
                "null",
                "{\"id\":0}",
                "{\"id\":-1}",
                "{\"id\":1.5}",
                "{\"id\":true}",
                "{\"id\":\"7\"}",
                "{\"id\":9007199254740992}",
                "{\"id\":7,\"action\":\"click\"}"
            }

            )
                Reject(() => NativeWindowDiscovery.WorkerRequest(invalid), "invalid/action request accepted: " + invalid);
            var before = new Dictionary<IntPtr, long>
            {
                {
                    new IntPtr(123),
                    42
                }
            };
            var rows = new object[]
            {
                Wire.Obj("handle", 123, "pid", 456),
                Wire.Obj("handle", 124, "pid", 456)
            };
            var result = NativeWindowDiscovery.WorkerReply(Reply(rows, new[] { "partial directory" }), 7, before);
            Assert(result.Windows.Count == 2 && result.Notes.Count == 1, "scalar metadata lost");
            Assert(result.Windows[0].Handle == new IntPtr(123) && result.Windows[0].Pid == 456, "native identity changed");
            Assert(result.Windows[0].Generation == 42, "parent pre-request lifetime not retained");
            Assert(result.Windows[1].Generation == 0, "unknown lifetime was invented by helper");
            Assert(result.Notes[0] == "partial directory", "partial evidence lost");
            Assert(NativeWindowDiscovery.WorkerReply(Reply(new object[0], new string[0]), 7, before).Windows.Count == 0, "empty directory is not a protocol failure");
            Reject(() => NativeWindowDiscovery.WorkerReply(Reply(rows, new string[0], 6), 7, before), "stale reply accepted");
            Reject(() => NativeWindowDiscovery.WorkerReply("{\"id\":7,\"windows\":[],\"notes\":[],\"generation\":99}", 7, before), "helper authority field accepted");
            Reject(() => NativeWindowDiscovery.WorkerReply("{\"id\":7,\"windows\":[]}", 7, before), "incomplete response accepted");
            foreach (object invalid in new object[]
            {
                Wire.Obj("handle", 0, "pid", 456),
                Wire.Obj("handle", 1.5, "pid", 456),
                Wire.Obj("handle", "123", "pid", 456),
                Wire.Obj("handle", 9007199254740992L, "pid", 456),
                Wire.Obj("handle", 123, "pid", 0),
                Wire.Obj("handle", 123, "pid", 4294967296L),
                Wire.Obj("handle", 123, "pid", true),
                Wire.Obj("handle", 123, "pid", 456, "generation", 99),
                Wire.Obj("handle", 123, "pid", 456, "node", "old-worker-node"),
                null
            }

            )
                Reject(() => NativeWindowDiscovery.WorkerReply(Reply(new[] { invalid }, new string[0]), 7, before), "invalid window hint accepted");
            Reject(() => NativeWindowDiscovery.WorkerReply(Reply(new object[513], new string[0]), 7, before), "unbounded windows accepted");
            Reject(() => NativeWindowDiscovery.WorkerReply(Reply(new object[0], new string[129]), 7, before), "unbounded notes accepted");
            Reject(() => NativeWindowDiscovery.WorkerReply(Reply(new object[0], new[] { new string ('x', 4097) }), 7, before), "unbounded note accepted");
            Reject(() => NativeWindowDiscovery.WorkerReply(Reply(new object[0], new object[] { 1 }), 7, before), "nontext note accepted");
            Reject(() => NativeWindowDiscovery.WorkerReply(Reply("windows", new string[0]), 7, before), "nonarray windows accepted");
            Reject(() => NativeWindowDiscovery.WorkerReply(Reply(new object[0], null), 7, before), "nonarray notes accepted");
            Console.WriteLine(Wire.Json(Wire.Obj("assertions", assertions, "scope", "production private metadata protocol; no GUI or provider claim")));
        }
    }
}

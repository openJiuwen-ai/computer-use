// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;

namespace ComputerUse.Windows
{
    internal static class NativeCaptureRecoveryTests
    {
        static readonly Dictionary<string, object> Desktop = Wire.Obj(
            "session", "session", "instance", null, "rootWindow", null, "surface", null, "node", null, "capture", null);
        static readonly Dictionary<string, object> Capture = Wire.Obj(
            "binding", Wire.Obj("session", "session", "instance", null, "rootWindow", null,
                                "surface", null, "node", null, "capture", "image"),
            "image", new[] { -300, 40, 1200, 700, 600, 350 },
            "canvas", new[] { -300, 40, 1200, 700, 1, 1 },
            "layout", "physical layout",
            "displays", new[] { new[] { -300, 40, 1200, 700, 1, 1 } },
            "windowBounds", new[] { -300, 40, 1200, 700, 600, 350 });

        sealed class Worker : INativeWorkerProcess
        {
            string reply;
            internal Dictionary<string, object> Captured = Capture;
            internal Dictionary<string, object> Bootstrap;
            public bool ExitConfirmed { get; private set; }

            public void Send(object value)
            {
                var request = Wire.Object(value);
                if (!request.ContainsKey("method"))
                {
                    Bootstrap = request;
                    reply = Wire.Json(Wire.Obj("ready", true));
                    return;
                }

                string method = Wire.Str(request, "method");
                object result = method == "hello" ? Wire.Obj("capabilities", new string[0], "globalInputAllowed", true)
                              : method == "begin" ? Wire.Obj("operation", "read")
                              : method == "capture" ? Wire.Obj("kind", "desktopCapture", "desktopRef", "image")
                              : method == "finish" ? Wire.Obj("cleanup", "complete")
                              : Wire.Obj("kind", "surfaces");
                var response = Wire.Obj("id", request["id"], "session", "session", "result", result,
                                        "windowCheckpoint", new object[0], "recycleReadWorker", method == "inspect");
                if (method == "capture")
                    response["captureCheckpoint"] = Captured;
                reply = Wire.Json(response);
            }

            public string Poll(int milliseconds) { var line = reply; reply = null; return line; }
            public bool Stop(int grace = 0) { ExitConfirmed = true; return true; }
            public void Dispose() { }
        }

        sealed class DirectoryWorker : INativeWorkerProcess
        {
            readonly bool blocked;
            object id;
            internal bool Sent { get; private set; }
            public bool ExitConfirmed { get; private set; }

            internal DirectoryWorker(bool blocked) { this.blocked = blocked; }

            public void Send(object value)
            {
                Sent = true;
                id = Wire.Object(value)["id"];
            }

            public string Poll(int milliseconds)
            {
                if (blocked)
                {
                    Thread.Sleep(milliseconds);
                    return null;
                }
                return Wire.Json(Wire.Obj("id", id, "windows", new[] { Wire.Obj("handle", 27, "pid", 42) },
                                          "notes", new string[0]));
            }

            public bool Stop(int grace = 0) { ExitConfirmed = true; return true; }
            public void Dispose() { }
        }

        static void Require(bool condition, string message)
        {
            if (!condition)
                throw new InvalidOperationException(message);
        }

        static Request Request() { return new Request(CancellationToken.None, 5000); }

        static int Main()
        {
            Environment.SetEnvironmentVariable("COMPUTER_USE_POINTER", "0");
            try
            {
                DesktopSupplementPreservesCompletedFrameBudget();
                DesktopCatalogueBindings();
                Recovery(Desktop, Capture, true);
                var window = Wire.Obj("session", "session", "instance", "missing", "rootWindow", "missing-window",
                                      "surface", "missing-surface", "node", null, "capture", null);
                var bound = new Dictionary<string, object>(window);
                bound["capture"] = "image";
                var lost = new Dictionary<string, object>(Capture);
                lost["binding"] = bound;
                Recovery(window, lost, false);
                SessionBoundary();
                Console.WriteLine("Windows capture recovery tests passed");
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
                return 1;
            }
        }

        static void DesktopSupplementPreservesCompletedFrameBudget()
        {
            var notes = new List<string>();
            var healthy = new DirectoryWorker(false);
            var longRequest = Request();
            try
            {
                var coverage = NativeWindowDiscovery.Supplement(longRequest, notes, longRequest.Deadline, () => healthy);
                Require(healthy.Sent && coverage.Windows.Count == 1 && coverage.Windows[0].Handle == new IntPtr(27),
                        "An affordable desktop supplement must still return its additional window evidence");
            }
            finally
            {
                NativeWindowDiscovery.Close();
                NativeWindowDiscovery.Release();
            }

            var blocked = new DirectoryWorker(true);
            var shortRequest = new Request(CancellationToken.None, 400);
            try
            {
                var coverage = NativeWindowDiscovery.Supplement(shortRequest, notes, shortRequest.Deadline, () => blocked);
                shortRequest.Check(); // The capture path must still be able to publish its completed frame.
                Require(coverage == null && !blocked.Sent && notes.Count == 1,
                        "A blocked desktop supplement must leave the completed frame's request alive and disclose omitted coverage");
            }
            catch (Failure failure)
            {
                throw new InvalidOperationException("Desktop directory supplement discarded the completed frame: " + failure.Code);
            }
            finally
            {
                NativeWindowDiscovery.Close();
                NativeWindowDiscovery.Release();
            }

            var bounded = new DirectoryWorker(true);
            var boundedRequest = Request();
            notes.Clear();
            try
            {
                var coverage = NativeWindowDiscovery.Supplement(boundedRequest, notes, Wire.Now + 1500, () => bounded);
                boundedRequest.Check();
                Require(coverage == null && bounded.Sent && bounded.ExitConfirmed && notes.Count == 1,
                        "An admitted but blocked desktop supplement must confirm cleanup and degrade to native rows");
            }
            finally
            {
                NativeWindowDiscovery.Close();
                NativeWindowDiscovery.Release();
            }
        }

        static void DesktopCatalogueBindings()
        {
            var item = new NativeWindowRecord
            {
                Application = "application:retained",
                Native = new Window { Name = "Fixture host", Ref = "window:retained",
                                      Selector = "instance:retained", Scope = "surface:retained" }
            };
            var rectangle = Wire.Obj("x", 1, "y", 2, "width", 80, "height", 60);
            var row = NativeCapture.DesktopWindowRow(item, "Fixture document", rectangle, false, null);
            var front = NativeCapture.DesktopForegroundRow(item);
            Require(Equals(row["selector"], item.Application) && Equals(front["selector"], item.Application),
                    "Desktop App references must use the same application ID as the surface directory");
            var window = Wire.Object(row["window"]);
            Require(Equals(window["window"], item.Native.Ref) && Equals(window["surface"], item.Native.Scope) &&
                    Equals(window["instance"], item.Native.Selector), "Desktop window reference changed its retained identity");
            Require(window["node"] == null && Equals(window["accessibility"], false) &&
                    window["parent"] == null && Equals(window["parentState"], "none") && window["modal"] == null,
                    "Desktop window rows must not invent UIA roots or modality");
            var unresolved = Wire.Object(NativeCapture.DesktopWindowRow(item, "Fixture document", rectangle,
                                                                        true, null)["window"]);
            Require(unresolved["parent"] == null && Equals(unresolved["parentState"], "unknown"),
                    "An unconfirmed native owner must remain unknown");
            var owner = new NativeWindowRecord { Native = new Window { Ref = "window:owner" } };
            var owned = Wire.Object(NativeCapture.DesktopWindowRow(item, "Fixture document", rectangle,
                                                                   true, owner)["window"]);
            Require(Equals(owned["parent"], owner.Native.Ref) && Equals(owned["parentState"], "known"),
                    "An evidenced native owner must retain its own window ID");
        }

        static void Recovery(Dictionary<string, object> target, Dictionary<string, object> capture, bool retained)
        {
            var first = new Worker { Captured = capture };
            var second = new Worker();
            var children = new Queue<Worker>(new[] { first, second });
            var runtime = new NativeIsolatedRuntime("session", true, children.Dequeue);
            var begin = Wire.Obj("mode", "read", "target", target);
            try
            {
                runtime.Perform("begin", begin, Request());
                runtime.Perform("capture", Wire.Obj("operation", "read", "target", target), Request());
                runtime.Perform("inspect", Wire.Obj("operation", "read", "query", Wire.Obj("kind", "surfaces")), Request());
                runtime.Perform("finish", Wire.Obj("operation", "read"), Request());
                Require(first.ExitConfirmed, "the previous read worker must exit before replacement");
                runtime.Perform("begin", begin, Request());
                object checkpoint;
                Require(second.Bootstrap.TryGetValue("captures", out checkpoint),
                        "read worker replacement lost the delivered capture geometry");
                var restored = Wire.Array(checkpoint);
                if (retained)
                {
                    Require(restored.Length == 1 && Wire.Json(restored.Single()) == Wire.Json(capture),
                            "the replacement must receive the original token, layout and scale unchanged");
                    var point = NativePointerCapture.Restore(restored.Single(), "session").Point(100, 75);
                    Require(point.X == -100 && point.Y == 190, "restored scaling must preserve the actual point");
                }
                else
                    Require(restored.Length == 0, "a capture of an unretained HWND must not survive replacement");
                runtime.Perform("finish", Wire.Obj("operation", "read"), Request());
            }
            finally { runtime.Close(); }
        }

        static void SessionBoundary()
        {
            try
            {
                NativePointerCapture.Restore(Capture, "other-session");
                throw new InvalidOperationException("capture checkpoint crossed session boundary");
            }
            catch (Failure error)
            {
                Require(error.Code == "stale_target", "foreign capture must be recognized as stale");
            }
        }
    }
}

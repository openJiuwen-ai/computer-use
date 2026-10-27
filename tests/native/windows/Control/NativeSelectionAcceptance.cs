// Real discovery/identity/read tests against independent disposable windows.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;

namespace ComputerUse.Windows
{
    internal static class NativeSelectionAcceptance
    {
        static int assertions;
        static string output;
        static void Save(string name, object value)
        {
            File.WriteAllText(Path.Combine(output, name + ".json"), Wire.Json(value), new UTF8Encoding(false));
        }

        static void Assert(bool value, string message)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(message);
        }

        static Request Request()
        {
            return new Request(CancellationToken.None, 30000);
        }

        static Dictionary<string, object> Target(NativeWindowRecord w = null, string node = null)
        {
            return Wire.Obj("session", "selection", "instance", w == null ? null : w.Native.Selector, "rootWindow", w == null ? null : w.Native.Ref, "surface", w == null ? null : w.Native.Scope, "node", node, "capture", null);
        }

        static Dictionary<string, object> Read(NativeRuntime runtime, Dictionary<string, object> query, Dictionary<string, object> target = null)
        {
            if (target == null)
                target = Target();
            var operation = Wire.Object(runtime.Perform("begin", Wire.Obj("mode", "read", "target", target, "intent", Wire.Obj("kind", "read", "parameters", Wire.Obj())), Request()));
            try
            {
                return Wire.Object(runtime.Perform("inspect", Wire.Obj("operation", operation["operation"], "target", target, "query", query), Request()));
            }
            finally
            {
                runtime.Perform("finish", Wire.Obj("operation", operation["operation"]), Request());
            }
        }

        static Dictionary<string, object> Command(string name)
        {
            File.WriteAllText(Path.Combine(output, name + ".command"), "");
            double until = Wire.Now + 5000;
            string path = Path.Combine(output, name + ".json");
            while (!File.Exists(path))
            {
                if (Wire.Now > until)
                    throw new InvalidOperationException("Fixture did not complete " + name);
                Thread.Sleep(50);
            }

            return Wire.Object(Wire.Serializer().DeserializeObject(File.ReadAllText(path)));
        }

        [MTAThread]
        static int Main(string[] args)
        {
            output = args[0];
            Native.Initialize();
            var truth = Wire.Object(Wire.Serializer().DeserializeObject(File.ReadAllText(Path.Combine(output, "ready.json"))));
            IntPtr foreground = Native.GetForegroundWindow();
            Native.POINT cursor;
            Native.GetCursorPos(out cursor);
            var changes = new List<object>();
            var stop = new ManualResetEvent(false);
            var sampler = new Thread(() =>
            {
                while (!stop.WaitOne(20))
                {
                    Native.POINT p;
                    Native.GetCursorPos(out p);
                    if (foreground != Native.GetForegroundWindow() || cursor.X != p.X || cursor.Y != p.Y)
                        lock (changes)
                        {
                            if (changes.Count < 100)
                                changes.Add(Wire.Obj("at", Wire.Now, "front", Native.GetForegroundWindow().ToInt64(), "cursor", new[] { p.X, p.Y }));
                        }
                }
            });
            sampler.IsBackground = true;
            sampler.Start();
            var reader = new NativeReadBackend("selection");
            var runtime = new NativeRuntime("selection", reader);
            try
            {
                var apps = Read(runtime, Wire.Obj("kind", "applications"));
                Save("applications", apps);
                Assert((string)apps["scope"] == "runningWindows", "Window-only catalogue scope missing");
                var notes = new List<string>();
                var windows = reader.Store.Catalog(Request(), notes);
                var selected = windows.Single(w => w.Native.Handle.ToInt64() == Convert.ToInt64(truth["second"]));
                var selectedTarget = Target(selected);
                var app = Wire.Array(apps["applications"]).Select(Wire.Object).Single(a => (string)a["id"] == selected.Application);
                var links = Wire.Array(app["windows"]).Select(Wire.Object).ToArray();
                Assert(links.Length == 4, "Same process's four distinct windows not discoverable");
                Assert(links.Any(w => (string)w["selector"] == selected.Native.Selector && (string)w["window"] == selected.Native.Ref), "Desktop selector cannot resolve to selected window");
                var directory = Read(runtime, Wire.Obj("kind", "surfaces", "application", selected.Application));
                Save("surfaces", directory);
                var rows = Wire.Array(directory["surfaces"]).Select(Wire.Object).ToArray();
                Assert(rows.Count(w => (string)w["title"] == "Same document title") == 2, "Same-title windows collapsed");
                Assert(rows.Any(w => (string)w["title"] == ""), "Untitled window omitted");
                var first = windows.Single(w => w.Native.Handle.ToInt64() == Convert.ToInt64(truth["first"]));
                var owned = windows.Single(w => w.Native.Handle.ToInt64() == Convert.ToInt64(truth["owned"]));
                Assert((string)rows.Single(w => (string)w["window"] == owned.Native.Ref)["parent"] == first.Native.Ref, "Native owner relationship lost");
                var fields = Wire.Obj("kind", "nodes", "fields", new[] { "Name", "Value.Value", "IsPassword", "SupportedPatterns" }, "maxNodes", 100, "maxDepth", 10);
                var tree = Read(runtime, fields, Target(selected, selected.RootNode));
                Save("selected-tree", tree);
                Assert(Wire.Array(tree["nodes"]).Select(Wire.Object).Select(n => Wire.Object(n["values"])).Any(v => v.ContainsKey("Value.Value") && (string)v["Value.Value"] == "document B"), "Selected window contents are not B");
                Command("hide");
                windows = reader.Store.Catalog(Request(), new List<string>());
                Assert(!windows.Contains(selected), "Hidden window still claimed visible");
                Command("show");
                windows = reader.Store.Catalog(Request(), new List<string>());
                Assert(windows.Contains(selected), "Hide/show changed retained window identity");
                selected.Native.Validate();
                var replacement = Command("replace");
                Thread.Sleep(150); // Allow destroy WinEvent to arrive.
                windows = reader.Store.Catalog(Request(), new List<string>());
                var next = windows.Single(w => w.Native.Handle.ToInt64() == Convert.ToInt64(replacement["second"]));
                Assert(next.Native.Selector != selected.Native.Selector && next.Native.Ref != selected.Native.Ref, "New same-title window reused old public identity");
                bool refused = false;
                try
                {
                    reader.Store.Validate(selectedTarget, Request(), true);
                }
                catch (Failure error)
                {
                    refused = error.Code == "stale_target";
                }

                Assert(refused, "Closed App retargeted another window");
                Save("after-replacement", Read(runtime, Wire.Obj("kind", "applications")));
                stop.Set();
                sampler.Join();
                Assert(changes.Count == 0, "Foreground or pointer changed; inspect concurrent desktop use");
                Save("result", Wire.Obj("status", "passed", "assertions", assertions, "desktopChanged", false));
                return 0;
            }
            catch (Exception error)
            {
                Save("result", Wire.Obj("status", "failed", "assertions", assertions, "error", error.ToString()));
                return 1;
            }
            finally
            {
                runtime.Close();
                stop.Set();
                sampler.Join();
                stop.Dispose();
                Save("desktop-changes", changes);
                File.WriteAllText(Path.Combine(output, "stop.command"), "");
            }
        }
    }
}

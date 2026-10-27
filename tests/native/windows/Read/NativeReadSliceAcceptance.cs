using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace ComputerUse.Windows
{
    // Opt-in interactive mechanism check. This does not replace installed MCP
    // acceptance: it calls the production collector on its own native window.
    internal static class NativeReadSliceAcceptance
    {
        static int assertions;
        static void Assert(bool value, string detail)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(detail);
        }

        static Request Request()
        {
            return new Request(CancellationToken.None, 30000);
        }

        static Dictionary<string, object> Binding(NativeWindowRecord window = null, string node = null)
        {
            return Wire.Obj("session", "slice", "instance", window == null ? null : window.Native.Selector, "rootWindow", window == null ? null : window.Native.Ref, "surface", window == null ? null : window.Native.Scope, "node", node, "capture", null);
        }

        [MTAThread]
        static int Main()
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            Native.Initialize();
            var ready = new ManualResetEvent(false);
            Form form = null;
            TextBox guard = null;
            IntPtr handle = IntPtr.Zero;
            Exception startup = null;
            int clicks = 0;
            var ui = new Thread(() =>
            {
                try
                {
                    form = new Form
                    {
                        Text = "Computer Use read slice specimen",
                        ClientSize = new Size(500, 170),
                        StartPosition = FormStartPosition.CenterScreen
                    };
                    guard = new TextBox
                    {
                        Text = "GUARD_UNCHANGED",
                        AccessibleName = "Read-only guard",
                        ReadOnly = true,
                        Left = 20,
                        Top = 30,
                        Width = 350
                    };
                    var button = new Button
                    {
                        Text = "Visible action",
                        Left = 20,
                        Top = 80,
                        Width = 150
                    };
                    button.Click += (s, e) => clicks++;
                    form.Controls.AddRange(new Control[] { guard, button });
                    form.Shown += (s, e) =>
                    {
                        handle = form.Handle;
                        ready.Set();
                    };
                    Application.Run(form);
                }
                catch (Exception error)
                {
                    startup = error;
                    ready.Set();
                }
            });
            ui.SetApartmentState(ApartmentState.STA);
            ui.Start();
            var results = new List<object>();
            try
            {
                Assert(ready.WaitOne(5000), "Specimen startup timed out");
                if (startup != null)
                    throw startup;
                var reader = new NativeReadBackend("slice");
                try
                {
                    var window = reader.Store.Catalog(Request(), new List<string>()).Single(w => w.Native.Handle == handle);
                    var request = Request();
                    request.ObservationDeadline = Wire.Now + 4000;
                    reader.Inspect(Binding(), Wire.Obj("kind", "surfaces", "application", window.Application), request);
                    var target = Binding(window, window.RootNode);
                    var fields = new[]
                    {
                        "Name",
                        "Text.DocumentRange",
                        "Text.Source",
                        "Text.Selection"
                    };
                    var query = Wire.Obj("kind", "nodes", "fields", fields, "maxNodes", 1, "maxDepth", 0);
                    request = Request();
                    request.ObservationDeadline = Wire.Now + 4000;
                    reader.Inspect(target, query, request);
                    int preserved = 0, timeouts = 0;
                    foreach (int milliseconds in new[]
                    {
                        49,
                        40,
                        30,
                        20,
                        10
                    }

                    )
                    {
                        request = Request();
                        request.ObservationDeadline = Wire.Now + milliseconds;
                        var observed = Wire.Object(reader.Inspect(target, query, request));
                        foreach (var node in Wire.Array(observed["nodes"]).Select(Wire.Object))
                        {
                            if (node["properties"] == null || !Equals(node["kind"], "element"))
                                continue;
                            var values = NativeObservationAssertions.Fields(node);
                            var properties = NativeObservationAssertions.Properties(node);
                            Assert(fields.All(field => properties.Any(p => Equals(Wire.Array(p["key"])[1], field))), "A deferred field disappeared without a status");
                            if (values.ContainsKey("Name") && Equals(values["Name"], "Computer Use read slice specimen"))
                                preserved++;
                            timeouts += properties.Count(property => Equals(property["state"], "timeout"));
                        }

                        results.Add(Wire.Obj("softBudgetMs", milliseconds, "observation", observed));
                    }

                    Assert(preserved > 0 && timeouts > 0, "Short slices did not retain captured structure and explicit unread fields");
                    bool unchanged = false;
                    form.Invoke((Action)(() => unchanged = guard.Text == "GUARD_UNCHANGED" && clicks == 0));
                    Assert(unchanged, "Observation changed specimen content or invoked its button");
                }
                finally
                {
                    reader.Dispose();
                    reader.Release();
                }

                Console.WriteLine(Wire.Json(Wire.Obj("status", "passed", "assertions", assertions, "results", results, "scope", "controlled soft deadlines, production collector and Windows UIA; own native window; no input")));
                return 0;
            }
            catch (Exception error)
            {
                Console.WriteLine(Wire.Json(Wire.Obj("status", "failed", "error", error.ToString(), "results", results)));
                return 1;
            }
            finally
            {
                if (form != null && form.IsHandleCreated)
                    form.BeginInvoke((Action)(() => form.Close()));
                ui.Join(3000);
                ready.Dispose();
            }
        }
    }
}

// Windows-only acceptance against disposable WPF/WinForms specimens.
// Compile with production sources and /main:ComputerUse.Windows.NativeReadAcceptance.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Windows;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    internal static class NativeReadAcceptance
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

        static Dictionary<string, object> Binding(NativeWindowRecord w = null, string node = null)
        {
            return Wire.Obj("session", "acceptance", "instance", w == null ? null : w.Native.Selector, "rootWindow", w == null ? null : w.Native.Ref, "surface", w == null ? null : w.Native.Scope, "node", node, "capture", null);
        }

        static Dictionary<string, object> Read(NativeRuntime runtime, Dictionary<string, object> binding, Dictionary<string, object> query)
        {
            var opened = Wire.Object(runtime.Perform("begin", Wire.Obj("mode", "read", "target", binding, "intent", Wire.Obj("kind", "read", "parameters", Wire.Obj())), Request()));
            string operation = (string)opened["operation"];
            try
            {
                return Wire.Object(runtime.Perform("inspect", Wire.Obj("operation", operation, "target", binding, "query", query), Request()));
            }
            finally
            {
                runtime.Perform("finish", Wire.Obj("operation", operation), Request());
            }
        }

        static Dictionary<string, object> Values(Dictionary<string, object> batch, string id)
        {
            return NativeObservationAssertions.Fields(Node(batch, id));
        }

        static Dictionary<string, object> Node(Dictionary<string, object> batch, string id)
        {
            return NativeObservationAssertions.Node(batch, id);
        }

        static string Selection(Dictionary<string, object> values)
        {
            var ranges = Wire.Array(Wire.Object(values["Text.Selection"])["ranges"]);
            return String.Join("|", ranges.Select(r => (string)Wire.Object(Wire.Object(r)["values"])["text"]));
        }

        // Exercise the production COM value conversion and raw rectangle
        // encoder independently of display visibility or pointer eligibility.
        static void Geometry()
        {
            foreach (var raw in new[]
            {
                new[]
                {
                    -1920.5,
                    -240.25,
                    400.75,
                    32.5
                },
                new[]
                {
                    0.0,
                    0.0,
                    0.0,
                    0.0
                },
                new[]
                {
                    -25.0,
                    12.0,
                    0.0,
                    30.0
                },
                new[]
                {
                    10.0,
                    -3.0,
                    40.0,
                    0.0
                }
            }

            )
            {
                var rectangle = (Rect)UiaClient.Value(raw, AutomationElement.BoundingRectangleProperty.Id);
                var encoded = Wire.Object(NativeReadText.Bounds(rectangle));
                var roundTrip = Wire.Object(Wire.Serializer().DeserializeObject(Wire.Json(encoded)));
                Assert(new[] { "x", "y", "width", "height" }.Select((key, i) => Convert.ToDouble(roundTrip[key]) == raw[i]).All(v => v), "Raw physical bounds changed negative coordinates, fractional values, or zero sizes");
            }

            Assert(UiaClient.Value(null, AutomationElement.BoundingRectangleProperty.Id) == null, "A missing rectangle became an origin box");
            Assert(NativeReadText.Bounds(Rect.Empty) == null, "Rect.Empty became finite coordinates");
            var unsupported = NativeReadBackend.Status(new NativeFieldUnavailable("unsupported"));
            Assert((string)unsupported["state"] == "unsupported", "Unsupported geometry lost its native field status");
            foreach (var raw in new[]
            {
                new[]
                {
                    Double.NaN,
                    0,
                    1,
                    1
                },
                new[]
                {
                    0.0,
                    Double.PositiveInfinity,
                    1,
                    1
                },
                new[]
                {
                    0.0,
                    0,
                    Double.NaN,
                    1
                },
                new[]
                {
                    0.0,
                    0,
                    1,
                    Double.PositiveInfinity
                },
                new[]
                {
                    0.0,
                    0,
                    -1,
                    1
                }
            }

            )
            {
                bool rejected = false;
                try
                {
                    NativeReadText.Bounds((Rect)UiaClient.Value(raw, AutomationElement.BoundingRectangleProperty.Id));
                }
                catch (Exception error)
                {
                    rejected = true;
                    var status = NativeReadBackend.Status(error);
                    Assert((string)status["state"] == "error", "Invalid geometry was not a field error");
                    Assert(status.ContainsKey("rawResult"), "Malformed acquired geometry lost its original native payload");
                }

                Assert(rejected, "Invalid geometry became usable raw bounds");
            }
        }

        [MTAThread]
        static int Main(string[] args)
        {
            output = args[1];
            Directory.CreateDirectory(output);
            Geometry();
            var fixture = Wire.Object(Wire.Serializer().DeserializeObject(File.ReadAllText(args[0])));
            var expected = Wire.Object(fixture["expected"]);
            string kind = (string)fixture["kind"];
            IntPtr handle = new IntPtr(Convert.ToInt64(fixture["hwnd"]));
            Native.Initialize(); // Establish physical pixel coordinates before sampling.
            IntPtr foreground = Native.GetForegroundWindow();
            Native.POINT pointer;
            Native.GetCursorPos(out pointer);
            var desktopChanges = new List<object>();
            var stop = new ManualResetEvent(false);
            var sampler = new Thread(() =>
            {
                while (!stop.WaitOne(20))
                {
                    Native.POINT current;
                    Native.GetCursorPos(out current);
                    var front = Native.GetForegroundWindow();
                    if (front != foreground || current.X != pointer.X || current.Y != pointer.Y)
                        lock (desktopChanges)
                        {
                            if (desktopChanges.Count < 100)
                                desktopChanges.Add(Wire.Obj("at", Wire.Now, "foreground", front.ToInt64(), "cursor", new[] { current.X, current.Y }));
                        }
                }
            });
            sampler.IsBackground = true;
            sampler.Start();
            try
            {
                Action<string> trace = args.Contains("--trace") ? (Action<string>)(message => File.AppendAllText(Path.Combine(output, "calls.log"), DateTime.UtcNow.ToString("o") + " " + message + "\n")) : null;
                var reader = new NativeReadBackend("acceptance", trace);
                var runtime = new NativeRuntime("acceptance", reader);
                var window = reader.Store.Catalog(Request(), new List<string>()).Single(w => w.Native.Handle == handle);
                var directory = Read(runtime, Binding(), Wire.Obj("kind", "surfaces", "application", window.Application));
                Save("directory", directory);
                Assert(window.RootNode != null, "Fixture root was not discovered");
                string[] fields = NativeReadSchema.Properties.Keys.Concat(new[] { "SupportedPatterns", "Text.DocumentRange", "Text.IsReadOnly", "Text.SupportedTextSelection", "Text.Selection", "UIA.Collection", "UIA.ReadSource", "UIA.ReferenceScope" }).ToArray();
                var query = Wire.Obj("kind", "nodes", "fields", fields, "maxNodes", 1000, "maxDepth", 24, "followRelations", new[] { "Text.Children", "Selection.Selection", "Table.RowHeaders", "Table.ColumnHeaders", "TableItem.RowHeaderItems", "TableItem.ColumnHeaderItems" });
                var target = Binding(window, window.RootNode);
                var first = Read(runtime, target, query);
                Save("first", first);
                var second = Read(runtime, target, query);
                Save("second", second);
                if (kind == "slow")
                {
                    foreach (var batch in new[]
                    {
                        first,
                        second
                    }

                    )
                    {
                        Assert((string)Values(batch, "nestedAction")["ControlType"] == "ControlType.Button", "Slow text hid a sibling control");
                        Assert((string)Values(batch, "nestedAction")["Name"] == "Nested action", "Slow text hid a sibling label");
                        Assert(Wire.Array(NativeObservationAssertions.Data(NativeObservationAssertions.Property(Node(batch, "nestedAction"), "actions")["value"])).Length > 0, "Slow text hid sibling actions");
                        Assert((string)NativeObservationAssertions.Property(Node(batch, "slowText"), "Text.DocumentRange")["state"] == "timeout", "Slow field was not explicitly incomplete");
                        foreach (object rawNode in Wire.Array(batch["nodes"]))
                        {
                            var row = Wire.Object(rawNode);
                            if (row["properties"] == null || !Equals(row["kind"], "element"))
                                continue;
                            Assert(fields.All(f => NativeObservationAssertions.Properties(row).Any(p => Equals(Wire.Array(p["key"])[1], f))), "Deferred field silently omitted");
                        }
                    }
                }
                else if (kind != "window")
                {
                    Assert((string)Values(second, "searchInput")["ControlType"] == "ControlType.Edit", "Native COM control type mapping lost Edit");
                    Assert((string)Values(second, "nestedAction")["ControlType"] == "ControlType.Button", "Native COM control type mapping lost Button");
                    Assert((string)Values(second, "searchInput")["Value.Value"] == (string)expected["search_text"], "Input Value text mismatch");
                    Assert(Selection(Values(second, "searchInput")) == (string)expected["search_selection"], "Input selection mismatch");
                    Assert(Selection(Values(second, "documentBody")) == (string)expected["document_selection"], "Document selection mismatch");
                    Assert(((string)Values(second, "documentBody")["Text.DocumentRange"]).Contains("selected phrase"), "Document body omitted");
                    Assert(Equals(Values(second, "readOnlyInput")["Value.IsReadOnly"], true), "Read-only state omitted");
                    Assert((string)Node(first, "searchInput")["id"] == (string)Node(second, "searchInput")["id"], "Identity changed across read batches");
                    var button = Node(second, "nestedAction");
                    Assert(!NativeObservationAssertions.Fields(button).ContainsKey("Toggle.ToggleState"), "Unsupported Toggle became a default Off");
                    if (kind == "wpf")
                    {
                        var badge = Values(second, "rawBadge");
                        Assert((string)badge["Name"] == (string)expected["raw_only_visible_text"] && Equals(badge["IsControlElement"], false), "Raw-only meaningful text was filtered");
                        Assert(Convert.ToDouble(Values(second, "levelSlider")["RangeValue.Value"]) == 7, "Slider numeric value lost");
                        Assert((string)Values(second, "embeddedAction")["Name"] == (string)expected["embedded_button_name"], "Embedded control lost");
                        var table = Wire.Array(second["nodes"]).Select(Wire.Object).First(n =>
                        {
                            var values = NativeObservationAssertions.Fields(n);
                            return values.ContainsKey("Grid.ColumnCount") && Convert.ToInt32(values["Grid.ColumnCount"]) == 2;
                        });
                        var cells = Read(runtime, Binding(window, (string)table["id"]), Wire.Obj("kind", "relation", "property", "Grid.Cells", "cursor", null, "limit", 128));
                        Save("grid-cells", cells);
                        Assert(NativeObservationAssertions.Targets(cells).Length == 2, "Grid.GetItem discovery lost cells");
                        var list = Node(second, "virtualList");
                        Assert(Equals(Wire.Object(NativeObservationAssertions.Fields(list)["UIA.Collection"])["mayContainVirtualizedItems"], true), "Virtualization coverage missing");
                        var items = Read(runtime, Binding(window, (string)list["id"]), Wire.Obj("kind", "relation", "property", "ItemContainer.Items", "cursor", null, "limit", 256));
                        Save("logical-items", items);
                        Assert((string)NativeObservationAssertions.RelationDetail(items, "state") == "complete" && NativeObservationAssertions.Targets(items).Length == Convert.ToInt32(expected["list_total"]), "Logical item discovery incomplete");
                        var after = Read(runtime, target, query);
                        Save("after-items", after);
                        Assert(Equals(Values(after, "virtualList")["Scroll.VerticalScrollPercent"], Values(second, "virtualList")["Scroll.VerticalScrollPercent"]), "Read changed list scroll position");
                    }
                }
                else
                    Assert(Wire.Array(second["nodes"]).Length > 1, "Application returned only a window shell; inspect partial statuses");
                // Depth limits must preserve known-but-unread objects.
                var bounded = Read(runtime, target, Wire.Obj("kind", "nodes", "fields", new[] { "Name" }, "maxNodes", 1, "maxDepth", 0));
                Save("bounded", bounded);
                Assert(Wire.Array(bounded["nodes"]).Select(Wire.Object).Any(n => n["properties"] == null && Equals(n["kind"], "element")), "Bounded read silently lost descendants");
                runtime.Close();
                stop.Set();
                sampler.Join();
                Save("desktop-changes", desktopChanges);
                Assert(desktopChanges.Count == 0, "Desktop changed during read; inspect whether there was concurrent user input");
                Save("result", Wire.Obj("status", "passed", "kind", kind, "assertions", assertions, "desktopChanged", false));
                return 0;
            }
            catch (Exception error)
            {
                Save("result", Wire.Obj("status", "failed", "kind", kind, "assertions", assertions, "error", error.ToString()));
                return 1;
            }
            finally
            {
                stop.Set();
                sampler.Join();
                stop.Dispose();
                Save("desktop-changes", desktopChanges);
            }
        }
    }
}

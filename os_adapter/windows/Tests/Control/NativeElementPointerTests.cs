using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class ElementPointerProbe : INativePointerProvider, INativeElementPointerProvider
    {
        internal bool Ready = true, Gone, BreakAfterClick;
        internal int Sent, Activated;
        internal readonly List<uint> Flags = new List<uint>();
        public NativePointerCapture Capture(Dictionary<string, object> target, Request r)
        {
            throw new InvalidOperationException("element gestures must not acquire a screenshot");
        }

        public NativePointerState Read(Dictionary<string, object> target, NativePointerPoint point, string kind, bool dragging, Request r)
        {
            throw new InvalidOperationException("element gestures must retain the element binding");
        }

        public NativeElementPointerTarget Element(Dictionary<string, object> target, string[] within, Request r)
        {
            return new NativeElementPointerTarget
            {
                Point = new NativePointerPoint(30, 40),
                Canvas = new NativeCaptureGeometry(0, 0, 1000, 1000, 1)
            };
        }

        public NativePointerState ReadElement(Dictionary<string, object> target, string[] within, NativeElementPointerTarget point, Request r)
        {
            r.Check();
            if (Gone)
                throw new Failure("stale_target", "the fixture element retired after the first click");
            return new NativePointerState
            {
                Ready = Ready,
                Foreground = Ready,
                ForegroundReady = Ready,
                Native = this
            };
        }

        public void RequireIdle(Request r)
        {
            r.Check();
        }

        public void Activate(NativePointerState state, Request r)
        {
            Activated++;
            Ready = true;
        }

        public int Send(NativePointerEvent[] events)
        {
            Sent++;
            Flags.AddRange(events.Select(e => e.Flags));
            if (BreakAfterClick)
                Gone = true;
            return events.Length;
        }
    }

    internal sealed class ElementObjectProbe : INativeObjectProvider
    {
        internal int Actions, Replacements;
        internal string Value = "old";
        internal bool IgnoreReplacement;
        public IDisposable Acquire(Request r)
        {
            return null;
        }

        public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request r)
        {
            return new NativeObjectState
            {
                Enabled = true,
                Replaceable = true,
                Role = "ControlType.Edit",
                Label = "Fixture element",
                ActionsComplete = true,
                Actions = new[]
                {
                    "Invoke.Invoke"
                },
                TextValueKnown = true,
                TextValue = Value
            };
        }

        public Action Prepare(NativeObjectState state, string unit, string action, string text, Request r)
        {
            return () =>
            {
                if (unit == "native_text_edit")
                {
                    Replacements++;
                    if (!IgnoreReplacement)
                        Value = text;
                }
                else
                    Actions++;
            };
        }
    }

    internal static class NativeElementPointerTests
    {
        static int assertions;
        static void Assert(bool yes, string detail)
        {
            assertions++;
            if (!yes)
                throw new InvalidOperationException(detail);
        }

        static Request Request()
        {
            return new Request(System.Threading.CancellationToken.None, 3000);
        }

        static Dictionary<string, object> Target(string node = "field")
        {
            return Wire.Obj("session", "test", "application", "app", "instance", "window", "surface", "surface", "rootWindow", "window", "node", node, "capture", null);
        }

        static Dictionary<string, object> Run(NativeExecution execution, NativeWriteOperation operation, string unit, bool foreground = false)
        {
            var facts = Wire.Object(execution.Inspect(operation, Target(), new string[0], Request()));
            var primitive = Wire.Array(facts["primitives"]).Select(Wire.Object).Single(p => (string)p["unit"] == unit);
            Assert(NativeWriteOperation.Same(Wire.Object(facts["binding"]), Target()), "inspection changed the original element");
            return Wire.Object(execution.Execute(operation, Wire.Obj("kind", foreground ? "prepare_receiver" : unit, "primitiveId", primitive["id"], "target", Target(), "foreground", foreground), Request()));
        }

        static void Clicks()
        {
            foreach (string button in new[]
            {
                "left",
                "right",
                "middle"
            }

            )
                foreach (int count in new[]
                {
                    1,
                    2
                }

                )
                {
                    var provider = new ElementPointerProbe();
                    var objects = new ElementObjectProbe();
                    var execution = new NativeExecution(objects, pointer: new NativePointer(provider, "exclusive", false));
                    var intent = Wire.Obj("kind", "click", "parameters", Wire.Obj("button", button, "count", count));
                    execution.Validate(intent, Target(null)); // Lazy Element query has not selected its node yet.
                    var operation = new NativeWriteOperation(Target(null), intent);
                    var result = Run(execution, operation, "pointer_sequence");
                    uint down = button == "left" ? 2u : button == "right" ? 8u : 32u;
                    uint up = button == "left" ? 4u : button == "right" ? 16u : 64u;
                    Assert((string)result["dispatch"] == "dispatched", "gesture was not sent");
                    Assert(provider.Sent == count && provider.Flags.Count(f => f == down) == count && provider.Flags.Count(f => f == up) == count, "button/count was not preserved");
                    Assert(objects.Actions == 0 && provider.Activated == 0, "gesture replayed an object action or prepared a ready target");
                    Assert((string)result["cleanup"] == "complete", "paired release incomplete");
                }

            var changing = new ElementPointerProbe
            {
                BreakAfterClick = true
            };
            var actions = new ElementObjectProbe();
            var driver = new NativeExecution(actions, pointer: new NativePointer(changing, "exclusive", false));
            var interrupted = Run(driver, new NativeWriteOperation(Target(), Wire.Obj("kind", "click", "parameters", Wire.Obj("count", 2))), "pointer_sequence");
            Assert(changing.Sent == 1 && actions.Actions == 0, "retired target received a second click or fallback");
            Assert((string)interrupted["errorCode"] == "stale_target" && (string)interrupted["dispatch"] == "unknown", "first click effect was hidden");
            Assert((string)interrupted["cleanup"] == "complete", "first click left a held button");
            var background = new ElementPointerProbe
            {
                Ready = false
            };
            var shared = new NativeExecution(new ElementObjectProbe(), pointer: new NativePointer(background, "shared", false));
            var refused = Run(shared, new NativeWriteOperation(Target(), Wire.Obj("kind", "click", "parameters", Wire.Obj("count", 2))), "pointer_sequence", true);
            Assert(background.Sent == 0 && background.Activated == 0 && (string)refused["errorCode"] == "mode_unsupported", "strict shared granted foreground preparation");
        }

        static void Replacement()
        {
            foreach (bool ignore in new[]
            {
                false,
                true
            }

            )
            {
                var objects = new ElementObjectProbe
                {
                    IgnoreReplacement = ignore
                };
                var execution = new NativeExecution(objects);
                var result = Run(execution, new NativeWriteOperation(Target(), Wire.Obj("kind", "replace", "parameters", Wire.Obj("text", "new"))), "native_text_edit");
                Assert(objects.Replacements == 1 && (string)result["dispatch"] == "dispatched", "replacement replayed or hid dispatch");
                Assert((string)result["confirmation"] == (ignore ? "unavailable" : "confirmed"), "unchanged value was claimed as confirmed");
                if (ignore)
                    Assert(((string)result["detail"]).Contains("remains unchanged"), "unchanged value missing from receipt");
            }
        }

        static void EmptyClipboard()
        {
            var api = new EmptyClipboardProbe();
            var saved = new NativeClipboardTransaction(api, Request());
            saved.Install("demo", Request());
            saved.Validate(Request());
            Assert(api.Data.Count == 1 && api.Revision != 0, "empty initial clipboard could not publish a tracked payload");
            Assert(saved.Finish(true) == NativeClipboardRestore.Restored && api.Data.Count == 0, "empty initial clipboard was not restored");
            api = new EmptyClipboardProbe
            {
                Data = new[]
                {
                    new NativeClipboardData(13, new byte[] { 1, 0 })
                }
            };
            try
            {
                new NativeClipboardTransaction(api, Request());
                throw new InvalidOperationException("untracked nonempty clipboard accepted");
            }
            catch (Failure e)
            {
                Assert(e.Code == "input_unavailable" && api.Changes == 0, "untracked clipboard was changed");
            }

            api = new EmptyClipboardProbe
            {
                KeepZero = true
            };
            saved = new NativeClipboardTransaction(api, Request());
            try
            {
                saved.Install("demo", Request());
                throw new InvalidOperationException("untracked publication accepted");
            }
            catch (Failure e)
            {
                Assert(e.Code == "input_unavailable" && api.Data.Count == 0, "untracked publication was not rolled back under its lock");
            }

            Assert(saved.Finish(false) == NativeClipboardRestore.Restored, "untracked rollback cleanup failed");
        }

        static void ReadPreparation()
        {
            foreach (bool exclusive in new[]
            {
                false,
                true
            }

            )
            {
                var reader = new ReadPreparationProbe
                {
                    Exclusive = exclusive
                };
                var runtime = new NativeRuntime("test", reader);
                var directory = Wire.Obj("session", "test", "instance", null, "rootWindow", null, "surface", null, "node", null, "capture", null);
                Func<Dictionary<string, object>, string> begin = target => (string)Wire.Object(runtime.Perform("begin", Wire.Obj("mode", "read", "target", target, "intent", Wire.Obj("kind", "read", "parameters", Wire.Obj())), Request()))["operation"];
                string token = begin(directory);
                runtime.Perform("finish", Wire.Obj("operation", token), Request());
                Assert(reader.Preparations == 0, "directory read activated a window");
                var window = new Dictionary<string, object>(Target(null));
                window.Remove("application");
                token = begin(window);
                Assert(reader.Preparations == 0, "read admission changed the desktop before returning its cleanup token");
                var node = new Dictionary<string, object>(window);
                node["node"] = "field";
                runtime.Perform("inspect", Wire.Obj("operation", token, "target", node, "query", Wire.Obj("kind", "nodes", "fields", new[] { "Name" }, "maxNodes", 1, "maxDepth", 0)), Request());
                Assert(reader.Activations == (exclusive ? 1 : 0), "selected UIA observation did not obey desktop policy");
                runtime.Perform("finish", Wire.Obj("operation", token), Request());
                reader.RetireOnPrepare = exclusive;
                if (exclusive)
                {
                    token = begin(window);
                    try
                    {
                        runtime.Perform("inspect", Wire.Obj("operation", token, "target", node, "query", Wire.Obj("kind", "nodes", "fields", new[] { "Name" }, "maxNodes", 1, "maxDepth", 0)), Request());
                        throw new InvalidOperationException("preparation replaced the fixed window");
                    }
                    catch (Failure e)
                    {
                        Assert(e.Code == "stale_target", "failed preparation did not validate the original read scope");
                    }

                    runtime.Perform("finish", Wire.Obj("operation", token), Request());
                    Assert(reader.Releases == 3, "failed preparation leaked the original read scope");
                }

                runtime.Close();
            }
        }

        static int Main()
        {
            Clicks();
            Replacement();
            EmptyClipboard();
            ReadPreparation();
            Console.WriteLine(Wire.Json(Wire.Obj("passed", true, "assertions", assertions, "scope", "Production element gesture/cleanup and replacement read-back; fake OS, no GUI")));
            return 0;
        }
    }

    internal sealed class ReadPreparationProbe : INativeReadBackend, INativeReadPreparation
    {
        internal bool Exclusive, RetireOnPrepare, Retired;
        internal int Preparations, Activations, Releases;
        public void Validate(Dictionary<string, object> target, Request r)
        {
            r.Check();
            if (Retired)
                throw new Failure("stale_target", "original window retired");
        }

        public bool NeedsPreparation(Dictionary<string, object> target)
        {
            return Exclusive && target["surface"] != null;
        }

        public void PrepareRead(Dictionary<string, object> target, Request r, bool activate)
        {
            Preparations++;
            if (activate)
                Activations++;
            if (RetireOnPrepare)
                Retired = true;
        }

        public object Inspect(Dictionary<string, object> target, Dictionary<string, object> query, Request r)
        {
            return Wire.Obj();
        }

        public void Release()
        {
            Releases++;
        }
    }

    internal sealed class EmptyClipboardProbe : INativeClipboardApi
    {
        internal uint Revision;
        internal int Changes;
        internal bool KeepZero;
        internal IList<NativeClipboardData> Data = new NativeClipboardData[0];
        sealed class Payload : INativeClipboardPayload
        {
            internal IList<NativeClipboardData> Data;
            public void Dispose()
            {
            }
        }

        sealed class Lock : IDisposable
        {
            public void Dispose()
            {
            }
        }

        public IDisposable Open(Request r)
        {
            return new Lock();
        }

        public uint Version
        {
            get
            {
                return Revision;
            }
        }

        public IList<NativeClipboardData> Read(Request r)
        {
            return Data;
        }

        public INativeClipboardPayload Prepare(IList<NativeClipboardData> data, Request r)
        {
            return new Payload
            {
                Data = data
            };
        }

        public INativeClipboardPayload PrepareText(string text, Request r)
        {
            return Prepare(new[] { new NativeClipboardData(13, System.Text.Encoding.Unicode.GetBytes(text)) }, r);
        }

        public void Replace(INativeClipboardPayload payload, Action changed, Request r)
        {
            Data = ((Payload)payload).Data;
            Changes++;
            if (!KeepZero)
                Revision++;
            changed();
        }

        public void Handoff()
        {
        }

        public void Dispose()
        {
        }
    }
}

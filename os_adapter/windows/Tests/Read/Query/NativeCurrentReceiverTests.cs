using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Text.Json;

namespace ComputerUse.Windows
{
    // Executes the production ancestor-query algorithm and native protocol
    // pipeline. This is mechanism evidence, not a claim of live Windows UIA.
    internal sealed class CurrentReceiverProbe : INativeCurrentReceiverProvider, INativeCurrentReceiverTree
    {
        internal readonly Dictionary<string, string> Parents = new Dictionary<string, string>();
        internal readonly List<string> ParentReads = new List<string>(), Retained = new List<string>();
        internal string Focus = "focused", NextFocus;
        internal int Validations, FocusReads;
        internal Action<Request> BeforeFocus, BeforeParent;
        internal Action Validate;
        internal CurrentReceiverProbe()
        {
            Parents["focused"] = "wrapper";
            Parents["wrapper"] = "parent";
        }

        public object Focused(Request r)
        {
            FocusReads++;
            if (BeforeFocus != null)
                BeforeFocus(r);
            r.Check();
            return FocusReads > 1 && NextFocus != null ? NextFocus : Focus;
        }

        public object Parent(object element, Request r)
        {
            if (BeforeParent != null)
                BeforeParent(r);
            r.Check();
            ParentReads.Add((string)element);
            string parent;
            return Parents.TryGetValue((string)element, out parent) ? parent : null;
        }

        public string Identity(object element, Request r)
        {
            r.Check();
            return (string)element;
        }

        public string Retain(object element, Request r)
        {
            r.Check();
            Retained.Add((string)element);
            return "token-" + element;
        }

        public NativeCurrentReceiverEvidence Read(Dictionary<string, object> scope, Request r)
        {
            return CurrentReceiverPath.Read("parent", (string)scope["node"], this, r, () =>
            {
                Validations++;
                if (Validate != null)
                    Validate();
                r.Check();
            });
        }
    }

    internal static class NativeCurrentReceiverTests
    {
        static int assertions;
        static Request Req()
        {
            return new Request(CancellationToken.None, 5000);
        }

        static void Assert(bool value, string detail)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(detail);
        }

        static void Refused(string code, Action call)
        {
            try
            {
                call();
            }
            catch (Failure error)
            {
                Assert(error.Code == code, "Expected " + code + ", got " + error.Code);
                return;
            }

            throw new InvalidOperationException("Expected " + code);
        }

        static Dictionary<string, object> Target(string node = "parent")
        {
            return Wire.Obj("session", "session", "instance", "instance", "rootWindow", "window", "surface", "surface", "node", node, "capture", null);
        }

        static NativeCurrentReceiverEvidence Read(CurrentReceiverProbe probe)
        {
            return probe.Read(Target(), Req());
        }

        static object Query(NativeRuntime runtime, string operation, Dictionary<string, object> query = null, string node = "parent")
        {
            return runtime.Perform("inspect", Wire.Obj("operation", operation, "target", Target(node), "query", query ?? Wire.Obj("kind", "inputContext", "within", new string[0], "currentReceiver", true)), Req());
        }

        static string Begin(NativeRuntime runtime)
        {
            return (string)Wire.Object(runtime.Perform("begin", Wire.Obj("mode", "write", "target", Target(null), "intent", Wire.Obj("kind", "key", "parameters", Wire.Obj("key", "Return"))), Req()))["operation"];
        }

        static void Finish(NativeRuntime runtime, string op)
        {
            runtime.Perform("finish", Wire.Obj("operation", op), Req());
        }

        static int Main()
        {
            var probe = new CurrentReceiverProbe();
            for (int n = 0; n < 500; n++)
                probe.Parents["unrelated-" + n] = "parent";
            var evidence = Read(probe);
            Assert(evidence.State == "confirmed" && evidence.Node == "token-focused", "focused receiver lost in wide tree");
            Assert(evidence.Ancestors.SequenceEqual(new[] { "token-wrapper", "parent" }), "parent path does not end at original scope");
            Assert(probe.ParentReads.SequenceEqual(new[] { "focused", "wrapper" }), "read an unrelated branch");
            Assert(probe.Validations == 2 && probe.FocusReads == 2, "scope/focus checks missing");
            probe = new CurrentReceiverProbe
            {
                Focus = "parent"
            };
            evidence = Read(probe);
            Assert(evidence.Node == "parent" && evidence.Ancestors.Length == 0 && probe.Retained.Count == 0, "self focus rebound identity");
            probe = new CurrentReceiverProbe
            {
                Focus = null
            };
            evidence = Read(probe);
            Assert(evidence.State == "absent" && evidence.Node == null && evidence.Ancestors.Length == 0, "missing focus is not absence");
            probe = new CurrentReceiverProbe
            {
                Focus = "other-window"
            };
            evidence = Read(probe);
            Assert(evidence.State == "conflict" && evidence.Node == null && probe.Retained.Count == 0, "foreign focus registered or accepted");
            probe = new CurrentReceiverProbe();
            probe.Parents["wrapper"] = "focused";
            evidence = Read(probe);
            Assert(evidence.State == "conflict" && evidence.Ancestors.Length == 0, "cycle accepted");
            probe = new CurrentReceiverProbe
            {
                BeforeFocus = r =>
                {
                    throw new Failure("stale_target", "optional focus was destroyed");
                }
            };
            evidence = Read(probe);
            Assert(evidence.State == "unavailable" && probe.Validations == 2 && probe.FocusReads == 1, "optional focus failure propagated or retried");
            probe.BeforeFocus = null;
            evidence = Read(probe);
            Assert(evidence.State == "confirmed", "optional focus failure poisoned scope");
            probe = new CurrentReceiverProbe
            {
                BeforeParent = r =>
                {
                    throw new Failure("observation_incomplete", "parent unavailable");
                }
            };
            evidence = Read(probe);
            Assert(evidence.State == "unavailable" && evidence.Node == null, "unknown ancestry granted input authority");
            probe = new CurrentReceiverProbe
            {
                NextFocus = "other-focus"
            };
            evidence = Read(probe);
            Assert(evidence.State == "conflict" && evidence.Node == null && probe.FocusReads == 2, "focus change restarted lookup or accepted old focus");
            probe = new CurrentReceiverProbe
            {
                Validate = () =>
                {
                    throw new Failure("stale_target", "original scope destroyed");
                }
            };
            Refused("stale_target", () => Read(probe));
            Assert(probe.FocusReads == 0, "scope failure entered optional lookup");
            probe = new CurrentReceiverProbe();
            probe.Validate = () =>
            {
                if (probe.Validations == 2)
                    throw new Failure("stale_target", "original scope changed during query");
            };
            Refused("stale_target", () => Read(probe));
            probe = new CurrentReceiverProbe
            {
                BeforeFocus = r =>
                {
                    throw new Failure("cancelled", "cancelled focus query");
                }
            };
            Refused("cancelled", () => Read(probe));
            probe = new CurrentReceiverProbe
            {
                BeforeFocus = r =>
                {
                    throw new Failure("deadline_exceeded", "focus query deadline");
                }
            };
            Refused("deadline_exceeded", () => Read(probe));
            probe = new CurrentReceiverProbe
            {
                Focus = "0"
            };
            for (int n = 0; n < 129; n++)
                probe.Parents[n.ToString()] = (n + 1).ToString();
            probe.Parents["129"] = "parent";
            evidence = Read(probe);
            Assert(evidence.State == "unavailable" && probe.ParentReads.Count == 128 && probe.Retained.Count == 0, "unbounded parent query");
            var objects = new ObjectProbe();
            var reader = new ReadProbeBackend();
            var keys = new KeyboardProbe
            {
                Foreground = true,
                Route = true,
                Focused = true
            };
            probe = new CurrentReceiverProbe();
            var runtime = new NativeRuntime("session", reader, new NativeExecution(objects, new NativeKeyboard(keys, "shared", false), currentReceiver: probe));
            Assert(runtime.Capabilities.Contains("nativeCurrentReceiver"), "missing capability");
            string op = Begin(runtime);
            var facts = Wire.Object(Query(runtime, op));
            Assert(Wire.Array(facts["primitives"]).Length == 0, "discovery declared executable input");
            Assert((string)Wire.Object(Wire.Object(facts["input"])["currentReceiver"])["state"] == "confirmed", "query branch dropped evidence");
            Assert(keys.Reads == 0 && keys.FocusCalls == 0 && keys.Activated == 0 && keys.Batches.Count == 0 && objects.Reads == 0 && reader.Reads == 0, "discovery entered regular route inspection, preparation or tree reading");
            Refused("invalid_request", () => Query(runtime, op, Wire.Obj("kind", "inputContext", "within", new string[0], "currentReceiver", false)));
            Refused("invalid_request", () => Query(runtime, op, Wire.Obj("kind", "inputContext", "within", new[] { "parent" }, "currentReceiver", true)));
            Refused("invalid_request", () => Query(runtime, op, Wire.Obj("kind", "inputContext", "within", new string[0], "currentReceiver", true, "basis", new object[0])));
            Refused("invalid_request", () => Query(runtime, op, node: null));
            var normal = Wire.Object(Query(runtime, op, Wire.Obj("kind", "inputContext", "within", new[] { "parent" }), "token-focused"));
            Assert(Wire.Array(normal["primitives"]).Length == 1 && keys.Reads == 1, "discovery froze scope instead of allowing receiver selection");
            keys.Focused = false;
            var primitive = Wire.Object(Wire.Array(normal["primitives"])[0]);
            var receipt = Wire.Object(runtime.Perform("execute", Wire.Obj("operation", op, "step", 1, "command", Wire.Obj("kind", primitive["unit"], "primitiveId", primitive["id"], "target", normal["binding"], "foreground", false)), Req()));
            Assert((string)receipt["dispatch"] == "notDispatched" && keys.Batches.Count == 0, "old discovery bypassed live dispatch guard");
            Finish(runtime, op);
            probe.BeforeFocus = r =>
            {
                throw new Failure("stale_target", "optional focus vanished");
            };
            op = Begin(runtime);
            var gap = Wire.Object(Query(runtime, op));
            Assert((string)Wire.Object(Wire.Object(gap["input"])["currentReceiver"])["state"] == "unavailable", "optional failure failed entire query");
            probe.BeforeFocus = null;
            Assert(Wire.Object(Query(runtime, op))["kind"].Equals("inputContext"), "optional failure retired operation");
            Finish(runtime, op);
            op = Begin(runtime);
            reader.RejectValidation = true;
            Refused("stale_target", () => Query(runtime, op));
            Finish(runtime, op);
            runtime.Close();
            runtime = new NativeRuntime("session", new ReadProbeBackend(), new NativeExecution(new ObjectProbe(), new NativeKeyboard(new KeyboardProbe(), "shared", false)));
            Assert(!runtime.Capabilities.Contains("nativeCurrentReceiver"), "unsupported provider advertised capability");
            op = Begin(runtime);
            Refused("mode_unsupported", () => Query(runtime, op));
            Finish(runtime, op);
            runtime.Close();
            Console.WriteLine(JsonSerializer.Serialize(new { assertions = assertions, facts = facts, unavailable = gap, guarded = receipt }));
            return 0;
        }
    }
}

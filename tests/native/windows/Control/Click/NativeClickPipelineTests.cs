using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Text.Json;
using System.IO;
using System.Runtime.InteropServices;

namespace ComputerUse.Windows
{
    internal sealed class ClickMetadataProbe : INativeObjectProvider, INativeClickObjectProvider
    {
        internal readonly ObjectProbe Objects = new ObjectProbe();
        internal Action Metadata, Boundary;
        internal int LegacyReads, ClickReads, Boundaries;
        public IDisposable Acquire(Request r)
        {
            return Objects.Acquire(r);
        }

        NativeObjectState State()
        {
            return new NativeObjectState
            {
                Role = "ControlType.Button",
                Label = "Button",
                Enabled = true,
                ActionsComplete = true,
                Actions = new[]
                {
                    "Invoke.Invoke"
                }
            };
        }

        public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request r)
        {
            LegacyReads++;
            r.Check();
            if (Metadata != null)
                Metadata();
            return State();
        }

        public NativeObjectState ReadClick(Dictionary<string, object> target, string[] within, Request r)
        {
            ClickReads++;
            var state = State();
            NativeClickObjectObservation.Read(state, () =>
            {
                if (Metadata != null)
                    Metadata();
            }, () =>
            {
                Boundaries++;
                if (Boundary != null)
                    Boundary();
            }, r);
            return state;
        }

        public Action Prepare(NativeObjectState state, string unit, string action, string text, Request r)
        {
            return Objects.Prepare(state, unit, action, text, r);
        }
    }

    internal sealed class ClickElementProbe : INativePointerProvider, INativeElementPointerProvider
    {
        internal bool Ready, Alive = true, NoGeometry, ExactPoint;
        internal int X = 30, Activated, Sampled;
        internal Action OnActivate;
        internal Action OnSend;
        internal NativeElementHit Hit = new NativeElementHit(NativeElementHitKind.Target);
        internal readonly List<NativePointerEvent[]> Batches = new List<NativePointerEvent[]>();
        public NativePointerCapture Capture(Dictionary<string, object> t, Request r)
        {
            throw new InvalidOperationException("element used image mapping");
        }

        public NativePointerState Read(Dictionary<string, object> t, NativePointerPoint p, string k, bool d, Request r)
        {
            throw new InvalidOperationException("element lost its authority");
        }

        public NativeElementPointerTarget Element(Dictionary<string, object> t, string[] w, Request r)
        {
            r.Check();
            Sampled++;
            if (!Alive)
                throw new Failure("stale_target", "element retired");
            if (NoGeometry)
                throw new Failure("input_unavailable", "element has no geometry");
            return new NativeElementPointerTarget
            {
                Point = new NativePointerPoint(X, 40),
                RequiresConfirmedHit = ExactPoint,
                Canvas = new NativeCaptureGeometry(0, 0, 1000, 800, 1)
            };
        }

        public NativePointerState ReadElement(Dictionary<string, object> t, string[] w, NativeElementPointerTarget p, Request r)
        {
            r.Check();
            if (!Alive)
                throw new Failure("stale_target", "element retired");
            if (p.Point.X != X)
                throw new Failure("input_unavailable", "stale element point");
            p.Hit = Hit;
            return new NativePointerState
            {
                Ready = Hit.Ready(Ready, p.RequiresConfirmedHit),
                ForegroundReady = Ready,
                Foreground = Ready,
                Native = this
            };
        }

        public void RequireIdle(Request r)
        {
            r.Check();
        }

        public void Activate(NativePointerState s, Request r)
        {
            r.Check();
            Activated++;
            Ready = true;
            X = 75;
            if (OnActivate != null)
                OnActivate();
        }

        public int Send(NativePointerEvent[] e)
        {
            Batches.Add(e);
            if (OnSend != null)
                OnSend();
            return e.Length;
        }
    }

    internal static class NativeClickPipelineTests
    {
        sealed class FailingDiagnosticWriter : StringWriter
        {
            public override void WriteLine(string value)
            {
                throw new InvalidOperationException("diagnostic sink unavailable");
            }
        }

        static int assertions;
        static void Assert(bool value, string message)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(message);
        }

        static Request Request()
        {
            return new Request(CancellationToken.None, 5000);
        }

        static Dictionary<string, object> Target(bool capture = true)
        {
            return Wire.Obj("session", "session", "instance", "instance", "rootWindow", "window", "surface", "surface", "node", capture ? null : "node", "capture", capture ? "capture" : null);
        }

        static NativeRuntime Runtime(PointerProbe p, ObjectProbe o, bool assist = false)
        {
            var canvas = new NativeCaptureGeometry(-1000, -100, 2000, 1200, 1);
            var window = Target();
            window["capture"] = null;
            p.Captures.Add("capture", window, new NativeCaptureGeometry(-200, 50, 400, 300, 400), canvas, "layout", new[] { canvas });
            return new NativeRuntime("session", new ObjectProbeRead(o), new NativeExecution(o, pointer: new NativePointer(p, "shared", assist)));
        }

        static Dictionary<string, object> Begin(NativeRuntime rt, bool v1 = true, int count = 1, bool capture = true)
        {
            var p = Wire.Obj("mode", "write", "target", Target(capture), "intent", Wire.Obj("kind", "click", "parameters", capture ? Wire.Obj("x", 10, "y", 20, "count", count) : Wire.Obj("count", count)));
            if (v1)
                p["clickContract"] = "v1";
            return Wire.Object(rt.Perform("begin", p, Request()));
        }

        static Dictionary<string, object> Inspect(NativeRuntime rt, string op, bool capture = true)
        {
            return Wire.Object(rt.Perform("inspect", Wire.Obj("operation", op, "target", Target(capture), "query", Wire.Obj("kind", "inputContext", "within", new string[0])), Request()));
        }

        static Dictionary<string, object> Command(Dictionary<string, object> facts, bool foreground = false, string unit = "pointer_sequence")
        {
            var p = Wire.Array(facts["primitives"]).Select(Wire.Object).First(x => (string)x["unit"] == unit);
            return Wire.Obj("kind", unit, "primitiveId", p["id"], "target", facts["binding"], "foreground", foreground);
        }

        static Dictionary<string, object> Execute(NativeRuntime rt, string op, Dictionary<string, object> command, int step = 1, Request r = null)
        {
            return Wire.Object(rt.Perform("execute", Wire.Obj("operation", op, "step", step, "command", command), r ?? Request()));
        }

        static Dictionary<string, object> Transport(Dictionary<string, object> result)
        {
            return Wire.Object(Wire.Object(result["click"])["transportEvidence"]);
        }

        static void Refused(Action action)
        {
            try
            {
                action();
            }
            catch (Failure e)
            {
                Assert(e.Code == "execution_unknown", "terminal state returned wrong failure");
                return;
            }

            throw new InvalidOperationException("terminal operation permitted new input");
        }

        static void Finish(NativeRuntime rt, string op)
        {
            rt.Perform("finish", Wire.Obj("operation", op), Request());
        }

        static void Negotiation()
        {
            var p = new PointerProbe();
            var o = new ObjectProbe();
            var rt = Runtime(p, o);
            Assert(rt.Capabilities.Contains("nativeClickPipelineV1"), "capability was not advertised");
            var begin = Begin(rt, false);
            string op = (string)begin["operation"];
            var facts = Inspect(rt, op);
            var result = Execute(rt, op, Command(facts));
            Assert(!begin.ContainsKey("clickContract") && !Wire.Object(Wire.Array(facts["primitives"])[0]).ContainsKey("click") && !result.ContainsKey("click"), "old client received new fields");
            Finish(rt, op);
            foreach (string version in new[]
            {
                "v2",
                "",
                "V1"
            }

            )
            {
                try
                {
                    rt.Perform("begin", Wire.Obj("mode", "write", "target", Target(), "intent", Wire.Obj("kind", "click", "parameters", Wire.Obj("x", 1, "y", 1)), "clickContract", version), Request());
                    throw new InvalidOperationException("unsupported version admitted");
                }
                catch (Failure e)
                {
                    Assert(e.Code == "invalid_request", "wrong version error");
                }
            }

            begin = Begin(rt);
            op = (string)begin["operation"];
            facts = Inspect(rt, op);
            Assert((string)begin["clickContract"] == "v1", "negotiation not echoed");
            var offer = Wire.Object(Wire.Object(Wire.Array(facts["primitives"])[0])["click"]);
            Assert((string)offer["delivery"] == "desktop_pointer" && (string)offer["preferenceProfile"] == "physicalWhenAvailableInExclusive", "SendInput delivery/profile changed");
            var command = Command(facts);
            command["kind"] = "prepare_receiver";
            result = Execute(rt, op, command);
            Assert((string)result["errorCode"] == "invalid_request" && (string)result["dispatch"] == "notDispatched", "v1 retained separate preparation");
            Finish(rt, op);
        }

        static Dictionary<string, object> Prefixes()
        {
            Dictionary<string, object> result = null;
            foreach (int prefix in new[]
            {
                0,
                1,
                2,
                3,
                -1
            }

            )
            {
                var p = new PointerProbe();
                var rt = Runtime(p, new ObjectProbe());
                string op = (string)Begin(rt)["operation"];
                var facts = Inspect(rt, op);
                var command = Command(facts);
                var request = Request();
                p.OnSend = e =>
                {
                    if (e.Length != 3)
                        return e.Length;
                    if (prefix < 0)
                        throw new InvalidOperationException("unknown insertion result");
                    return prefix;
                };
                result = Execute(rt, op, command, r: request);
                var evidence = Transport(result);
                Assert((string)result["dispatch"] == (prefix == 0 ? "notDispatched" : prefix == 3 ? "dispatched" : prefix < 0 ? "unknown" : "partial"), "wrong prefix dispatch " + prefix);
                Assert((bool)evidence["callEntered"] && (bool)evidence["callReturned"] == (prefix >= 0) && Convert.ToInt32(evidence["expected"]) == 3, "call evidence differs from actual SendInput");
                Assert(prefix < 0 ? evidence["submitted"] == null : Convert.ToInt32(evidence["submitted"]) == prefix, "accepted prefix lost");
                Assert(p.Batches.Count == (prefix == 2 || prefix < 0 ? 2 : 1), "paired cleanup changed unpressed/held button handling");
                Assert(ReferenceEquals(result, Execute(rt, op, command)) && p.Batches.Count == (prefix == 2 || prefix < 0 ? 2 : 1), "same step replayed");
                if (prefix == 0)
                {
                    Assert(!request.Effect && !request.Dispatched && result["retryReason"] == null, "zero inferred effect, UIPI or retry permission");
                    var fresh = Inspect(rt, op);
                    Assert((string)Command(fresh)["primitiveId"] != (string)command["primitiveId"], "proven zero did not release permanent Bind lock");
                }
                else
                {
                    Refused(() => Execute(rt, op, command, 2));
                    Refused(() => Inspect(rt, op));
                }

                Finish(rt, op);
            }

            foreach (bool retire in new[]
            {
                false,
                true
            }

            )
            {
                var p = new PointerProbe();
                var rt = Runtime(p, new ObjectProbe());
                string op = (string)Begin(rt, count: 2)["operation"];
                var command = Command(Inspect(rt, op));
                int calls = 0;
                p.OnSend = e =>
                {
                    calls++;
                    if (calls == 1)
                    {
                        if (retire)
                            p.Alive = false;
                        return 3;
                    }

                    return 0;
                };
                result = Execute(rt, op, command);
                Assert((string)result["dispatch"] == "partial" && Convert.ToInt32(Transport(result)["submitted"]) == 3, "later zero/retirement erased first click");
                Assert(calls == (retire ? 1 : 2), "double click was replayed or retargeted");
                Refused(() => Execute(rt, op, command, 2));
                Finish(rt, op);
            }

            var dying = new PointerProbe();
            var deadRuntime = Runtime(dying, new ObjectProbe());
            string deadOp = (string)Begin(deadRuntime)["operation"];
            var deadCommand = Command(Inspect(deadRuntime, deadOp));
            dying.OnSend = e =>
            {
                dying.Alive = false;
                return e.Length == 3 ? 2 : 1;
            };
            result = Execute(deadRuntime, deadOp, deadCommand);
            Assert(dying.Batches.Count == 2 && dying.Batches[1][0].Flags == 4 && (string)result["cleanup"] == "complete", "destroyed target stranded global button release");
            Finish(deadRuntime, deadOp);
            return result;
        }

        static Dictionary<string, object> PreparationAndElement()
        {
            foreach (bool assist in new[]
            {
                false,
                true
            }

            )
            {
                var p = new PointerProbe
                {
                    Ready = false
                };
                var rt = Runtime(p, new ObjectProbe(), assist);
                string op = (string)Begin(rt)["operation"];
                var result = Execute(rt, op, Command(Inspect(rt, op), true));
                Assert(p.Activated == (assist ? 1 : 0) && p.Batches.Count == (assist ? 1 : 0), "foreground bool bypassed native host policy or did not merge execute");
                if (assist)
                {
                    var evidence = Wire.Object(Wire.Array(Wire.Object(result["click"])["preparationEvidence"])[0]);
                    Assert((string)evidence["kind"] == "activation" && (string)evidence["effect"] == "contextOnly" && (string)evidence["wait"] == "satisfied", "normal activation evidence lost");
                }

                Finish(rt, op);
            }

            var element = new ClickElementProbe();
            var objects = new ObjectProbe();
            var runtime = new NativeRuntime("session", new ObjectProbeRead(objects), new NativeExecution(objects, pointer: new NativePointer(element, "exclusive", false)));
            string token = (string)Begin(runtime, capture: false)["operation"];
            var facts = Inspect(runtime, token, false);
            element.X = 55;
            var receipt = Execute(runtime, token, Command(facts, true));
            Assert((string)receipt["dispatch"] == "dispatched" && element.Activated == 1 && element.Sampled >= 4, "same element was not resampled around preparation");
            var normalized = NativePointerCapture.Normalize(new NativePointerPoint(75, 40), new NativeCaptureGeometry(0, 0, 1000, 800, 1));
            Assert(element.Batches[0][0].X == normalized.X && objects.Sent == 0, "post-prepare point or physical callback semantics lost");
            Finish(runtime, token);
            element.NoGeometry = true;
            token = (string)Begin(runtime, capture: false)["operation"];
            facts = Inspect(runtime, token, false);
            Assert(Wire.Array(facts["primitives"]).All(x => (string)Wire.Object(x)["unit"] == "native_action"), "missing geometry erased independent UIA actions");
            receipt = Execute(runtime, token, Command(facts, unit: "native_action"));
            Assert((string)receipt["dispatch"] == "dispatched" && (string)Transport(receipt)["kind"] == "nativeAction" && objects.Sent == 1, "UIA candidate not independently executable");
            Finish(runtime, token);
            return facts;
        }

        static void ObjectUnknownAndPreparationStop()
        {
            foreach (string action in new[]
            {
                "Invoke.Invoke",
                "Toggle.Toggle",
                "SelectionItem.Select"
            }

            )
            {
                var provider = new ObjectProbe();
                provider.Actions[0] = action;
                provider.Actions[1] = "Other.One";
                provider.Actions[2] = "Other.Two";
                var executor = new NativeRuntime("session", new ObjectProbeRead(provider), new NativeExecution(provider));
                string token = (string)Begin(executor, capture: false)["operation"];
                var unchanged = Execute(executor, token, Command(Inspect(executor, token, false), unit: "native_action"));
                Assert(provider.Sent == 1 && (string)unchanged["dispatch"] == "dispatched" && (string)unchanged["confirmation"] == "unavailable", "unchanged state replaced the requested " + action + " with claimed completion");
                Finish(executor, token);
            }

            var objects = new ObjectProbe();
            var rt = new NativeRuntime("session", new ObjectProbeRead(objects), new NativeExecution(objects));
            string op = (string)Begin(rt, capture: false)["operation"];
            var command = Command(Inspect(rt, op, false), unit: "native_action");
            objects.OnSend = () =>
            {
                throw new InvalidOperationException("provider acted then reply lost");
            };
            var result = Execute(rt, op, command);
            Assert(objects.Sent == 1 && (string)result["dispatch"] == "unknown" && !(bool)Transport(result)["callReturned"], "UIA uncertainty became a replay opportunity");
            Refused(() => Execute(rt, op, command, 2));
            Finish(rt, op);
            var p = new ClickElementProbe();
            var driver = new NativeExecution(new ObjectProbe(), pointer: new NativePointer(p, "exclusive", false));
            var operation = new NativeWriteOperation(Target(false), Wire.Obj("kind", "click", "parameters", Wire.Obj()), true);
            var facts = Wire.Object(driver.Inspect(operation, Target(false), new string[0], Request()));
            var r = Request();
            p.OnActivate = () =>
            {
                var effect = r.ClickPreparation.Preparing("escape");
                effect.Dispatch = "dispatched";
                effect.Observation = "confirmed";
                r.ClickPreparation.RequireObservation();
                p.Alive = false;
            };
            result = Wire.Object(driver.Execute(operation, Command(facts, true), r));
            Assert((string)result["dispatch"] == "notDispatched" && p.Batches.Count == 0 && result["retryReason"] == null, "content-affecting preparation permitted another click");
            Assert((string)Wire.Object(Wire.Object(result["click"])["continuation"])["kind"] == "observe", "preparation loss did not require observation");
            Refused(() => operation.Bind(Target(false), new string[0]));
        }

        static Dictionary<string, object> MetadataIsolation()
        {
            var provider = new ClickMetadataProbe
            {
                Metadata = () =>
                {
                    throw new COMException("pattern cache unavailable");
                }
            };
            var pointer = new ClickElementProbe
            {
                Ready = true
            };
            var driver = new NativeExecution(provider, pointer: new NativePointer(pointer, "exclusive", false));
            Func<bool, NativeWriteOperation> operation = v1 => new NativeWriteOperation(Target(false), Wire.Obj("kind", "click", "parameters", Wire.Obj()), v1);
            var selected = operation(true);
            var facts = Wire.Object(driver.Inspect(selected, Target(false), new string[0], Request()));
            Assert(provider.ClickReads == 1 && provider.LegacyReads == 0 && provider.Boundaries == 1, "v1 did not isolate metadata before final identity/scope checks");
            Assert(!(bool)facts["actionsComplete"] && facts["nativeRole"] == null && (string)facts["enabled"] == "unknown" && facts.ContainsKey("unavailableReason"), "failed semantic metadata became fabricated facts");
            Assert(Wire.Array(facts["primitives"]).Length == 1 && (string)Wire.Object(Wire.Array(facts["primitives"])[0])["unit"] == "pointer_sequence", "semantic metadata failure blocked the independent pointer candidate");
            var receipt = Wire.Object(driver.Execute(selected, Command(facts), Request()));
            Assert((string)receipt["dispatch"] == "dispatched" && pointer.Batches.Count == 1 && provider.Objects.Sent == 0, "independently validated pointer was not executable");
            int sampled = pointer.Sampled;
            try
            {
                driver.Inspect(operation(false), Target(false), new string[0], Request());
                throw new InvalidOperationException("legacy metadata failure changed");
            }
            catch (COMException)
            {
                Assert(provider.LegacyReads == 1 && pointer.Sampled == sampled, "legacy observation order changed");
            }

            foreach (string failure in new[]
            {
                "stale_target",
                "blocked_by_modal",
                "desktop_unavailable",
                "deadline_exceeded",
                "cancelled"
            }

            )
            {
                provider.Boundary = () =>
                {
                    throw new Failure(failure, "required target boundary failed");
                };
                try
                {
                    driver.Inspect(operation(true), Target(false), new string[0], Request());
                    throw new InvalidOperationException("required boundary was swallowed");
                }
                catch (Failure error)
                {
                    Assert(error.Code == failure && pointer.Sampled == sampled && pointer.Batches.Count == 1, "metadata degradation swallowed identity/modal/desktop/deadline/cancellation failure");
                }
            }

            provider.Boundary = null;
            provider.Metadata = () =>
            {
                throw new COMException("element retired", unchecked((int)0x80040201));
            };
            try
            {
                driver.Inspect(operation(true), Target(false), new string[0], Request());
                throw new InvalidOperationException("element lifetime failure was swallowed");
            }
            catch (COMException error)
            {
                Assert((uint)error.HResult == 0x80040201 && pointer.Sampled == sampled, "dead UIA object became unknown metadata");
            }

            provider.Metadata = () =>
            {
                throw new Failure("stale_target", "retained object changed");
            };
            try
            {
                driver.Inspect(operation(true), Target(false), new string[0], Request());
                throw new InvalidOperationException("typed identity failure was swallowed");
            }
            catch (Failure error)
            {
                Assert(error.Code == "stale_target" && pointer.Sampled == sampled, "metadata catch widened to required checks");
            }

            using (var cancellation = new CancellationTokenSource())
            {
                provider.Metadata = () =>
                {
                    cancellation.Cancel();
                    throw new COMException("query interrupted");
                };
                try
                {
                    driver.Inspect(operation(true), Target(false), new string[0], new Request(cancellation.Token, 5000));
                    throw new InvalidOperationException("cancelled query continued");
                }
                catch (Failure error)
                {
                    Assert(error.Code == "cancelled" && pointer.Sampled == sampled, "cancellation after metadata failure reached pointer sampling");
                }
            }

            var expired = Request();
            provider.Metadata = () =>
            {
                expired.Deadline = 0;
                throw new COMException("query ran out of time");
            };
            try
            {
                driver.Inspect(operation(true), Target(false), new string[0], expired);
                throw new InvalidOperationException("expired query continued");
            }
            catch (Failure error)
            {
                Assert(error.Code == "deadline_exceeded" && pointer.Sampled == sampled, "deadline after metadata failure reached pointer sampling");
            }

            return facts;
        }

        static void Diagnostics()
        {
            string previousFlag = Environment.GetEnvironmentVariable("COMPUTER_USE_TRACE_NATIVE_INPUT");
            var previousWriter = Console.Error;
            var output = new StringWriter();
            var previousSerializer = System.Web.Script.Serialization.JavaScriptSerializer.DiagnosticSerialize;
            try
            {
                System.Web.Script.Serialization.JavaScriptSerializer.DiagnosticSerialize = value => JsonSerializer.Serialize(value);
                Console.SetError(output);
                int sampledWithoutTrace = 0;
                foreach (bool enabled in new[]
                {
                    false,
                    true
                }

                )
                {
                    Environment.SetEnvironmentVariable("COMPUTER_USE_TRACE_NATIVE_INPUT", enabled ? "1" : null);
                    var pointer = new PointerProbe();
                    var runtime = Runtime(pointer, new ObjectProbe());
                    string op = (string)Begin(runtime)["operation"];
                    var command = Command(Inspect(runtime, op));
                    var result = Execute(runtime, op, command);
                    Assert((string)result["dispatch"] == "dispatched" && pointer.Batches.Count == 1, "timing diagnostics changed the requested click");
                    if (!enabled)
                    {
                        sampledWithoutTrace = pointer.Reads;
                        Assert(output.ToString() == "", "disabled native input diagnostics emitted a record");
                    }
                    else
                    {
                        var record = Wire.Object(Decode(JsonDocument.Parse(output.ToString()).RootElement));
                        Assert((string)record["operation"] == op && Convert.ToInt64(record["step"]) == 1 && Equals(record["primitiveId"], command["primitiveId"]), "native metrics lost operation/step correlation");
                        Assert((string)record["kind"] == "native_click_metrics" && (string)record["routeFamily"] == "windows.sendInput" && (string)record["delivery"] == "desktop_pointer" && (string)record["coordinateSource"] == "capture", "native metrics changed the selected mechanism or coordinate authority");
                        var phases = Wire.Array(record["phases"]).Select(Wire.Object).ToArray();
                        Assert(phases.Select(p => (string)p["name"]).SequenceEqual(new[] { "resolve", "sampleBeforePreparation", "prepare", "sampleAfterPreparation", "finalCheck", "dispatchAndRelease", "buildReceipt" }) && phases.All(p => Convert.ToDouble(p["durationMs"]) >= 0), "native stage timing order or duration is invalid");
                        Assert(pointer.Reads == sampledWithoutTrace, "diagnostics introduced an extra native UI query");
                        string recorded = output.ToString();
                        Assert(ReferenceEquals(result, Execute(runtime, op, command)) && output.ToString() == recorded, "step retransmission reran native timing/input stages");
                    }

                    Finish(runtime, op);
                }

                output.GetStringBuilder().Clear();
                var partial = new PointerProbe
                {
                    OnSend = events => events.Length == 3 ? 2 : 1
                };
                var partialRuntime = Runtime(partial, new ObjectProbe());
                string token = (string)Begin(partialRuntime)["operation"];
                var partialReceipt = Execute(partialRuntime, token, Command(Inspect(partialRuntime, token)));
                var partialRecord = Wire.Object(Decode(JsonDocument.Parse(output.ToString()).RootElement));
                Assert((string)partialReceipt["dispatch"] == "partial" && (string)partialRecord["dispatch"] == "partial" && (string)partialRecord["cleanup"] == "complete" && partial.Batches.Count == 2, "failed-stage timing lost partial dispatch or paired release");
                Assert(Wire.Array(partialRecord["phases"]).Select(Wire.Object).Any(p => (string)p["name"] == "dispatchAndRelease"), "failed dispatch did not close its timing interval");
                Finish(partialRuntime, token);
                output.GetStringBuilder().Clear();
                var objects = new ObjectProbe();
                var objectRuntime = new NativeRuntime("session", new ObjectProbeRead(objects), new NativeExecution(objects));
                token = (string)Begin(objectRuntime, capture: false)["operation"];
                Execute(objectRuntime, token, Command(Inspect(objectRuntime, token, false), unit: "native_action"));
                var objectRecord = Wire.Object(Decode(JsonDocument.Parse(output.ToString()).RootElement));
                Assert(Wire.Array(objectRecord["phases"]).Select(Wire.Object).Select(p => (string)p["name"]).SequenceEqual(new[] { "resolve", "uiaObject", "buildReceipt" }) && (string)objectRecord["delivery"] == "object_action" && objects.Sent == 1, "object timing split or changed its existing native execution branch");
                Finish(objectRuntime, token);
                Console.SetError(new FailingDiagnosticWriter());
                var unaffected = new PointerProbe();
                var unaffectedRuntime = Runtime(unaffected, new ObjectProbe());
                token = (string)Begin(unaffectedRuntime)["operation"];
                var unaffectedResult = Execute(unaffectedRuntime, token, Command(Inspect(unaffectedRuntime, token)));
                Assert((string)unaffectedResult["dispatch"] == "dispatched" && unaffected.Batches.Count == 1, "diagnostic sink failure changed or replayed the input result");
                Finish(unaffectedRuntime, token);
            }
            finally
            {
                Console.SetError(previousWriter);
                System.Web.Script.Serialization.JavaScriptSerializer.DiagnosticSerialize = previousSerializer;
                Environment.SetEnvironmentVariable("COMPUTER_USE_TRACE_NATIVE_INPUT", previousFlag);
            }
        }

        static void SharedBeginFixtures(string path)
        {
            int cases = 0;
            foreach (var item in Wire.Array(Decode(JsonDocument.Parse(File.ReadAllText(path)).RootElement)).Select(Wire.Object))
            {
                var begin = Wire.Object(item["begin"]);
                if (!begin.ContainsKey("clickContract"))
                    continue;
                cases++;
                var objects = new ObjectProbe();
                var rt = new NativeRuntime("session", new ObjectProbeRead(objects), new NativeExecution(objects));
                bool valid = (bool)item["valid"];
                try
                {
                    var response = Wire.Object(rt.Perform("begin", begin, Request()));
                    Assert(valid && (string)response["clickContract"] == "v1", "shared fixture accepted invalid negotiation: " + item["name"]);
                    Finish(rt, (string)response["operation"]);
                }
                catch (Failure e)
                {
                    Assert(!valid && e.Code == "invalid_request" && objects.Acquired == 0, "shared fixture validation acquired resources: " + item["name"]);
                }
            }

            Assert(cases == 4, "shared negotiation fixture coverage changed");
        }

        static object Decode(JsonElement value)
        {
            switch (value.ValueKind)
            {
                case JsonValueKind.Object:
                    return value.EnumerateObject().ToDictionary(p => p.Name, p => Decode(p.Value));
                case JsonValueKind.Array:
                    return value.EnumerateArray().Select(Decode).ToArray();
                case JsonValueKind.String:
                    return value.GetString();
                case JsonValueKind.Number:
                    return value.GetDouble();
                case JsonValueKind.True:
                    return true;
                case JsonValueKind.False:
                    return false;
                default:
                    return null;
            }
        }

        sealed class HitNode
        {
            internal string Id, Role;
            internal HitNode Parent;
            internal HitNode(string id, HitNode parent = null, string role = "ControlType.Pane")
            {
                Id = id;
                Parent = parent;
                Role = role;
            }
        }

        static void ElementHitEvidence()
        {
            var root = new HitNode("window");
            var target = new HitNode("target", root, "ControlType.Button");
            var child = new HitNode("caption", target, "ControlType.Text");
            var other = new HitNode("other", root, "ControlType.Button");
            var otherCaption = new HitNode("other-caption", other, "ControlType.Text");
            var coarsePage = new HitNode("page", root, "ControlType.Document");
            Func<HitNode, NativeElementHit> sample = hit => NativeElementHit.Observe(target, target.Id, () => hit, n => n.Id, n => n.Parent, n => true, n => NativeElementHit.IndependentControl(n.Role), () =>
            {
            });
            Assert(sample(target).Kind == NativeElementHitKind.Target && sample(child).Kind == NativeElementHitKind.Descendant, "target/descendant hit lost");
            Assert(sample(root).Kind == NativeElementHitKind.Ancestor, "coarse ancestor became a conflicting control");
            Assert(sample(other).Conflicts && sample(otherCaption).Conflicts, "independent sibling/control caption did not conflict");
            Assert(sample(coarsePage).Kind == NativeElementHitKind.OtherBranch && sample(coarsePage).Ready(true), "underlying generic page blocked same-HWND floating UI");
            Assert(sample(null).Kind == NativeElementHitKind.NoElement && sample(new HitNode("detached")).Kind == NativeElementHitKind.Incomplete, "missing hit/ancestry lost");
            var cycle = new HitNode("cycle");
            cycle.Parent = cycle;
            Assert(sample(cycle).Kind == NativeElementHitKind.Incomplete && sample(new HitNode("")).Kind == NativeElementHitKind.Incomplete, "cyclic/missing identity granted conflict evidence");
            var deep = new HitNode("deep");
            var tail = deep;
            for (int i = 0; i < 70; i++)
            {
                tail.Parent = new HitNode("deep-" + i);
                tail = tail.Parent;
            }

            Assert(sample(deep).Kind == NativeElementHitKind.Incomplete, "unbounded hit ancestry traversal");
            var outside = NativeElementHit.Observe(target, target.Id, () => other, n => n.Id, n => n.Parent, n => false, n => true, () =>
            {
            });
            Assert(outside.Kind == NativeElementHitKind.OutsideScope && !outside.Confirmed, "unknown ownership became exact hit");
            var primary = new NativePointerPoint(60, 50);
            var alternatives = NativeElementHit.Alternatives(primary, new[] { new NativeCaptureGeometry(10, 10, 100, 80, 1) }).ToArray();
            Assert(alternatives.Length <= 9 && alternatives.All(p => p.X >= 10 && p.X < 110 && p.Y >= 10 && p.Y < 90), "extra points left visible bounds");
            foreach (var kind in new[]
            {
                NativeElementHitKind.Target,
                NativeElementHitKind.Ancestor,
                NativeElementHitKind.QueryFailed,
                NativeElementHitKind.NoElement
            }

            )
            {
                int queries = 0;
                var selected = NativeElementHit.SelectPoint(primary, alternatives, p =>
                {
                    queries++;
                    return new NativeElementHit(kind);
                }, () => true);
                Assert(queries == 1 && selected.Point.X == primary.X && selected.Point.Y == primary.Y, "uncertainty added point-search blocking");
            }

            int coveredQueries = 0;
            var uncovered = NativeElementHit.SelectPoint(primary, alternatives, p =>
            {
                coveredQueries++;
                return new NativeElementHit(coveredQueries == 1 ? NativeElementHitKind.OtherControl : NativeElementHitKind.Target);
            }, () => true);
            Assert(coveredQueries == 2 && uncovered.Hit.Confirmed && uncovered.RequiresConfirmed && (uncovered.Point.X != primary.X || uncovered.Point.Y != primary.Y), "covered center did not choose an exact alternate");
            int missingQueries = 0;
            var retained = NativeElementHit.SelectPoint(primary, alternatives, p =>
            {
                missingQueries++;
                return new NativeElementHit(missingQueries == 1 ? NativeElementHitKind.OtherControl : NativeElementHitKind.QueryFailed);
            }, () => true);
            Assert(retained.Hit.Conflicts && retained.Point.X == primary.X, "unknown new point replaced known conflict");
            int budgetQueries = 0;
            NativeElementHit.SelectPoint(primary, alternatives, p =>
            {
                budgetQueries++;
                return new NativeElementHit(NativeElementHitKind.OtherControl);
            }, () => false);
            Assert(budgetQueries == 1, "point search ignored query budget");
            try
            {
                NativeElementHit.Observe(target, target.Id, () => other, n => n.Id, n => n.Parent, n => true, n => true, () =>
                {
                    throw new Failure("cancelled", "cancelled");
                });
                throw new InvalidOperationException("hit traversal swallowed cancellation");
            }
            catch (Failure e)
            {
                Assert(e.Code == "cancelled", "hit traversal changed cancellation");
            }

            foreach (bool v1 in new[]
            {
                false,
                true
            }

            )
            {
                foreach (var kind in new[]
                {
                    NativeElementHitKind.Target,
                    NativeElementHitKind.Descendant,
                    NativeElementHitKind.Ancestor,
                    NativeElementHitKind.OtherControl,
                    NativeElementHitKind.OtherBranch,
                    NativeElementHitKind.NoElement,
                    NativeElementHitKind.Incomplete,
                    NativeElementHitKind.QueryFailed,
                    NativeElementHitKind.OutsideScope
                }

                )
                {
                    var p = new ClickElementProbe
                    {
                        Ready = true,
                        Hit = new NativeElementHit(kind, unchecked((int)0x80040201))
                    };
                    var objects = new ObjectProbe();
                    var runtime = new NativeRuntime("session", new ObjectProbeRead(objects), new NativeExecution(objects, pointer: new NativePointer(p, "exclusive", false)));
                    string token = (string)Begin(runtime, v1, capture: false)["operation"];
                    var facts = Inspect(runtime, token, false);
                    var result = Execute(runtime, token, Command(facts));
                    bool conflict = kind == NativeElementHitKind.OtherControl;
                    Assert((string)result["dispatch"] == (conflict ? "notDispatched" : "dispatched") && p.Batches.Count == (conflict ? 0 : 1), "hit admission changed for " + kind);
                    Assert(p.Activated == 0, "hit uncertainty/conflict caused foreground activation");
                    string detail = (string)result["detail"];
                    Assert(conflict ? detail.Contains("another UIA control") : p.Hit.Confirmed ? detail.Contains("UIA confirmed") : detail.Contains("UIA element hit unconfirmed"), "hit certainty missing in receipt for " + kind);
                    if (kind == NativeElementHitKind.QueryFailed)
                        Assert(detail.Contains("0x80040201"), "hit-query error was dropped");
                    Finish(runtime, token);
                }
            }

            var changing = new ClickElementProbe
            {
                Ready = true
            };
            var objectProbe = new ObjectProbe();
            var rt = new NativeRuntime("session", new ObjectProbeRead(objectProbe), new NativeExecution(objectProbe, pointer: new NativePointer(changing, "exclusive", false)));
            string op = (string)Begin(rt, count: 2, capture: false)["operation"];
            var command = Command(Inspect(rt, op, false));
            changing.OnSend = () =>
            {
                changing.Hit = new NativeElementHit(NativeElementHitKind.OtherControl);
            };
            var stopped = Execute(rt, op, command);
            Assert((string)stopped["dispatch"] == "partial" && changing.Batches.Count == 1 && Convert.ToInt32(Transport(stopped)["submitted"]) == 3, "second click ignored new conflict or replayed first click");
            Assert((string)stopped["cleanup"] == "complete" && stopped["retryReason"] == null, "hit conflict erased cleanup/allowed replay");
            Refused(() => Execute(rt, op, command, 2));
            Finish(rt, op);
            changing = new ClickElementProbe
            {
                Ready = true,
                ExactPoint = true
            };
            rt = new NativeRuntime("session", new ObjectProbeRead(objectProbe), new NativeExecution(objectProbe, pointer: new NativePointer(changing, "exclusive", false)));
            op = (string)Begin(rt, count: 2, capture: false)["operation"];
            command = Command(Inspect(rt, op, false));
            changing.OnSend = () =>
            {
                changing.Hit = new NativeElementHit(NativeElementHitKind.QueryFailed);
            };
            stopped = Execute(rt, op, command);
            Assert((string)stopped["dispatch"] == "partial" && changing.Batches.Count == 1 && ((string)stopped["detail"]).Contains("alternate element point"), "frozen alternate degraded to unconfirmed geometry or changed point");
            Finish(rt, op);
        }

        static int Main(string[] args)
        {
            Negotiation();
            var receipt = Prefixes();
            var facts = PreparationAndElement();
            ObjectUnknownAndPreparationStop();
            var isolatedFacts = MetadataIsolation();
            Diagnostics();
            ElementHitEvidence();
            if (args.Length == 1)
                SharedBeginFixtures(args[0]);
            Console.WriteLine(JsonSerializer.Serialize(new { assertions = assertions, facts = facts, receipt = receipt, isolatedFacts = isolatedFacts, scope = "Production Windows click pipeline with fake OS providers; no Windows GUI execution" }));
            return 0;
        }
    }
}

// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal struct NativePointerEvent
    {
        internal readonly uint Flags;
        internal readonly int X, Y, Data;
        internal NativePointerEvent(uint flags, int x = 0, int y = 0, int data = 0)
        {
            Flags = flags;
            X = x;
            Y = y;
            Data = data;
        }
    }

    internal sealed class NativePointerState
    {
        internal object Native;
        // The actual native mouse recipient, not just its top-level window.
        internal object Recipient;
        // Actual foreground identity differs from pointer foreground readiness:
        // a nonactivating popup can use its confirmed owner's foreground.
        internal bool Ready, Foreground, ForegroundReady, CaptureBlocked;
        // PhysicalReady is the native route before element hit semantics.
        // NaturalActivation is an admitted inactive single-click route, not
        // evidence that Windows has activated the target.
        internal bool PhysicalReady, NaturalActivation;
        internal NativeClickOcclusion Occlusion;
    }
    internal sealed class NativePointerClickTiming
    {
        internal int Milliseconds, Width, Height;
    }
    internal interface INativePointerClickTimingProvider
    {
        NativePointerClickTiming ClickTiming();
    }

    internal interface INativePointerProvider
    {
        NativePointerCapture Capture(Dictionary<string, object> target, Request r);
        NativePointerState Read(Dictionary<string, object> target, NativePointerPoint point,
                                NativePointerGestureIntent gesture, bool dragging, Request r);
        void RequireIdle(Request r);
        void Activate(NativePointerState state, Request r);
        int Send(NativePointerEvent[] events);
    }

    internal sealed class NativeElementPointerTarget
    {
        internal object Native;
        internal NativePointerPoint Point;
        internal NativeCaptureGeometry Bounds, WindowBounds, Canvas;
        internal string Layout;
        internal NativeElementHit Hit = new NativeElementHit(NativeElementHitKind.NotSampled);
        internal bool RequiresConfirmedHit;
        internal bool GeometryDeferred;
    }

    internal interface INativeElementPointerProvider
    {
        NativeElementPointerTarget Element(Dictionary<string, object> target, string[] within, Request r);
        NativePointerState ReadElement(Dictionary<string, object> target, string[] within,
                                       NativeElementPointerTarget point, NativePointerGestureIntent gesture, Request r);
    }

    internal sealed class NativePointerPlan
    {
        internal NativePointerCapture Capture;
        internal NativeElementPointerTarget Element;
        internal NativePointerPoint[] Points;
        internal string Kind;
        internal uint Down, Up, Wheel;
        internal int Count, Amount;
        internal object DispatchRecipient;
        internal bool NaturalActivation;
        internal NativeClickOcclusion Occlusion;
        internal NativePointerGestureIntent Gesture
        {
            get {
                return new NativePointerGestureIntent(Kind, Down, Count);
            }
        }
        internal NativePointerEvent Move(int index)
        {
            var p = Element == null ? Capture.Normalize(Points[index])
                                    : NativePointerCapture.Normalize(Points[index], Element.Canvas);
            return new NativePointerEvent(0xc001, p.X, p.Y);
        }
    }

    // One requested physical sequence. Common route selection and retry policy
    // remain in Python; this unit owns dispatch accounting and paired release.
    internal sealed class NativePointer
    {
        readonly INativePointerProvider provider;
        readonly bool foregroundAllowed;
        readonly NativePointerClickHistory clicks = new NativePointerClickHistory();
        internal bool SupportsDesktop { get; private set; }

        internal bool SupportsElements
        {
            get {
                return provider is INativeElementPointerProvider;
            }
        }

        internal NativePointer(INativePointerProvider provider, string mode, bool assist)
        {
            this.provider = provider;
            foregroundAllowed = mode == "exclusive" || assist;
            SupportsDesktop = mode == "exclusive";
        }

        internal static bool Intent(Dictionary<string, object> intent, Dictionary<string, object> target)
        {
            return target != null && target["capture"] != null &&
                   new[] { "click", "scroll", "drag" }.Contains((string)intent["kind"]);
        }

        internal bool ElementIntent(Dictionary<string, object> intent, Dictionary<string, object> target)
        {
            // Lazy Element queries begin with the fixed window and resolve
            // their node inside that same operation before inspection.
            return SupportsElements && target["surface"] != null && target["capture"] == null &&
                   (string)intent["kind"] == "click";
        }

        internal void Validate(Dictionary<string, object> intent, Dictionary<string, object> target)
        {
            if (ElementIntent(intent, target))
            {
                var options = Wire.Object(intent["parameters"]);
                Wire.Keys(options, "", "button,count");
                if (options.ContainsKey("button") &&
                    !new[] { "left", "middle", "right" }.Contains(Wire.Str(options, "button")))
                    throw new Failure("invalid_request", "invalid pointer button");
                if (options.ContainsKey("count"))
                    Wire.Num(options, "count", 1, 2, true);
                return;
            }

            if (target["node"] != null)
                throw new Failure("invalid_request", "image input cannot select an accessibility element");
            if (target["surface"] == null && !SupportsDesktop)
                throw new Failure("mode_unsupported", "desktop pointer input requires exclusive mode");
            string kind = (string)intent["kind"];
            var p = Wire.Object(intent["parameters"]);
            if (kind == "click")
            {
                Wire.Keys(p, "x,y", "button,count");
                if (p.ContainsKey("button") && !new[] { "left", "middle", "right" }.Contains(Wire.Str(p, "button")))
                    throw new Failure("invalid_request", "invalid pointer button");
                if (p.ContainsKey("count"))
                    Wire.Num(p, "count", 1, 2, true);
            }
            else if (kind == "scroll")
            {
                Wire.Keys(p, "x,y,direction,pages");
                if (!new[] { "up", "down", "left", "right" }.Contains(Wire.Str(p, "direction")))
                    throw new Failure("invalid_request", "invalid scroll direction");
                Wire.Num(p, "pages", 0.00001, 100);
            }
            else if (kind == "drag")
                Wire.Keys(p, "fromX,fromY,toX,toY");
            else
                throw new Failure("invalid_request", "unsupported pointer intent");
            foreach (var key in kind == "drag" ? new[] { "fromX", "fromY", "toX", "toY" }

                                               : new[] { "x", "y" }

            )
                Wire.Num(p, key, 0, 4096);
        }

        NativePointerPlan Plan(NativeWriteOperation operation, Dictionary<string, object> target, Request r)
        {
            Validate(operation.Intent, target);
            var image = provider.Capture(target, r);
            var p = Wire.Object(operation.Intent["parameters"]);
            string kind = (string)operation.Intent["kind"];
            var start = image.Point(Convert.ToDouble(p[kind == "drag" ? "fromX" : "x"]),
                                    Convert.ToDouble(p[kind == "drag" ? "fromY" : "y"]));
            var points = new List<NativePointerPoint> { start };
            if (kind == "drag")
            {
                var end = image.Point(Convert.ToDouble(p["toX"]), Convert.ToDouble(p["toY"]));
                for (int i = 1; i <= 24; i++)
                    points.Add(new NativePointerPoint(start.X + (int)Math.Round((end.X - start.X) * i / 24.0),
                                                      start.Y + (int)Math.Round((end.Y - start.Y) * i / 24.0)));
            }

            foreach (var point in points)
                image.RequirePoint(point); // Validate the complete trajectory before any preparation.
            string button = p.ContainsKey("button") ? (string)p["button"] : "left";
            var plan = new NativePointerPlan { Capture = image,
                                               Points = points.ToArray(),
                                               Kind = kind,
                                               Down = button == "left"    ? 2u
                                                      : button == "right" ? 8u
                                                                          : 32u,
                                               Up = button == "left"    ? 4u
                                                    : button == "right" ? 16u
                                                                        : 64u,
                                               Count = p.ContainsKey("count") ? Convert.ToInt32(p["count"]) : 1 };
            if (kind == "scroll")
            {
                string direction = (string)p["direction"];
                plan.Wheel = direction == "left" || direction == "right" ? 0x1000u : 0x800u;
                // Keep the existing Windows API's approximate three-detent page.
                plan.Amount = (int)Math.Round(120 * Math.Max(1, Convert.ToDouble(p["pages"]) * 3)) *
                              (direction == "up" || direction == "right" ? 1 : -1);
            }

            return plan;
        }

        internal object Inspect(NativeWriteOperation operation, Dictionary<string, object> target, string[] within,
                                Request r)
        {
            NativePointerPlan plan;
            NativePointerState state;
            if (ElementIntent(operation.Intent, target))
            {
                Validate(operation.Intent, target);
                var element = ((INativeElementPointerProvider)provider).Element(target, within, r);
                var options = Wire.Object(operation.Intent["parameters"]);
                string button = options.ContainsKey("button") ? (string)options["button"] : "left";
                plan = new NativePointerPlan { Element = element,
                                               Points = new[] { element.Point },
                                               Kind = "click",
                                               Down = button == "left"    ? 2u
                                                      : button == "right" ? 8u
                                                                          : 32u,
                                               Up = button == "left"    ? 4u
                                                    : button == "right" ? 16u
                                                                        : 64u,
                                               Count = options.ContainsKey("count") ? Convert.ToInt32(options["count"])
                                                                                    : 1 };
                state = ((INativeElementPointerProvider)provider).ReadElement(target, within, element, plan.Gesture, r);
            }
            else
            {
                if (within.Length != 0 || target["node"] != null)
                    throw new Failure("invalid_request", "image input cannot select element ancestors");
                plan = Plan(operation, target, r);
                state = provider.Read(target, plan.Points[0], plan.Gesture, false, r);
            }

            r.Check();
            if (plan.Element == null || !plan.Element.GeometryDeferred)
                operation.Indicate(plan.Points[0]);
            var contract = new NativeInputContract {
                Id = "primitive:" + Guid.NewGuid().ToString("N"), Unit = "pointer_sequence",         Supported = true,
                Target = new Dictionary<string, object>(target),  Within = (string[])within.Clone(), Pointer = plan
            };
            plan.Occlusion = state.Occlusion;
            bool semanticOcclusion = plan.Element != null && SupportsDesktop && plan.Gesture.CanActivateWindow &&
                                     NativePointerClickAdmission.ElementScopeAllowsNatural(within) &&
                                     state.Occlusion != null && state.Occlusion.TargetCondition != null &&
                                     !state.Ready && !state.CaptureBlocked;
            bool canPrepare =
                !state.CaptureBlocked && !state.ForegroundReady && foregroundAllowed && target["surface"] != null;
            if (operation.ClickV1)
                contract.Click = new NativeClickOffer(
                    "windows.sendInput", "pointer", "desktop_pointer",
                    state.Ready  ? "ready"
                    : canPrepare ? "preparationRequired"
                                 : "unavailable",
                    state.Ready ? null
                    : state.CaptureBlocked
                        ? "Another native window owns mouse capture; activation cannot prepare this click."
                    : state.ForegroundReady ? UnavailablePoint(plan)
                                            : "The selected window requires foreground preparation.",
                    !state.Ready && canPrepare ? new[] { "foreground" } : new string[0],
                    semanticOcclusion ? "default" : "physicalWhenAvailableInExclusive");
            if (operation.ClickV1)
                contract.ClickAuthority = new NativeClickPreparationAuthority("windows.sendInput", operation, target,
                                                                              foregroundAllowed, true);
            operation.Contracts.Add(contract.Id, contract);
            var primitive =
                Wire.Obj("id", contract.Id, "unit", contract.Unit, "channel", "pointer", "supported", "yes",
                         "readiness", state.Ready ? "yes" : "no", "pending",
                         state.Ready ? new string[0] : new[] { state.ForegroundReady ? "receiver" : "foreground" },
                         "backgroundPreparation", "no");
            if (contract.Click != null)
                primitive["click"] = contract.Click.WireValue();
            var result = Wire.Obj(
                "kind", "inputContext", "binding", new Dictionary<string, object>(target), "observed",
                "observation:" + Guid.NewGuid().ToString("N"), "targetLabel",
                plan.Element != null        ? "Selected element"
                : target["surface"] == null ? "Captured desktop"
                                            : "Captured window",
                "identity", "yes", "withinScope", "yes", "enabled", "unknown", "blockedBy", new string[0], "nativeRole",
                null, "actionsComplete", false, "within", within, "primitives", new[] { primitive }, "input",
                Wire.Obj("foregroundSurface", state.Foreground ? target["surface"] : null, "keyboardSurface", null,
                         "focusedNode", null, "focusAncestors", new string[0], "editor", null, "editorOwner", null,
                         "selectionKnown", "unknown", "geometryCurrent",
                         plan.Element != null && plan.Element.GeometryDeferred ? "no" : "yes", "windowRoute", "unknown",
                         "pointerRoute", state.Ready ? "yes" : "no"));
            if (!state.Ready && state.ForegroundReady)
                result["unavailableReason"] = UnavailablePoint(
                    plan,
                    "The captured point cannot currently reach the selected window; inspect its current image and desktop for an obstruction or input capture.");
            return result;
        }

        internal NativePointerState Guard(NativeInputContract contract, int point, bool dragging, Request r)
        {
            var plan = contract.Pointer;
            if (plan.Element != null)
                return ((INativeElementPointerProvider)provider)
                    .ReadElement(contract.Target, contract.Within, plan.Element, plan.Gesture, r);
            if (!ReferenceEquals(provider.Capture(contract.Target, r), plan.Capture))
                throw new Failure("snapshot_expired", "the original image expired");
            var state = provider.Read(contract.Target, plan.Points[point], plan.Gesture, dragging, r);
            r.Check();
            return state;
        }

        internal static string ClickDetail(NativeInputContract contract)
        {
            string detail =
                contract.Pointer.Element == null
                    ? "Pointer click submitted at the original captured coordinate; read to verify its effect."
                    : contract.Pointer.Element.Hit.SubmissionDetail();
            return contract.Pointer.NaturalActivation
                       ? detail +
                             " This requested click used the reachable inactive original window; activation and control effect require read-back."
                       : detail;
        }

        static string UnavailablePoint(NativePointerPlan plan,
                                       string otherwise = "The original target point is obstructed or captured.")
        {
            return plan.Element != null && plan.Element.Hit.Conflicts
                       ? "The selected point hits another UIA control in the original window; no remaining requested click was sent. Read the window to inspect coverage."
                   : plan.Element != null && plan.Element.RequiresConfirmedHit && !plan.Element.Hit.Confirmed
                       ? "The alternate element point no longer has a confirmed UIA hit; no remaining requested click was sent."
                       : otherwise;
        }

        internal void RefreshClickPoint(NativeInputContract contract, Request r)
        {
            if (contract.Pointer.Element == null)
                return; // A capture owns its immutable mapping.
            var current = ((INativeElementPointerProvider)provider).Element(contract.Target, contract.Within, r);
            contract.Pointer.Element = current;
            contract.Pointer.Points = new[] { current.Point };
        }

        internal void PrepareClick(NativeWriteOperation operation, NativeInputContract contract, bool foreground,
                                   Request r)
        {
            var state = Guard(contract, 0, false, r);
            if (state.Ready)
                return;
            if (state.CaptureBlocked)
                throw new Failure(
                    "input_unavailable",
                    "another native window owns mouse capture; no activation or requested click was sent");
            // This system-stream transport has no background preparation. The
            // common selector owns cross-candidate background-first policy;
            // native authority checks the real host policy and this route.
            if (!state.ForegroundReady && foreground && contract.Target["surface"] != null)
            {
                if (!foregroundAllowed)
                    throw new Failure("mode_unsupported", "host policy does not permit foreground assistance");
                if (contract.Click == null || contract.Click.RouteFamily != "windows.sendInput" ||
                    !contract.Click.PreparationOptions.Contains("foreground") ||
                    contract.Click.PreparationOptions.Contains("background"))
                    throw new Failure("input_unavailable",
                                      "this selected contract has no native authority for foreground preparation");
                if (contract.ClickAuthority == null)
                    throw new Failure("invalid_request", "the selected contract has no preparation authority");
                contract.ClickAuthority.Require(operation, contract);
                provider.RequireIdle(r);
                RefreshClickPoint(contract, r);
                state = Guard(contract, 0, false, r);
                if (state.CaptureBlocked)
                    throw new Failure(
                        "input_unavailable",
                        "another native window acquired mouse capture; no activation or requested click was sent");
                if (!state.Ready && !state.ForegroundReady)
                {
                    var effect = operation.Click.Preparing("foreground");
                    r.Check();
                    r.Effect = true;
                    provider.Activate(state, r);
                    effect.Dispatch = "dispatched";
                    double until = Math.Min(r.Deadline, Wire.Now + 400);
                    do
                    {
                        RefreshClickPoint(contract, r);
                        state = Guard(contract, 0, false, r);
                        if (state.Ready || Wire.Now >= until)
                            break;
                        r.Wait(20);
                    } while (true);
                    effect.Observation = state.Foreground ? "confirmed" : "unavailable";
                }
            }

            if (!state.Ready)
                throw new Failure(
                    "input_unavailable",
                    UnavailablePoint(contract.Pointer, "the captured point is not reachable; no requested input sent"));
        }

        internal void CheckClick(NativeInputContract contract, Request r)
        {
            provider.RequireIdle(r);
            var state = Guard(contract, 0, false, r);
            if (!state.Ready)
                throw new Failure(
                    "input_unavailable",
                    UnavailablePoint(contract.Pointer, "the selected point no longer reaches the original target"));
            contract.Pointer.DispatchRecipient = state.Recipient;
            contract.Pointer.NaturalActivation = state.NaturalActivation;
        }
        NativePointerClickTiming ClickTiming()
        {
            var source = provider as INativePointerClickTimingProvider;
            return source == null ? null : source.ClickTiming();
        }
        internal void PrepareClickCadence(NativeInputContract contract, Request r)
        {
            if (!clicks.HasDown)
                return;
            var timing = ClickTiming();
            if (timing == null)
                return;
            var state = Guard(contract, 0, false, r);
            double remaining =
                clicks.Remaining(state.Recipient, contract.Pointer.Points[0], contract.Pointer.Down, timing, Wire.Now);
            if (remaining <= 0)
                return;
            // A wait is preparation only: cancellation/deadline here has sent
            // none of this operation's requested mouse input.
            double until = Wire.Now + remaining;
            while (Wire.Now < until)
                r.Wait((int)Math.Max(1, Math.Min(25, Math.Ceiling(until - Wire.Now))));
            RefreshClickPoint(contract, r);
        }

        internal void SendClick(NativeWriteOperation operation, NativeInputContract contract, Request r)
        {
            var plan = contract.Pointer;
            bool down = false;
            var timing = ClickTiming();
            if (clicks.Remaining(plan.DispatchRecipient, plan.Points[0], plan.Down, timing, Wire.Now) > 0)
                throw new Failure(
                    "input_unavailable",
                    "the native click recipient changed during click preparation; no requested input was sent");
            double firstDown = 0;
            object firstRecipient = plan.DispatchRecipient;
            operation.Indicate(plan.Points[0], true, plan.Count);
            try
            {
                for (int i = 0; i < plan.Count; i++)
                {
                    if (i > 0)
                    {
                        r.Wait(60);
                        CheckClick(contract, r);
                        if (firstRecipient != null && !Equals(firstRecipient, plan.DispatchRecipient))
                            throw new Failure(
                                "input_unavailable",
                                "the native mouse recipient changed after the first click; the second click was not sent or replayed");
                        if (timing != null && Wire.Now - firstDown >= timing.Milliseconds)
                            throw new Failure(
                                "input_unavailable",
                                "the first click was submitted but target validation exceeded the double-click interval; the second click was not sent or replayed");
                    }

                    var events =
                        new[] { plan.Move(0), new NativePointerEvent(plan.Down), new NativePointerEvent(plan.Up) };
                    operation.Click.BeginCall(r, "SendInput", events.Length);
                    bool prior = down;
                    down = true; // An exception can leave any prefix accepted.
                    int sent;
                    double started = Wire.Now;
                    if (i == 0)
                        firstDown = started;
                    try
                    {
                        sent = provider.Send(events);
                    }
                    catch
                    {
                        clicks.Record(plan.DispatchRecipient, plan.Points[0], plan.Down, Wire.Now);
                        operation.Click.Unknown();
                        throw;
                    }
                    if (sent < 0 || sent > events.Length || sent >= 2)
                        clicks.Record(plan.DispatchRecipient, plan.Points[0], plan.Down, Wire.Now);

                    operation.Click.Returned(sent, events.Length, r);
                    if (sent >= 0 && sent <= events.Length)
                    {
                        down = prior;
                        foreach (var e in events.Take(sent))
                        {
                            if ((e.Flags & plan.Down) != 0)
                                down = true;
                            if ((e.Flags & plan.Up) != 0)
                                down = false;
                        }
                    }

                    if (sent != events.Length)
                        throw new Failure(operation.Click.HasRequestedInput ? "execution_unknown" : "input_unavailable",
                                          "SendInput accepted " + sent + "/" + events.Length +
                                              " pointer events; input was not replayed");
                }

                operation.Click.Completed();
                r.Check(); // Final up may legitimately close the original window.
            }
            finally
            {
                // Global-stream release is owned by this held button, not the
                // lifetime or hit test of the original element/window.
                if (down)
                {
                    try
                    {
                        if (provider.Send(new[] { new NativePointerEvent(plan.Up) }) != 1)
                            operation.Cleanup = "failed";
                    }
                    catch
                    {
                        operation.Cleanup = "failed";
                    }
                }
            }
        }

        void Send(NativeWriteOperation operation, NativePointerPlan plan, NativePointerEvent[] events, Request r,
                  Dictionary<string, object> result, ref bool down)
        {
            r.Sending();
            operation.Dispatched = true;
            result["dispatch"] = "unknown";
            bool prior = down;
            if (events.Any(e => (e.Flags & plan.Down) != 0))
                down = true; // A thrown call may have sent its prefix.
            int sent;
            try
            {
                sent = provider.Send(events);
            }
            catch
            {
                if (plan.Kind == "click")
                    clicks.Record(plan.DispatchRecipient, plan.Points[0], plan.Down, Wire.Now);
                throw;
            }
            if (plan.Kind == "click" && (sent < 0 || sent > events.Length || sent >= 2))
                clicks.Record(plan.DispatchRecipient, plan.Points[0], plan.Down, Wire.Now);
            if (sent >= 0 && sent <= events.Length)
            {
                down = prior;
                foreach (var e in events.Take(sent))
                {
                    if ((e.Flags & plan.Down) != 0)
                        down = true;
                    if ((e.Flags & plan.Up) != 0)
                        down = false;
                }
            }

            if (sent != events.Length)
                throw new Failure("execution_unknown", "SendInput accepted " + sent + "/" + events.Length +
                                                           " pointer events; input was not replayed");
        }

        internal object Execute(NativeWriteOperation operation, Dictionary<string, object> command, Request r)
        {
            var target = Wire.Object(command["target"]);
            var preparation = new List<string>();
            bool down = false;
            NativeInputContract contract = null;
            var result =
                Wire.Obj("target", new Dictionary<string, object>(target), "dispatch", "notDispatched", "detail",
                         "No pointer input dispatched.", "preparation", preparation, "confirmation", "unavailable",
                         "cleanup", "complete", "errorCode", null, "retryReason", null, "context", null);
            try
            {
                if (!operation.Contracts.TryGetValue((string)command["primitiveId"], out contract) ||
                    contract.Unit != "pointer_sequence" || contract.Pointer == null || !contract.Supported ||
                    !NativeWriteOperation.Same(target, contract.Target))
                    throw new Failure("stale_target", "the pointer contract expired or belongs to another image");
                bool preparing = (string)command["kind"] == "prepare_receiver";
                if (!preparing && ((string)command["kind"] != "pointer_sequence" || (bool)command["foreground"]))
                    throw new Failure("invalid_request", "pointer dispatch cannot activate a window");
                Validate(operation.Intent, target);
                var plan = contract.Pointer;
                if (preparing)
                {
                    var state = Guard(contract, 0, false, r);
                    if (state.CaptureBlocked)
                        throw new Failure(
                            "input_unavailable",
                            "another native window owns mouse capture; no activation or requested click was sent");
                    if (!state.Ready && !state.ForegroundReady && (bool)command["foreground"] &&
                        target["surface"] != null)
                    {
                        if (!foregroundAllowed)
                            throw new Failure("mode_unsupported", "host policy does not permit foreground assistance");
                        provider.RequireIdle(r);
                        state = Guard(contract, 0, false, r);
                        if (state.CaptureBlocked)
                            throw new Failure(
                                "input_unavailable",
                                "another native window acquired mouse capture; no activation or requested click was sent");
                        if (!state.Ready && !state.ForegroundReady)
                        {
                            r.Check();
                            preparation.Add("foreground");
                            r.Effect = true;
                            provider.Activate(state, r);
                            double until = Math.Min(r.Deadline, Wire.Now + 400);
                            while (!(state = Guard(contract, 0, false, r)).Ready && Wire.Now < until)
                                r.Wait(20);
                        }
                    }

                    if (!state.Ready)
                        throw new Failure(
                            "input_unavailable",
                            UnavailablePoint(plan, "the captured point is not reachable; no requested input sent"));
                    result["confirmation"] = "confirmed";
                    result["detail"] = "The captured pointer target is reachable.";
                }
                else
                {
                    if (plan.Kind == "click")
                    {
                        PrepareClickCadence(contract, r);
                        CheckClick(contract, r);
                        if (clicks.Remaining(plan.DispatchRecipient, plan.Points[0], plan.Down, ClickTiming(),
                                             Wire.Now) > 0)
                            throw new Failure(
                                "input_unavailable",
                                "the native click recipient changed during click preparation; no requested input was sent");
                    }
                    else
                    {
                        provider.RequireIdle(r);
                        if (!Guard(contract, 0, false, r).Ready)
                            throw new Failure(
                                "input_unavailable",
                                UnavailablePoint(plan, "the captured point no longer reaches the selected window"));
                    }
                    if (plan.Kind == "drag")
                    {
                        operation.Indicate(plan.Points[0], true, 1, true);
                        Send(operation, plan, new[] { plan.Move(0), new NativePointerEvent(plan.Down) }, r, result,
                             ref down);
                        for (int i = 1; i < plan.Points.Length; i++)
                        {
                            r.Wait(12);
                            if (!Guard(contract, i, true, r).Ready)
                                throw new Failure("input_unavailable", "the original window lost the drag receiver");
                            operation.Indicate(plan.Points[i], dragging: true);
                            Send(operation, plan, new[] { plan.Move(i) }, r, result, ref down);
                        }

                        Send(operation, plan, new[] { new NativePointerEvent(plan.Up) }, r, result, ref down);
                        operation.Indicate(plan.Points[plan.Points.Length - 1]);
                    }
                    else if (plan.Kind == "scroll")
                    {
                        operation.Indicate(plan.Points[0]);
                        Send(operation, plan,
                             new[] { plan.Move(0), new NativePointerEvent(plan.Wheel, data: plan.Amount) }, r, result,
                             ref down);
                    }
                    else
                    {
                        operation.Indicate(plan.Points[0], true, plan.Count);
                        var timing = ClickTiming();
                        double firstDown = Wire.Now;
                        object firstRecipient = plan.DispatchRecipient;
                        for (int i = 0; i < plan.Count; i++)
                        {
                            if (i > 0)
                            {
                                r.Wait(60);
                                CheckClick(contract, r);
                                if (firstRecipient != null && !Equals(firstRecipient, plan.DispatchRecipient))
                                    throw new Failure(
                                        "input_unavailable",
                                        "the native mouse recipient changed after the first click; the second click was not sent or replayed");
                                if (timing != null && Wire.Now - firstDown >= timing.Milliseconds)
                                    throw new Failure(
                                        "input_unavailable",
                                        "the first click was submitted but target validation exceeded the double-click interval; the second click was not sent or replayed");
                            }
                            if (i == 0)
                                firstDown = Wire.Now;

                            Send(operation, plan,
                                 new[] { plan.Move(0), new NativePointerEvent(plan.Down),
                                         new NativePointerEvent(plan.Up) },
                                 r, result, ref down);
                        }
                    }

                    result["dispatch"] = "dispatched";
                    result["detail"] = plan.Element != null ? ClickDetail(contract)
                                                            : "Pointer sequence sent; read to verify its effect.";
                    r.Check(); // The final click/drop can legitimately destroy its target.
                }
            }
            catch (Exception error)
            {
                var desktopCleanup = error as NativeDesktopCleanupFailure;
                if (desktopCleanup != null)
                    operation.RetireWorker(desktopCleanup.Reason);
                var failure = error as Failure ?? (desktopCleanup == null ? null : desktopCleanup.OriginalFailure);
                result["errorCode"] = failure != null        ? failure.Code
                                      : operation.Dispatched ? "execution_unknown"
                                                             : "input_unavailable";
                result["detail"] =
                    String.IsNullOrWhiteSpace(error.Message) ? "Native pointer operation failed." : error.Message;
                if (!operation.Dispatched && (string)result["errorCode"] == "input_unavailable")
                    result["retryReason"] =
                        (string)command["kind"] == "prepare_receiver" ? "preparationUnavailable" : "routeUnavailable";
            }
            finally
            {
                // Cleanup has no cancellable Request or target lookup. Even a
                // destroyed window cannot strand our button in the down state.
                if (down)
                {
                    try
                    {
                        if (provider.Send(new[] { new NativePointerEvent(contract.Pointer.Up) }) != 1)
                            operation.Cleanup = "failed";
                    }
                    catch
                    {
                        operation.Cleanup = "failed";
                    }
                }
            }

            result["cleanup"] = operation.Cleanup;
            if (operation.Cleanup != "complete")
            {
                result["retryReason"] = null;
                result["errorCode"] = result["errorCode"] ?? "execution_unknown";
                result["detail"] =
                    (string)result["detail"] + " Pointer release was not confirmed; reconnect before more input.";
            }

            return result;
        }
    }
}

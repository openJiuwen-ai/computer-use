// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

namespace ComputerUse.Windows
{
internal static class NativeScrollTests
{
    sealed class Objects : INativeObjectProvider
    {
        public IDisposable Acquire(Request request)
        {
            throw new Exception("Scrolling must not acquire another object route");
        }
        public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request request)
        {
            throw new Exception("Scrolling must retain its addressed scrolling provider");
        }
        public Action Prepare(NativeObjectState state, string unit, string action, string text, Request request)
        {
            throw new Exception("Scrolling must not substitute another object operation");
        }
    }

    sealed class Provider : INativeScrollProvider
    {
        internal bool Scrollable = true, Enabled = true, ThrowPosition;
        internal double? Percent, ViewSize;
        internal int PercentReads, ViewSizeReads;
        internal Action Prepared, Sent;
        internal readonly List<bool> PositionRequests = new List<bool>();
        internal readonly List<string> Dispatches = new List<string>();
        public NativeScrollState Read(Dictionary<string, object> target, string[] within, string direction,
                                      bool needsPosition, Request request)
        {
            request.Check();
            PositionRequests.Add(needsPosition);
            var state = new NativeScrollState { Role = "ControlType.Pane", Enabled = Enabled };
            // Exercise the same property reader used by the UIA adapter. A
            // provider can implement Scroll while metric getters fail.
            NativeScrollPatternObservation.Read(
                state, needsPosition, () => Scrollable,
                () =>
                {
                    PercentReads++;
                    if (ThrowPosition)
                        throw new COMException("Scroll percentage is unavailable");
                    return Percent;
                },
                () =>
                {
                    ViewSizeReads++;
                    if (ThrowPosition)
                        throw new COMException("Viewport size is unavailable");
                    return ViewSize;
                });
            request.Check();
            return state;
        }
        public Action Prepare(NativeScrollState state, string direction, double pages, Request request)
        {
            request.Check();
            string dispatch = pages == 1
                                  ? "Scroll.Large" + (direction == "up" || direction == "left" ? "Decrement" : "Increment")
                                  : "SetScrollPercent:" + NativeScroll.FractionalPosition(state, direction, pages);
            if (Prepared != null)
                Prepared();
            return () =>
            {
                Dispatches.Add(dispatch);
                if (Sent != null)
                    Sent();
            };
        }
    }

    sealed class Operation
    {
        internal readonly NativeExecution Engine;
        internal readonly NativeWriteOperation Write;
        internal readonly Request Request = new Request(CancellationToken.None, 3000);
        internal readonly Dictionary<string, object> Target =
            Wire.Obj("session", "session", "instance", "instance", "rootWindow", "window", "surface", "surface",
                     "node", "scroller", "capture", null);
        internal Dictionary<string, object> Primitive;
        internal Operation(Provider provider, string direction, double pages)
        {
            Engine = new NativeExecution(new Objects(), scroll: new NativeScroll(provider));
            Write = new NativeWriteOperation(Target,
                Wire.Obj("kind", "scroll", "parameters", Wire.Obj("direction", direction, "pages", pages)));
        }
        internal void Inspect()
        {
            Engine.Validate(Write.Intent, Target);
            var facts = Wire.Object(Engine.Inspect(Write, Target, new string[0], Request));
            Primitive = Wire.Object(Wire.Array(facts["primitives"])[0]);
        }
        internal Dictionary<string, object> Execute()
        {
            return Wire.Object(Engine.Execute(Write,
                Wire.Obj("kind", "native_scroll", "primitiveId", Primitive["id"], "target", Target,
                         "foreground", false), Request));
        }
    }

    static void Check(bool condition, string detail)
    {
        if (!condition)
            throw new Exception(detail);
    }

    static void IntegerWithoutPosition()
    {
        foreach (string direction in new[] { "up", "down", "left", "right" })
        {
            var provider = new Provider { ThrowPosition = true };
            var operation = new Operation(provider, direction, 2);
            operation.Inspect();
            Check((string)operation.Primitive["supported"] == "yes", "Integer scrolling needs no position metrics");
            var result = operation.Execute();
            string expected = "Scroll.Large" + (direction == "up" || direction == "left" ? "Decrement" : "Increment");
            Check(result["errorCode"] == null && (string)result["dispatch"] == "dispatched" &&
                  provider.Dispatches.Count == 2 && provider.Dispatches.TrueForAll(value => value == expected),
                  "Integer pages must retain the requested Scroll Large* recipe");
            Check(provider.PercentReads == 0 && provider.ViewSizeReads == 0 &&
                  provider.PositionRequests.TrueForAll(value => !value),
                  "Neither inspection nor per-page guards may query unused integer position metrics");
        }
    }

    static void FractionalMissingPosition()
    {
        var provider = new Provider();
        var operation = new Operation(provider, "down", 0.5);
        operation.Inspect();
        Check((string)operation.Primitive["supported"] == "no" && provider.PercentReads == 1 &&
              provider.ViewSizeReads == 1, "Fractional scrolling must require real current position metrics");
        var result = operation.Execute();
        Check(result["errorCode"] != null && (string)result["dispatch"] == "notDispatched" &&
              !operation.Write.Dispatched && provider.Dispatches.Count == 0,
              "Missing fractional metrics must never fabricate a position or send an edit");

        var changed = new Provider { Percent = 10, ViewSize = 20 };
        var retained = new Operation(changed, "down", 0.5);
        retained.Inspect();
        Check((string)retained.Primitive["supported"] == "yes", "Valid fractional metrics must offer its recipe");
        changed.Percent = changed.ViewSize = null;
        var refused = retained.Execute();
        Check((string)refused["errorCode"] == "input_unavailable" &&
              (string)refused["dispatch"] == "notDispatched" && !retained.Write.Dispatched &&
              changed.Dispatches.Count == 0,
              "Fractional execution must sample current metrics rather than reuse inspection's old position");
    }

    static void PositionOnlyForFractionalStep()
    {
        var provider = new Provider { Percent = 10, ViewSize = 20 };
        var operation = new Operation(provider, "down", 1.5);
        operation.Inspect();
        provider.PositionRequests.Clear();
        var result = operation.Execute();
        Check(result["errorCode"] == null && provider.Dispatches.Count == 2 &&
              String.Join(",", provider.PositionRequests) == "False,False,True,False",
              "A mixed scroll must query position only for its fractional dispatch step");
    }

    static void CapabilityChangeBetweenPages()
    {
        var provider = new Provider { ThrowPosition = true };
        provider.Sent = () => provider.Scrollable = false;
        var operation = new Operation(provider, "down", 2);
        operation.Inspect();
        var result = operation.Execute();
        Check((string)result["dispatch"] == "partial" && operation.Write.Dispatched &&
              (string)result["errorCode"] == "input_unavailable" && result["retryReason"] == null &&
              provider.Dispatches.Count == 1,
              "Lost direction capability must stop page two and retain page one's dispatch progress");
    }

    static void CapabilityChangeDuringPreparation()
    {
        var provider = new Provider { ThrowPosition = true };
        provider.Prepared = () => provider.Enabled = false;
        var operation = new Operation(provider, "down", 1);
        operation.Inspect();
        var result = operation.Execute();
        Check((string)result["dispatch"] == "notDispatched" && !operation.Write.Dispatched &&
              (string)result["errorCode"] == "input_unavailable" && provider.Dispatches.Count == 0,
              "The final per-page guard must still reject a disabled retained scroller");
    }

    static void Main()
    {
        IntegerWithoutPosition();
        FractionalMissingPosition();
        PositionOnlyForFractionalStep();
        CapabilityChangeBetweenPages();
        CapabilityChangeDuringPreparation();
        Console.WriteLine("NativeScrollTests: passed (recipe-specific facts, fractional refusal, page guards, progress)");
    }
}
}

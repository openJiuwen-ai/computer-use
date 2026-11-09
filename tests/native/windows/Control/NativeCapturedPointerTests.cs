// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Threading;

namespace ComputerUse.Windows
{
internal static class NativeCapturedPointerTests
{
    sealed class Objects : INativeObjectProvider
    {
        public IDisposable Acquire(Request request) { throw new Exception("No object lease in this pointer test"); }
        public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request request)
        { throw new Exception("Image pointer input must not require an accessibility tree"); }
        public Action Prepare(NativeObjectState state, string unit, string action, string text, Request request)
        { throw new Exception("Image pointer input must not substitute an object mutation"); }
    }

    sealed class Provider : INativePointerProvider, INativeCapturedPointerProvider
    {
        internal readonly NativePointerCapture Image;
        internal NativeCapturedWindowFrame Current;
        internal bool Foreground = true, Identity = true, Layout = true, Modal = false, OwnedGrab;
        internal bool SampleNaturalActivation, CompetingForegroundHidden;
        internal int Activations, FrozenReads;
        internal int FirstAccepted = -1;
        internal Action Activated = null, BeforeFrozenRead = null;
        internal Action<int> AfterSend = null;
        internal readonly List<NativePointerPoint> Hits = new List<NativePointerPoint>();
        internal readonly List<NativePointerEvent[]> Sends = new List<NativePointerEvent[]>();
        internal Provider(NativePointerCapture image) { Image = image; Current = NativeCapturedWindowFrame.From(image.WindowBounds); }
        public NativePointerCapture Capture(Dictionary<string, object> target, Request request)
        {
            request.Check();
            if (!NativeWriteOperation.Same(target, Image.Binding))
                throw new Failure("stale_target", "capture binding changed");
            return Image;
        }
        public NativePointerState Read(Dictionary<string, object> target, NativePointerPoint point,
                                       NativePointerGestureIntent gesture, bool dragging, Request request)
        {
            // The pre-change provider required the original full frame. This
            // remains the conservative fallback without the projection port.
            return ReadCaptured(target, point, gesture, dragging, NativeCapturedWindowFrame.From(Image.WindowBounds), request);
        }
        public NativePointerState ReadCaptured(Dictionary<string, object> target, NativePointerPoint point,
                                               NativePointerGestureIntent gesture, bool dragging,
                                               NativeCapturedWindowFrame frozenWindow, Request request)
        {
            Capture(target, request);
            if (!Identity) throw new Failure("stale_target", "the native window lifetime changed");
            if (!Layout) throw new Failure("snapshot_expired", "display layout changed");
            if (Modal) throw new Failure("blocked_by_modal", "a known modal blocks this original window");
            if (frozenWindow != null)
            {
                FrozenReads++;
                if (BeforeFrozenRead != null) BeforeFrozenRead();
            }
            if (target["surface"] != null)
            {
                NativeCapturedPointerFrame.RequireWindow(Image, frozenWindow, Current, dragging && OwnedGrab);
                if (frozenWindow == null) point = NativeCapturedPointerFrame.Project(Image, Current, point);
            }
            Image.RequirePoint(point);
            Hits.Add(point);
            return State(point, gesture);
        }
        NativePointerState State(NativePointerPoint point, NativePointerGestureIntent gesture)
        {
            bool natural = !Foreground && SampleNaturalActivation && Image.Binding["surface"] != null &&
                NativePointerClickAdmission.NaturalActivation(gesture, true, true, true, CompetingForegroundHidden);
            return new NativePointerState { Ready = Foreground || natural, NaturalActivation = natural, Foreground = Foreground,
                ForegroundReady = Foreground, PhysicalReady = true, Recipient = "original-hwnd",
                ProjectedPoint = point, CurrentWindowBounds = Image.Binding["surface"] == null ? null : Current };
        }
        public void RequireIdle(Request request) { request.Check(); }
        public void Activate(NativePointerState state, Request request)
        {
            request.Check(); Activations++; Foreground = true;
            if (Activated != null) Activated();
        }
        public int Send(NativePointerEvent[] events)
        {
            Sends.Add((NativePointerEvent[])events.Clone());
            int accepted = Sends.Count == 1 && FirstAccepted >= 0 ? FirstAccepted : events.Length;
            for (int index = 0; index < Math.Min(accepted, events.Length); index++)
            {
                var item = events[index];
                if ((item.Flags & 2) != 0) OwnedGrab = true;
                if ((item.Flags & 4) != 0) OwnedGrab = false;
            }
            if (AfterSend != null) AfterSend(Sends.Count);
            return accepted;
        }
    }

    sealed class Legacy : INativePointerProvider
    {
        readonly Provider inner;
        internal Legacy(Provider provider) { inner = provider; }
        public NativePointerCapture Capture(Dictionary<string, object> target, Request request)
        { return inner.Capture(target, request); }
        public NativePointerState Read(Dictionary<string, object> target, NativePointerPoint point,
                                       NativePointerGestureIntent gesture, bool dragging, Request request)
        { return inner.Read(target, point, gesture, dragging, request); }
        public void RequireIdle(Request request) { inner.RequireIdle(request); }
        public void Activate(NativePointerState state, Request request) { inner.Activate(state, request); }
        public int Send(NativePointerEvent[] events) { return inner.Send(events); }
    }

    sealed class Operation
    {
        internal readonly Provider Provider;
        internal readonly NativeExecution Engine;
        internal readonly NativeWriteOperation Write;
        internal readonly Request Request = new Request(CancellationToken.None, 5000);
        internal readonly List<NativePointerPoint> Indicators = new List<NativePointerPoint>();
        internal Dictionary<string, object> Primitive;
        internal Operation(Provider provider, string kind = "click", int count = 1, bool negotiated = true,
                           INativePointerProvider transport = null)
        {
            Provider = provider;
            Engine = new NativeExecution(new Objects(), pointer: new NativePointer(transport ?? provider));
            var parameters = kind == "drag" ? Wire.Obj("fromX", 10, "fromY", 20, "toX", 40, "toY", 30)
                : kind == "scroll" ? Wire.Obj("x", 10, "y", 20, "direction", "down", "pages", 1)
                : Wire.Obj("x", 10, "y", 20, "count", count);
            Write = new NativeWriteOperation(provider.Image.Binding, Wire.Obj("kind", kind, "parameters", parameters),
                                              kind == "click" && negotiated);
            Write.DisplayPointer = (point, pulse, repeats, dragging) => { if (pulse || dragging) Indicators.Add(point); };
        }
        internal void Inspect()
        {
            var facts = Wire.Object(Engine.Inspect(Write, Provider.Image.Binding, new string[0], Request));
            Primitive = Wire.Object(Wire.Array(facts["primitives"])[0]);
        }
        internal Dictionary<string, object> Execute(bool foreground = false)
        {
            return Wire.Object(Engine.Execute(Write,
                Wire.Obj("kind", "pointer_sequence", "primitiveId", Primitive["id"], "target", Provider.Image.Binding,
                         "foreground", foreground), Request));
        }
        internal Dictionary<string, object> Prepare()
        {
            return Wire.Object(Engine.Execute(Write,
                Wire.Obj("kind", "prepare_receiver", "primitiveId", Primitive["id"], "target", Provider.Image.Binding,
                         "foreground", true), Request));
        }
    }

    static NativeCaptureGeometry Box(int x, int y, int width, int height, int dimension = 1)
    { return new NativeCaptureGeometry(x, y, width, height, dimension); }
    static NativePointerCapture Image(bool desktop = false)
    {
        var target = Wire.Obj("session", "session", "instance", desktop ? null : "app", "rootWindow",
                             desktop ? null : "root", "surface", desktop ? null : "surface", "node", null,
                             "capture", "image");
        var canvas = Box(-1000, -1000, 3000, 3000);
        var displays = new[] { Box(-1000, -1000, 1500, 3000), Box(500, -1000, 1500, 3000) };
        return new NativePointerCapture(target, Box(108, 108, 384, 284, 192), canvas, "layout", displays,
                                        Box(100, 100, 400, 300));
    }
    static void Check(bool condition, string detail)
    { if (!condition) throw new Exception(detail); }
    static bool Same(NativePointerPoint left, NativePointerPoint right)
    { return left.X == right.X && left.Y == right.Y; }
    static void CheckFirstMove(Provider provider, NativePointerPoint physical)
    {
        var expected = NativePointerCapture.Normalize(physical, provider.Image.Canvas);
        var sent = provider.Sends[0][0];
        Check(sent.Flags == 0xc001 && sent.X == expected.X && sent.Y == expected.Y,
              "SendInput normalization must consume the same frozen physical point as hit testing");
    }

    static void TranslationPreservesInsetAndScaling()
    {
        foreach (bool negotiated in new[] { true, false })
        {
            var image = Image();
            var provider = new Provider(image);
            var operation = new Operation(provider, negotiated: negotiated);
            operation.Inspect();
            provider.Current = new NativeCapturedWindowFrame(300, -50, 400, 300);
            var result = operation.Execute();
            var expected = new NativePointerPoint(328, -2);
            Check(result["errorCode"] == null && provider.Sends.Count == 1,
                  "A pure full-window translation must not expire its original image pointer plan");
            Check(Same(provider.Hits[provider.Hits.Count - 1], expected) && Same(operation.Indicators[0], expected),
                  "Hit testing and the dispatch indicator must retain WGC inset and original pixel scaling");
            CheckFirstMove(provider, expected);
            Check(image.Image.X == 108 && image.WindowBounds.X == 100, "The original capture record must stay immutable");
        }
    }

    static void OriginallyOffscreenImagePointsCanTranslateOntoDisplays()
    {
        var original = Image();
        var outside = new NativePointerCapture(original.Binding, Box(2208, 108, 384, 284, 192), original.Canvas,
            original.Layout, new[] { Box(-1000, -1000, 3000, 3000) }, Box(2200, 100, 400, 300));
        var provider = new Provider(outside) { Current = new NativeCapturedWindowFrame(300, -50, 400, 300) };
        var operation = new Operation(provider);
        operation.Inspect();
        Check(operation.Execute()["errorCode"] == null,
              "Valid local window-image pixels may move from outside the old canvas into a current visible point");
        CheckFirstMove(provider, new NativePointerPoint(328, -2));

        var gap = new NativePointerCapture(original.Binding, Box(308, 8, 184, 84, 92), Box(0, 0, 1000, 500),
            original.Layout, new[] { Box(0, 0, 300, 500), Box(500, 0, 500, 500) }, Box(300, 0, 200, 100));
        var gapProvider = new Provider(gap) { Current = new NativeCapturedWindowFrame(0, 0, 200, 100) };
        var gapOperation = new Operation(gapProvider);
        gapOperation.Inspect();
        Check(gapOperation.Execute()["errorCode"] == null,
              "Old absolute monitor-gap coordinates are not a prerequisite for current translated window pixels");
        CheckFirstMove(gapProvider, new NativePointerPoint(28, 48));
    }

    static void PreparationMovesBeforeTheSingleFreeze()
    {
        var provider = new Provider(Image()) { Foreground = false };
        provider.Activated = () => provider.Current = new NativeCapturedWindowFrame(500, 200, 400, 300);
        var operation = new Operation(provider);
        operation.Inspect();
        var result = operation.Execute(true);
        Check(result["errorCode"] == null && provider.Activations == 1 && provider.Sends.Count == 1,
              "Foreground preparation may move the same window before the dispatch frame is frozen");
        CheckFirstMove(provider, new NativePointerPoint(528, 248));
    }

    static void HiddenCompetingForegroundRequiresPreparation()
    {
        var hidden = new Provider(Image()) {
            Foreground = false, SampleNaturalActivation = true, CompetingForegroundHidden = true
        };
        var operation = new Operation(hidden);
        operation.Inspect();
        var offer = Wire.Object(operation.Primitive["click"]);
        Check(Equals(operation.Primitive["readiness"], "no") &&
              Equals(offer["admission"], "preparationRequired") &&
              Wire.Array(offer["preparationOptions"]).Length == 1 &&
              Equals(Wire.Array(offer["preparationOptions"])[0], "foreground") && hidden.Sends.Count == 0,
              "A reachable captured window with a known hidden competing foreground must offer preparation before input");
        hidden.AfterSend = sent => Check(hidden.Foreground && hidden.Activations == 1,
                                          "The captured click cannot enter dispatch before original foreground preparation");
        var result = operation.Execute(true);
        Check(result["errorCode"] == null && hidden.Activations == 1 && hidden.Sends.Count == 1 && hidden.Sends[0].Length == 3,
              "One prepared captured click must dispatch one move/down/up batch");

        var visible = new Provider(Image()) { Foreground = false, SampleNaturalActivation = true };
        operation = new Operation(visible);
        operation.Inspect();
        Check(Equals(operation.Primitive["readiness"], "yes") &&
              Equals(Wire.Object(operation.Primitive["click"])["admission"], "ready"),
              "A reachable inactive captured window with a visible competitor keeps natural activation");
        result = operation.Execute();
        Check(result["errorCode"] == null && visible.Activations == 0 && visible.Sends.Count == 1,
              "A visible competitor must not cause an extra activation");

        var changed = new Provider(Image()) { Foreground = false, SampleNaturalActivation = true };
        operation = new Operation(changed);
        operation.Inspect();
        changed.CompetingForegroundHidden = true;
        result = operation.Execute();
        Check(result["errorCode"] != null && changed.Activations == 0 && changed.Sends.Count == 0,
              "A fresh hidden foreground cannot dispatch under the previously ready contract or activate without authority");
    }

    static void ChangedFrameAfterFreezeCannotBeFollowed()
    {
        var provider = new Provider(Image());
        var operation = new Operation(provider);
        operation.Inspect();
        provider.BeforeFrozenRead = () =>
        {
            provider.Current = new NativeCapturedWindowFrame(120, 100, 400, 300);
            provider.BeforeFrozenRead = null;
        };
        var result = operation.Execute();
        Check((string)result["errorCode"] == "snapshot_expired" && !operation.Write.Dispatched && provider.Sends.Count == 0,
              "A window move after freeze must fail the final guard rather than recompute the requested point");
    }

    static void DimensionsIdentityLayoutAndModalRemainMandatory()
    {
        foreach (string change in new[] { "resize", "identity", "layout", "modal" })
        {
            var provider = new Provider(Image());
            var operation = new Operation(provider);
            operation.Inspect();
            if (change == "resize") provider.Current = new NativeCapturedWindowFrame(100, 100, 401, 300);
            if (change == "identity") provider.Identity = false;
            if (change == "layout") provider.Layout = false;
            if (change == "modal") provider.Modal = true;
            var result = operation.Execute();
            Check(result["errorCode"] != null && !operation.Write.Dispatched && provider.Sends.Count == 0,
                  "Translation must not waive original size, lifetime, display layout or known modal checks");
        }
    }

    static void FirstClickProgressCannotRebaseTheSecondClick()
    {
        var provider = new Provider(Image());
        var operation = new Operation(provider, count: 2);
        operation.Inspect();
        provider.AfterSend = count =>
        {
            if (count == 1) provider.Current = new NativeCapturedWindowFrame(150, 100, 400, 300);
        };
        var result = operation.Execute();
        Check((string)result["dispatch"] == "partial" && result["errorCode"] != null &&
              result["retryReason"] == null && operation.Write.Dispatched && provider.Sends.Count == 1,
              "A first click followed by window movement must remain partial, without moving or replaying click two");
    }

    static void PartialSendKeepsFrozenPointAndIndependentRelease()
    {
        var provider = new Provider(Image()) { FirstAccepted = 2 };
        var operation = new Operation(provider);
        operation.Inspect();
        provider.Current = new NativeCapturedWindowFrame(300, -50, 400, 300);
        provider.AfterSend = count =>
        {
            if (count == 1)
            {
                provider.Current = new NativeCapturedWindowFrame(900, 900, 400, 300);
                provider.Identity = false;
            }
        };
        var result = operation.Execute();
        Check((string)result["dispatch"] == "partial" && result["retryReason"] == null &&
              operation.Write.InputTerminated && provider.Sends.Count == 2 && !provider.OwnedGrab &&
              provider.Sends[1].Length == 1 && provider.Sends[1][0].Flags == 4,
              "A partially accepted click must release its owned button without target lookup, rebase or replay");
        CheckFirstMove(provider, new NativePointerPoint(328, -2));
    }

    static void OriginalImageCanStartIndependentOperationsWithoutAccumulatingDelta()
    {
        var image = Image();
        foreach (int origin in new[] { 300, 500 })
        {
            var provider = new Provider(image) { Current = new NativeCapturedWindowFrame(origin, 100, 400, 300) };
            var operation = new Operation(provider);
            operation.Inspect();
            Check(operation.Execute()["errorCode"] == null, "A retained image supports a fresh per-operation projection");
            CheckFirstMove(provider, new NativePointerPoint(origin + 28, 148));
        }
        Check(image.Image.X == 108 && image.WindowBounds.X == 100, "Per-operation deltas must never rewrite the capture");
    }

    static void DesktopAndCrossDisplayPhysicalCoordinates()
    {
        var desktop = new Provider(Image(true)) { Current = new NativeCapturedWindowFrame(-5000, -5000, 400, 300) };
        var desktopOperation = new Operation(desktop);
        desktopOperation.Inspect();
        Check(desktopOperation.Execute()["errorCode"] == null, "Desktop coordinates must not follow a window frame");
        CheckFirstMove(desktop, new NativePointerPoint(128, 148));

        var moved = new Provider(Image()) { Current = new NativeCapturedWindowFrame(-600, 100, 400, 300) };
        var movedOperation = new Operation(moved, "scroll");
        movedOperation.Inspect();
        Check(movedOperation.Execute()["errorCode"] == null, "Unchanged layout allows physical translation across displays");
        CheckFirstMove(moved, new NativePointerPoint(-572, 148));

        var original = Image();
        var noInset = new NativePointerCapture(original.Binding, Box(100, 100, 400, 300, 200), original.Canvas,
            original.Layout, new[] { Box(-1000, -1000, 3000, 3000) }, original.WindowBounds);
        var printWindow = new Provider(noInset) { Current = new NativeCapturedWindowFrame(300, -50, 400, 300) };
        var printOperation = new Operation(printWindow);
        printOperation.Inspect();
        Check(printOperation.Execute()["errorCode"] == null, "A capture without WGC inset retains the same formula");
        CheckFirstMove(printWindow, new NativePointerPoint(320, -10));
    }

    static void FrozenDragUsesItsAbsolutePathUnderTheOriginalGrab()
    {
        foreach (bool loseGrab in new[] { false, true })
        {
            var provider = new Provider(Image());
            var operation = new Operation(provider, "drag");
            operation.Inspect();
            provider.Current = new NativeCapturedWindowFrame(300, -50, 400, 300);
            provider.AfterSend = count =>
            {
                if (count == 1) provider.Current = new NativeCapturedWindowFrame(-900, -900, 10000, 10000);
                if (loseGrab && count == 2) provider.OwnedGrab = false;
            };
            var result = operation.Execute();
            CheckFirstMove(provider, new NativePointerPoint(328, -2));
            Check(provider.Sends[provider.Sends.Count - 1][0].Flags == 4 && !provider.OwnedGrab,
                  "The owned button must be released even if the drag's route becomes invalid");
            if (loseGrab)
                Check(result["errorCode"] != null && operation.Write.Dispatched && result["retryReason"] == null &&
                      provider.Sends.Count == 3,
                      "Lost original capture must stop the frozen drag and release without replay");
            else
            {
                Check(result["errorCode"] == null && provider.Sends.Count == 26,
                      "An owned drag may move/resize its window while its physical trajectory stays fixed");
                for (int index = 1; index <= 24; index++)
                {
                    var physical = new NativePointerPoint(328 + (int)Math.Round(60 * index / 24.0),
                                                          -2 + (int)Math.Round(20 * index / 24.0));
                    var normalized = NativePointerCapture.Normalize(physical, provider.Image.Canvas);
                    var sent = provider.Sends[index][0];
                    Check(sent.X == normalized.X && sent.Y == normalized.Y && Same(operation.Indicators[index], physical),
                          "Drag Move and indicator must use the original frozen absolute path, never chase the window");
                }
            }
        }
    }

    static void EntireTranslatedTrajectoryMustRemainOnCapturedDisplays()
    {
        var original = Image();
        var image = new NativePointerCapture(original.Binding, Box(500, 0, 200, 100, 200), Box(0, 0, 1000, 500),
            "layout", new[] { Box(0, 0, 300, 500), Box(500, 0, 500, 500) }, Box(500, 0, 200, 100));
        var provider = new Provider(image);
        var operation = new Operation(provider, "drag");
        operation.Write.Intent["parameters"] = Wire.Obj("fromX", 0, "fromY", 20, "toX", 180, "toY", 20);
        operation.Inspect();
        provider.Current = new NativeCapturedWindowFrame(250, 0, 200, 100);
        var result = operation.Execute();
        Check(result["errorCode"] != null && !operation.Write.Dispatched && provider.Sends.Count == 0,
              "A translated path entering a monitor gap must be rejected before any move or button event");

        var cold = new Provider(image) { Foreground = false };
        var pending = new Operation(cold, "drag");
        pending.Write.Intent["parameters"] = Wire.Obj("fromX", 0, "fromY", 20, "toX", 180, "toY", 20);
        pending.Inspect();
        cold.Current = new NativeCapturedWindowFrame(250, 0, 200, 100);
        Check(pending.Prepare()["errorCode"] != null && cold.Activations == 0 && cold.Sends.Count == 0,
              "The complete current projection must be checked before foreground preparation, not only at freeze");
    }

    static void LocalImageAndDesktopScreenBoundsRemainStrict()
    {
        var provider = new Provider(Image());
        var invalid = new Operation(provider);
        invalid.Write.Intent["parameters"] = Wire.Obj("x", provider.Image.Image.Width, "y", 20);
        bool rejected = false;
        try { invalid.Inspect(); }
        catch (Failure error) { rejected = error.Code == "invalid_request"; }
        Check(rejected && provider.Hits.Count == 0 && provider.Sends.Count == 0,
              "Window translation never permits coordinates outside the original local PNG");

        var desktop = Image(true);
        var canvas = Box(0, 0, 1000, 500, 1000);
        var gap = new NativePointerCapture(desktop.Binding, canvas, canvas, "layout",
            new[] { Box(0, 0, 300, 500), Box(500, 0, 500, 500) });
        var desktopProvider = new Provider(gap) { Current = new NativeCapturedWindowFrame(-300, 0, 1000, 500) };
        var desktopOperation = new Operation(desktopProvider);
        desktopOperation.Write.Intent["parameters"] = Wire.Obj("x", 328, "y", 20);
        rejected = false;
        try { desktopOperation.Inspect(); }
        catch (Failure error) { rejected = error.Code == "invalid_request"; }
        Check(rejected && desktopProvider.Sends.Count == 0,
              "Desktop pixels in a monitor gap must still fail rather than follow a window into a display");
    }

    static void ProvidersWithoutProjectionKeepTheirFixedFrame()
    {
        foreach (bool move in new[] { false, true })
        {
            var provider = new Provider(Image());
            var operation = new Operation(provider, transport: new Legacy(provider));
            operation.Inspect();
            if (move) provider.Current = new NativeCapturedWindowFrame(300, 100, 400, 300);
            var result = operation.Execute();
            Check(move ? result["errorCode"] != null && provider.Sends.Count == 0
                       : result["errorCode"] == null && provider.Sends.Count == 1,
                  "The optional projection port must not silently change a legacy provider's fixed-frame contract");
        }
    }

    static void Main()
    {
        HiddenCompetingForegroundRequiresPreparation();
        OriginallyOffscreenImagePointsCanTranslateOntoDisplays();
        TranslationPreservesInsetAndScaling();
        PreparationMovesBeforeTheSingleFreeze();
        ChangedFrameAfterFreezeCannotBeFollowed();
        DimensionsIdentityLayoutAndModalRemainMandatory();
        FirstClickProgressCannotRebaseTheSecondClick();
        PartialSendKeepsFrozenPointAndIndependentRelease();
        OriginalImageCanStartIndependentOperationsWithoutAccumulatingDelta();
        DesktopAndCrossDisplayPhysicalCoordinates();
        FrozenDragUsesItsAbsolutePathUnderTheOriginalGrab();
        EntireTranslatedTrajectoryMustRemainOnCapturedDisplays();
        LocalImageAndDesktopScreenBoundsRemainStrict();
        ProvidersWithoutProjectionKeepTheirFixedFrame();
        Console.WriteLine("NativeCapturedPointerTests: passed (capture translation, frozen trajectory, native guards)");
    }
}
}

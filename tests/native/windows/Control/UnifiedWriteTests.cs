// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Threading;

namespace ComputerUse.Windows
{
internal static class UnifiedWriteTests
{
    sealed class Provider : INativeKeyboardProvider, INativeKeyboardEditorPreparation
    {
        internal NativeKeyboardState State =
            new NativeKeyboardState { Enabled = true, Focusable = true, WindowRoute = true };
        internal readonly List<string> Effects = new List<string>();
        internal bool EditorWorks = true;
        public NativeKeyboardState Read(Dictionary<string, object> target, string[] within, Request request,
                                        bool text = false)
        {
            return State;
        }
        public void RequireIdle(NativeKeyEvent[] events, Request request)
        {
            request.Check();
        }
        public void Activate(NativeKeyboardState state, Request request)
        {
            Effects.Add("activate");
            State.Foreground = true;
        }
        public void Focus(NativeKeyboardState state, Request request)
        {
            Effects.Add("focus");
            State.Focused = true;
        }
        public int Send(NativeKeyEvent[] events)
        {
            throw new Exception("Preparation must never send requested keys");
        }
        public void PrepareEditor(NativeKeyboardState state, NativeWriteOperation operation, Request request,
                                  List<string> preparation)
        {
            Effects.Add("editor");
            State.Focused = EditorWorks;
            State.TextSelection = EditorWorks;
        }
    }

    sealed class ExpansionObjects : INativeObjectProvider, INativeExpansionObjectProvider
    {
        internal bool Collapsed;
        public IDisposable Acquire(Request request)
        {
            throw new Exception("No lease during inspection");
        }
        public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request request)
        {
            return new NativeObjectState { Role = "ControlType.ComboBox", Enabled = true, ActionsComplete = true,
                                           Actions = new[] { "ExpandCollapse.Expand", "Invoke.Invoke" } };
        }
        public NativeObjectState ReadExpansion(Dictionary<string, object> target, string[] within, Request request)
        {
            return new NativeObjectState { Role = "ControlType.ComboBox", Enabled = true, ActionsComplete = true,
                                           CollapsedExpansion = Collapsed,
                                           Actions = Collapsed ? new[] { "ExpandCollapse.Expand" }
                                                               : new[] { "Invoke.Invoke" } };
        }
        public Action Prepare(NativeObjectState state, string unit, string action, string text, Request request)
        {
            throw new Exception("No preparation during inspection");
        }
    }

    sealed class OccludedPointer : INativePointerProvider, INativeElementPointerProvider
    {
        public NativePointerCapture Capture(Dictionary<string, object> target, Request request)
        {
            throw new Exception("Not an image route");
        }
        public NativePointerState Read(Dictionary<string, object> target, NativePointerPoint point,
                                       NativePointerGestureIntent gesture, bool dragging, Request request)
        {
            throw new Exception("Not an image route");
        }
        public NativeElementPointerTarget Element(Dictionary<string, object> target, string[] within, Request request)
        {
            return new NativeElementPointerTarget { Point = new NativePointerPoint(30, 40) };
        }
        public NativePointerState ReadElement(Dictionary<string, object> target, string[] within,
                                              NativeElementPointerTarget point, NativePointerGestureIntent gesture,
                                              Request request)
        {
            return new NativePointerState { Occlusion = new NativeClickOcclusion(
                                                new IntPtr(1), new NativeClickTargetCondition(r => r.Check())) };
        }
        public void RequireIdle(Request request)
        {
            throw new Exception("No preparation during inspection");
        }
        public void Activate(NativePointerState state, Request request)
        {
            throw new Exception("No activation during inspection");
        }
        public int Send(NativePointerEvent[] events)
        {
            throw new Exception("No input during inspection");
        }
    }

    static void CheckExpansionProfile(bool collapsed, string expected)
    {
        var objects = new ExpansionObjects { Collapsed = collapsed };
        var engine = new NativeExecution(objects, pointer: new NativePointer(new OccludedPointer()));
        var target = Wire.Obj("session", "session", "instance", "instance", "rootWindow", "window", "surface",
                              "surface", "node", "combo", "capture", null);
        var intent = Wire.Obj("kind", "click", "parameters", Wire.Obj());
        var operation = new NativeWriteOperation(target, intent, true);
        var facts =
            Wire.Object(engine.Inspect(operation, target, new string[0], new Request(CancellationToken.None, 2000)));
        int pointers = 0;
        foreach (var raw in Wire.Array(facts["primitives"]))
        {
            var primitive = Wire.Object(raw);
            if ((string)primitive["unit"] != "pointer_sequence")
                continue;
            var click = Wire.Object(primitive["click"]);
            Check((string)click["preferenceProfile"] == expected,
                  "Changed expansion evidence must emit the current physical requirement profile");
            pointers++;
        }
        Check(pointers == 1, "The inspected original pointer offer must remain present");
    }

    static void Check(bool condition, string detail)
    {
        if (!condition)
            throw new Exception(detail);
    }

    static bool Prepare(Provider provider, bool foreground, string kind, bool replacement = false)
    {
        var target = Wire.Obj("node", "original-field");
        var contract = new NativeInputContract { Target = target, Replacement = replacement };
        var operation = new NativeWriteOperation(target, Wire.Obj("kind", kind, "parameters", Wire.Obj()));
        var request = new Request(CancellationToken.None, 2000);
        var pipeline = new NativePreparePipeline(provider, (c, r, p) => provider.State);
        return pipeline.Run(contract, foreground, request, new List<string>(), "text", operation);
    }

    static void Main()
    {
        var ready = new Provider();
        ready.State.Foreground = ready.State.Focused = true;
        Check(Prepare(ready, false, "insert") && ready.Effects.Count == 0,
              "Ready receiver must not acquire foreground or click");

        var background = new Provider();
        Check(!Prepare(background, false, "insert") && background.Effects.Count == 0,
              "Unselected foreground preparation must not activate");

        var foreground = new Provider();
        Check(Prepare(foreground, true, "insert"), "Selected foreground preparation must establish receiver");
        Check(String.Join(",", foreground.Effects) == "activate,focus",
              "Foreground preparation must activate then focus the original receiver");

        var insert = new Provider();
        insert.State.Foreground = insert.State.SelectionPreparable = true;
        insert.State.Focusable = false;
        Check(!Prepare(insert, false, "insert") && insert.Effects.Count == 0,
              "Insertion must not click to relocate an unknown caret");

        var replace = new Provider();
        replace.State.Foreground = replace.State.SelectionPreparable = true;
        replace.State.Focusable = false;
        Check(Prepare(replace, false, "replace", true), "Replacement can prepare the addressed editor");
        Check(String.Join(",", replace.Effects) == "editor",
              "Replacement must not activate an already foreground window");

        var unresolved = new Provider();
        unresolved.State.Foreground = unresolved.State.SelectionPreparable = true;
        unresolved.State.Focusable = false;
        unresolved.EditorWorks = false;
        try
        {
            Prepare(unresolved, false, "replace", true);
            throw new Exception("Unconfirmed editor preparation must report uncertain effect");
        }
        catch (Failure error)
        {
            Check(error.Code == "execution_unknown", "Uncertain editor click must not become undispatched");
            Check(String.Join(",", unresolved.Effects) == "editor", "Uncertain editor click must not replay");
        }

        Check(NativeKeys.Chord("super").Length == 2, "Complete input route includes system keys");
        Check(NativeKeys.Chord("alt+Tab").Length == 4, "Complete input route includes system shortcuts");
        var click = new NativePointerGestureIntent("click", 2, 1);
        Check(NativePointerClickAdmission.NaturalActivation(click, true, true, true),
              "A reachable single requested click may naturally activate its own window");
        Check(!NativePointerClickAdmission.NaturalActivation(click, true, true, false),
              "Held or captured input must prevent natural activation");
        Check(!NativePointerClickAdmission.NaturalActivation(new NativePointerGestureIntent("click", 2, 2), true, true,
                                                             true),
              "Double click must not use single-click activation admission");
        Check(!NativePointerClickAdmission.NaturalActivation(click, true, false, true),
              "Another native hit must not authorize activation");
        var dispatched = new Request(CancellationToken.None, 1000) { Dispatched = true };
        Check(!NativeForegroundRecovery.Try(dispatched, null),
              "Foreground recovery must stop before querying or sending after requested input");
        var alreadyPrepared = new Request(CancellationToken.None, 1000) { ForegroundRecoveryAttempted = true };
        Check(!NativeForegroundRecovery.Try(alreadyPrepared, null),
              "Foreground recovery must not reset its per-request attempt history");
        CheckExpansionProfile(true, "default");
        CheckExpansionProfile(false, "physicalRequired");
        Console.WriteLine(
            "UnifiedWriteTests: passed (background-first preparation, input semantics, no replay, profile contract)");
    }
}
}

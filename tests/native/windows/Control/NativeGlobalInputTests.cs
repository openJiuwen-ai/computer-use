// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Threading;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal static class NativeGlobalInputTests
    {
        static void Denied(Action call, string boundary)
        {
            try
            {
                call();
                throw new Exception(boundary + " admitted unauthorized global input");
            }
            catch (Failure error)
            {
                if (error.Code != "permission_required")
                    throw new Exception(boundary + " failed for the wrong reason: " + error.Code);
            }
        }

        sealed class Keyboard : INativeKeyboardProvider, INativeGlobalKeyboardAuthority
        {
            internal bool Allowed;
            internal int Activations, FocusChanges, Keys;
            readonly NativeKeyboardState state = new NativeKeyboardState {
                Enabled = true, Focusable = true, WindowRoute = true
            };
            public NativeKeyboardState Read(Dictionary<string, object> target, string[] within, Request r, bool text = false)
            { r.Check(); return state; }
            public void RequireGlobalInput() { NativeGlobalInput.Require(Allowed); }
            public void RequireIdle(NativeKeyEvent[] events, Request r)
            { r.Check(); if (events.Length != 0) RequireGlobalInput(); }
            public void Activate(NativeKeyboardState current, Request r)
            { Activations++; state.Foreground = true; }
            public void Focus(NativeKeyboardState current, Request r)
            { FocusChanges++; state.Focused = true; }
            public int Send(NativeKeyEvent[] events)
            { RequireGlobalInput(); Keys += events.Length; return events.Length; }
        }

        static void CheckPreparation(bool grant, string kind)
        {
            var provider = new Keyboard { Allowed = grant };
            var keyboard = new NativeKeyboard(provider);
            var target = Wire.Obj("node", "original", "surface", "window");
            var intent = Wire.Obj("kind", kind, "parameters", kind == "key" ? Wire.Obj("key", "Return") : Wire.Obj());
            var operation = new NativeWriteOperation(target, intent);
            var request = new Request(CancellationToken.None, 2000, grant);
            var facts = Wire.Object(keyboard.Inspect(operation, target, new string[0], request));
            var primitive = Wire.Object(Wire.Array(facts["primitives"])[0]);
            var command = Wire.Obj("kind", "prepare_receiver", "primitiveId", primitive["id"], "target", target,
                                   "foreground", true);
            var receipt = Wire.Object(keyboard.Execute(operation, command, request));
            if (kind == "key" && !grant)
            {
                if (!Equals(receipt["errorCode"], "permission_required") || provider.Activations != 0 ||
                    provider.FocusChanges != 0 || provider.Keys != 0 || request.Dispatched || request.Effect)
                    throw new Exception("Known global key route must deny before preparation or effects");
                return;
            }
            if (receipt["errorCode"] != null || provider.Activations != 1 || provider.FocusChanges != 1)
                throw new Exception("Authorized key preparation and ordinary focus must remain usable");
            if (kind == "key")
            {
                command["kind"] = "key_sequence";
                command["foreground"] = false;
                receipt = Wire.Object(keyboard.Execute(operation, command, request));
                if (receipt["errorCode"] != null || provider.Keys != 2 || !operation.Dispatched)
                    throw new Exception("An explicitly granted chord must dispatch once through its existing route");
            }
        }

        sealed class PrimitiveKeyboard : INativeKeyboardProvider, INativeKeyboardTextProvider, INativeKeyboardSequence
        {
            internal int? Accepted;
            internal Dictionary<string, object> Bound;
            internal bool Writable = true, ReadOnlyAfterFirstBatch;
            internal Action AfterFirstBatch;
            internal readonly List<NativeKeyEvent[]> Batches = new List<NativeKeyEvent[]>();
            public NativeKeyboardState Read(Dictionary<string, object> target, string[] within, Request r,
                                            bool text = false)
            {
                r.Check();
                bool original = Bound == null ? target.Count == 2 && Equals(target["global"], true)
                                               : NativeWriteOperation.Same(Bound, target);
                if (!original || within.Length != 0)
                    throw new Exception("Keyboard inspection changed its original target");
                return new NativeKeyboardState { Enabled = true, Foreground = true, WindowRoute = true, Focused = true,
                    Role = Bound == null ? null : "ControlType.Text",
                    TextWritable = Writable && !(ReadOnlyAfterFirstBatch && Batches.Count > 0) };
            }
            public void RequireIdle(NativeKeyEvent[] events, Request r) { r.Check(); }
            public void Activate(NativeKeyboardState current, Request r) { throw new Exception("Unexpected activation"); }
            public void Focus(NativeKeyboardState current, Request r) { throw new Exception("Unexpected focus change"); }
            public int Send(NativeKeyEvent[] events)
            {
                Batches.Add(events);
                if (Batches.Count == 1 && AfterFirstBatch != null)
                    AfterFirstBatch();
                return Batches.Count == 1 && Accepted.HasValue ? Accepted.Value : events.Length;
            }
            public INativeKeyboardSequence BeginSequence(NativeKeyboardState state, Request r) { return this; }
            public void Validate(Request r) { r.Check(); }
            public NativeTextSelection Selection(NativeKeyboardState state, Request r)
            { throw new Exception("Insertion must keep the existing selection"); }
            public void SelectAll(NativeKeyboardState state, Request r)
            { throw new Exception("Insertion must not select all"); }
        }

        sealed class Objects : INativeObjectProvider, IDisposable
        {
            internal int Acquired, Released;
            public IDisposable Acquire(Request r) { Acquired++; return this; }
            public void Dispose() { Released++; }
            public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request r)
            { throw new Exception("Unexpected object read"); }
            public Action Prepare(NativeObjectState state, string unit, string action, string text, Request r)
            { throw new Exception("Unexpected object preparation"); }
        }

        sealed class Reader : INativeReadBackend
        {
            public void Validate(Dictionary<string, object> target, Request r)
            { throw new Exception("Unexpected read scope"); }
            public object Inspect(Dictionary<string, object> target, Dictionary<string, object> query, Request r)
            { throw new Exception("Unexpected read query"); }
            public void Release() { throw new Exception("Unexpected read cleanup"); }
        }

        static void CheckPrimitive(string kind, string value, int? accepted, string expected)
        {
            var objects = new Objects();
            var provider = new PrimitiveKeyboard { Accepted = accepted };
            var keyboard = new NativeKeyboard(provider, new NativeWindowsClipboard());
            var runtime = new NativeRuntime("session", new Reader(), new NativeExecution(objects, desktopKeyboard: keyboard));
            var request = new Request(CancellationToken.None, 2000, true);
            var target = Wire.Obj("session", "session", "global", true);
            var intent = Wire.Obj("kind", kind, "globalInput", true,
                                  "parameters", Wire.Obj(kind == "key" ? "key" : "text", value));
            var admission = Wire.Obj("mode", "write", "target", target, "intent", intent);
            Denied(() => runtime.Perform("begin", admission, new Request(CancellationToken.None, 1000)),
                   "raw session admission");
            if (objects.Acquired != 0 || provider.Batches.Count != 0)
                throw new Exception("Denied raw input must not acquire the desktop or dispatch");
            var operation = Wire.Object(runtime.Perform("begin", admission, request))["operation"];
            if (!runtime.Capabilities.Contains("nativeGlobalInput"))
                throw new Exception("Driver did not advertise the admitted global keyboard primitives");
            var command = Wire.Obj("kind", "global_input", "primitiveId", "global", "target", target,
                                   "foreground", false);
            var dispatch = Wire.Obj("operation", operation, "step", 1, "command", command);
            var result = Wire.Object(runtime.Perform("execute", dispatch, request));
            int batches = provider.Batches.Count;
            if (!Equals(result["dispatch"], expected) || !Equals(result["cleanup"], "complete") ||
                !NativeWriteOperation.Same(Wire.Object(result["target"]), target))
                throw new Exception("Unexpected global dispatch receipt: " + Wire.Json(result));
            if (!Object.ReferenceEquals(result, runtime.Perform("execute", dispatch, request)) ||
                provider.Batches.Count != batches)
                throw new Exception("A retransmitted native step must not resend input");
            dispatch["step"] = 2;
            try
            {
                runtime.Perform("execute", dispatch, request);
                throw new Exception("A global operation admitted another primitive");
            }
            catch (Failure error)
            {
                if (error.Code != "invalid_request" || provider.Batches.Count != batches)
                    throw;
            }
            var cleanup = Wire.Object(runtime.Perform("finish", Wire.Obj("operation", operation), request));
            if (!Equals(cleanup["cleanup"], "complete") || objects.Acquired != 1 || objects.Released != 1)
                throw new Exception("Raw input must release its one shared desktop lease");
            if (kind == "key")
            {
                // ctrl-down, Return-down, Return-up have been accepted. Only
                // the accepted Ctrl-down still needs a matching release.
                if (accepted == 3 && (batches != 2 || provider.Batches[1].Length != 1 ||
                                      provider.Batches[1][0].Code != 17 || !provider.Batches[1][0].Up))
                    throw new Exception("Partial key cleanup released keys outside its outstanding prefix");
                if (accepted == 0 && (batches != 1 || request.Dispatched || request.Effect))
                    throw new Exception("A rejected batch must have no release or input effect");
            }
            else if (batches != 2 || provider.Batches[1].Length != 4 ||
                     provider.Batches.SelectMany(batch => batch).Any(e => !e.Unicode) ||
                     new String(provider.Batches.SelectMany(batch => batch)
                                    .Where(e => !e.Up).Select(e => (char)e.Code).ToArray()) != value)
                throw new Exception("Unicode input must send every UTF-16 unit once, keeping surrogate pairs together");
        }

        static void CheckExactTextStream(bool writable, bool readOnlyAfterFirstBatch, string expected, int batches)
        {
            var target = Wire.Obj("session", "session", "instance", "app", "rootWindow", "window",
                                  "surface", "surface", "node", "text", "capture", null);
            var provider = new PrimitiveKeyboard { Bound = target, ReadOnlyAfterFirstBatch = readOnlyAfterFirstBatch };
            var keyboard = new NativeKeyboard(provider);
            var operation = new NativeWriteOperation(
                target, Wire.Obj("kind", "insert", "parameters", Wire.Obj("text", "AB")));
            var request = new Request(CancellationToken.None, 2000, true);
            var facts = Wire.Object(keyboard.Inspect(operation, target, new string[0], request));
            var offered = Wire.Object(Wire.Array(facts["primitives"])[0]);
            var input = Wire.Object(facts["input"]);
            if (!Equals(offered["unit"], "key_sequence") || !Equals(offered["supported"], "yes") ||
                !Equals(offered["readiness"], "yes") || !Equals(input["focusedNode"], "text") ||
                !Equals(input["keyboardSurface"], "surface"))
                throw new Exception("A ready original text stream must offer its exact addressed keyboard route");
            provider.Writable = writable;
            var result = Wire.Object(keyboard.Execute(
                operation, Wire.Obj("kind", "key_sequence", "primitiveId", offered["id"], "target", target,
                                    "foreground", false), request));
            if (!Equals(result["dispatch"], expected) || provider.Batches.Count != batches ||
                (expected == "dispatched") != (result["errorCode"] == null))
                throw new Exception("Writable permission changed without preserving native text progress: " +
                                    Wire.Json(result));
        }

        sealed class Clipboard : INativeClipboardProvider, INativeClipboardPublication
        {
            internal string Content = "prior";
            internal int Releases;
            internal Action<Request> BeforeChord;
            public bool Changed { get; private set; }
            public INativeClipboardPublication Create() { return this; }
            public void Publish(string text, Request request)
            {
                request.Check();
                Content = text;
                Changed = request.Effect = true;
            }
            public void Validate(Request request)
            {
                if (BeforeChord != null)
                    BeforeChord(request);
                request.Check();
            }
            public void Dispose() { Releases++; }
        }

        static void CheckPasteProgress(string trigger, string dispatch, string code, string content, int batches)
        {
            using (var source = new CancellationTokenSource())
            {
                var provider = new PrimitiveKeyboard();
                var clipboard = new Clipboard();
                var keyboard = new NativeKeyboard(provider, clipboard);
                if (trigger == "overwrite")
                    clipboard.BeforeChord = r =>
                    {
                        clipboard.Content = "external";
                        throw new Failure("input_unavailable", "clipboard changed before the paste chord");
                    };
                else if (trigger == "before")
                    clipboard.BeforeChord = r => source.Cancel();
                else if (trigger == "after")
                    provider.AfterFirstBatch = source.Cancel;
                var target = Wire.Obj("session", "session", "global", true);
                var operation = new NativeWriteOperation(
                    target, Wire.Obj("kind", "paste", "parameters", Wire.Obj("text", "payload")));
                var request = new Request(source.Token, 2000, true);
                var result = Wire.Object(keyboard.Execute(
                    operation, Wire.Obj("kind", "global_input", "primitiveId", "global", "target", target,
                                        "foreground", false), request));
                if (!Equals(result["dispatch"], dispatch) || !Equals(result["errorCode"], code) ||
                    Equals(result["confirmation"], "confirmed") || result["retryReason"] != null ||
                    !Wire.Array(result["preparation"]).Contains("clipboard") ||
                    clipboard.Content != content || clipboard.Releases != 1 || provider.Batches.Count != batches)
                    throw new Exception("Paste did not preserve publication, input and cleanup separately: " +
                                        Wire.Json(result));
            }
        }

        static void Main()
        {
            var request = new Request(CancellationToken.None, 1000);
            if (request.GlobalInputAllowed)
                throw new Exception("Requests must not infer global input authorization");
            Denied(() => NativeGlobalInput.Require(request.GlobalInputAllowed), "session default");
            NativeGlobalInput.Require(new Request(CancellationToken.None, 1000, true).GlobalInputAllowed);
            // No Win32 calls may be reached by these unauthorized branches;
            // running on a non-Windows host is an additional independent oracle.
            Denied(() => Native.SendInput(0, new Native.INPUT[0], 0, false), "last injection boundary");
            Denied(() => Native.Send(new Native.INPUT[0], request), "request dispatch boundary");
            if (request.Dispatched || request.Effect)
                throw new Exception("Authorization denial must not claim an effect");
            Denied(() => new NativeWindowsPointer(null, null).RequireIdle(request), "pointer preparation");
            Denied(() => new NativeDesktopKeyboard().RequireIdle(new NativeKeyEvent[0], request),
                   "desktop keyboard preparation");
            Denied(() => new NativeUiaKeyboard(null).RequireIdle(NativeKeys.Chord("Return"), request),
                   "addressed global keyboard preparation");
            if (NativeForegroundRecovery.Try(request, null) || request.Effect || request.ForegroundRecoveryAttempted)
                throw new Exception("Without authorization, recovery must not open the shell menu before Escape");
            // Permission is a channel guard, not a requirement for pure chord
            // planning or ordinary focus/object/message execution.
            if (NativeKeys.Chord("Return").Length != 2)
                throw new Exception("Permission must not disable pure input planning");
            CheckPreparation(false, "focus");
            CheckPreparation(false, "key");
            CheckPreparation(true, "key");
            CheckPrimitive("key", "ctrl+Return", null, "dispatched");
            CheckPrimitive("key", "ctrl+Return", 0, "notDispatched");
            CheckPrimitive("key", "ctrl+Return", 3, "partial");
            CheckPrimitive("insert", "中\uD83D\uDE00", null, "dispatched");
            CheckExactTextStream(true, false, "dispatched", 2);
            CheckExactTextStream(false, false, "notDispatched", 0);
            CheckExactTextStream(true, true, "partial", 1);
            CheckPasteProgress("none", "dispatched", null, "payload", 1);
            CheckPasteProgress("overwrite", "notDispatched", "input_unavailable", "external", 0);
            CheckPasteProgress("before", "notDispatched", "cancelled", "payload", 0);
            CheckPasteProgress("after", "dispatched", "cancelled", "payload", 1);
            Console.WriteLine("NativeGlobalInputTests: passed (scope, authority, raw dispatch, dedup, prefix cleanup, Unicode, addressed text, paste progress)");
        }
    }
}

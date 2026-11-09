// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Threading;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    internal static class NativeMultilineReplacementTests
    {
        sealed class Provider : INativeObjectProvider, INativeValueReplacementObjectProvider, INativeSelectionReplacementSequence
        {
            internal int SetterCalls, SelectionCalls, ReplacementCalls;
            internal string Value = "original";
            internal bool ValuePatternAvailable = true;
            internal bool SelectionTimeout, ReplacementTimeout, ScopeLostAfterSelection, SelectionLostAfterPreparation;
            internal Action AfterSelection, AfterReplacement;
            internal int Start = 1, End = 1;
            internal int SelectionTimeoutMs, ReplacementTimeoutMs;

            public IDisposable Acquire(Request request) { throw new NotSupportedException(); }
            public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request request)
            { throw new NotSupportedException(); }

            public NativeObjectState ReadValueReplacement(Dictionary<string, object> target, string[] within, Request request)
            {
                request.Check();
                Check((string)target["node"] == "edit" && within.Length == 1 && within[0] == "owner",
                      "Replacement must retain its original element and explicit ancestry");
                return new NativeObjectState { Native = this, Enabled = true, Replaceable = ValuePatternAvailable,
                    SelectionReplacement = new NativeSelectionInsertOffer(this,
                        new NativeSelectionInsertState { Role = "ControlType.Edit" }) };
            }

            public Action Prepare(NativeObjectState state, string unit, string action, string text, Request request)
            {
                return () => { SetterCalls++; Value = text; };
            }

            public NativeSelectionInsertState Read(Request request)
            {
                request.Check();
                return new NativeSelectionInsertState { Role = "ControlType.Edit", Enabled = true, Writable = true,
                    Eligible = true, Ready = true, TextLength = Value.Length,
                    Selection = new NativeSelectionInsertSelection { Start = Start, End = End } };
            }

            public void ValidateSelectionTarget(Request request) { request.Check(); }
            public NativeSelectionInsertSendResult SelectAll(int timeoutMs)
            {
                SelectionCalls++;
                SelectionTimeoutMs = timeoutMs;
                Start = 0;
                End = SelectionLostAfterPreparation ? 1 : Value.Length;
                if (AfterSelection != null)
                    AfterSelection();
                return new NativeSelectionInsertSendResult(!SelectionTimeout);
            }
            public void ValidateFinal(NativeSelectionInsertState state, Request request)
            {
                request.Check();
                if (ScopeLostAfterSelection && SelectionCalls != 0)
                    throw new Failure("stale_target", "original HWND lifetime or explicit ancestry no longer matches");
                Check(state.Selection.Start == 0 && state.Selection.End == Value.Length, "Replacement requires full selection");
            }

            sealed class Buffer : IDisposable
            {
                internal readonly string Text;
                internal Buffer(string text) { Text = text; }
                public void Dispose() { }
            }
            public IDisposable CreateBuffer(string text) { return new Buffer(text); }
            public NativeSelectionInsertSendResult Send(IDisposable buffer, int timeoutMs)
            {
                ReplacementCalls++;
                ReplacementTimeoutMs = timeoutMs;
                Value = ((Buffer)buffer).Text;
                if (AfterReplacement != null)
                    AfterReplacement();
                return new NativeSelectionInsertSendResult(!ReplacementTimeout);
            }
        }

        static void Check(bool condition, string detail)
        {
            if (!condition)
                throw new Exception(detail);
        }

        static Dictionary<string, object> Replace(Provider provider, string text, Request request,
                                                   out NativeWriteOperation operation, NativeExecution engine = null)
        {
            var target = Wire.Obj("session", "session", "instance", "instance", "rootWindow", "root",
                                  "surface", "surface", "node", "edit", "capture", null);
            engine = engine ?? new NativeExecution(provider);
            operation = new NativeWriteOperation(target, Wire.Obj("kind", "replace", "parameters", Wire.Obj("text", text)));
            engine.Validate(operation.Intent, target);
            var facts = Wire.Object(engine.Inspect(operation, target, new[] { "owner" }, request));
            var primitive = Wire.Object(Wire.Array(facts["primitives"])[0]);
            Check(Equals(primitive["globalInput"], "no"), "An exact background edit must not require global input");
            Check(Equals(primitive["supported"], "yes") && Equals(primitive["readiness"], "yes"),
                  "The independently qualified multiline edit must be ready even without the ValuePattern setter");
            return Wire.Object(engine.Execute(operation,
                Wire.Obj("kind", "native_text_edit", "primitiveId", primitive["id"], "target", target, "foreground", false),
                request));
        }

        static Request RequestForTest() { return new Request(CancellationToken.None, 3000); }

        static void RepeatAfterValuePatternChanges()
        {
            Check(NativeSelectionInsertMechanism.IsReplacementRole(ControlType.Edit) &&
                  NativeSelectionInsertMechanism.IsReplacementRole(ControlType.Document) &&
                  !NativeSelectionInsertMechanism.IsReplacementRole(ControlType.Text) &&
                  !NativeSelectionInsertMechanism.IsReplacementRole(null),
                  "Only Edit and Document are eligible UIA presentations of an independently verified native Edit");
            var provider = new Provider();
            var engine = new NativeExecution(provider);
            NativeWriteOperation operation;
            Replace(provider, "first", RequestForTest(), out operation, engine);
            provider.ValuePatternAvailable = false;
            var result = Replace(provider, "second", RequestForTest(), out operation, engine);
            Check(Equals(result["dispatch"], "dispatched") && provider.Value == "second" &&
                  provider.SelectionCalls == 2 && provider.ReplacementCalls == 2 && provider.SetterCalls == 0,
                  "The same native editor must support a second addressed replacement without its former setter");
        }

        static void SuccessAndEmpty()
        {
            NativeWriteOperation operation;
            var provider = new Provider();
            var request = RequestForTest();
            var result = Replace(provider, "new \uD83D\uDE42", request, out operation);
            Check(provider.SetterCalls == 0 && provider.SelectionCalls == 1 && provider.ReplacementCalls == 1,
                  "Multiline native Edit replacement must enter one selection edit and never invoke the property setter");
            Check(provider.Value == "new \uD83D\uDE42" && Equals(result["dispatch"], "dispatched") && request.Dispatched &&
                  Equals(result["confirmation"], "unavailable"), "Replacement payload, dispatch or confirmation was misstated");
            Check(Equals(Wire.Array(result["preparation"])[0], "nativeSelection") && provider.SelectionTimeoutMs > 0 &&
                  provider.SelectionTimeoutMs <= 100 && provider.ReplacementTimeoutMs > 0 && provider.ReplacementTimeoutMs <= 250,
                  "Native preparation and dispatch must retain their individual bounds");

            provider = new Provider { Start = 0, End = "original".Length };
            result = Replace(provider, "", RequestForTest(), out operation);
            Check(provider.Value == "" && provider.SetterCalls == 0 && provider.SelectionCalls == 0 &&
                  provider.ReplacementCalls == 1 && Equals(result["dispatch"], "dispatched") &&
                  Wire.Array(result["preparation"]).Length == 0,
                  "An existing full selection needs no preparation; empty replacement must delete its content");
        }

        static void CheckPreparationFailure(Provider provider, string code, Request request)
        {
            NativeWriteOperation operation;
            var result = Replace(provider, "new", request, out operation);
            Check(Equals(result["dispatch"], "notDispatched") && Equals(result["errorCode"], code) &&
                  result["retryReason"] == null && Equals(Wire.Array(result["preparation"])[0], "nativeSelection") &&
                  ((string)result["detail"]).Contains("Replacement text was not sent") &&
                  operation.InputTerminated && request.Effect && !request.Dispatched &&
                  provider.Value == "original" && provider.SetterCalls == 0 && provider.SelectionCalls == 1 &&
                  provider.ReplacementCalls == 0, "Incomplete preparation must retain possible selection effects without replay");
        }

        static void FailuresAndCancellation()
        {
            CheckPreparationFailure(new Provider { SelectionTimeout = true }, "execution_unknown", RequestForTest());
            CheckPreparationFailure(new Provider { ScopeLostAfterSelection = true }, "stale_target", RequestForTest());
            CheckPreparationFailure(new Provider { SelectionLostAfterPreparation = true }, "input_unavailable", RequestForTest());
            using (var cancellation = new CancellationTokenSource())
            {
                CheckPreparationFailure(new Provider { AfterSelection = cancellation.Cancel }, "cancelled",
                                        new Request(cancellation.Token, 3000));
            }

            NativeWriteOperation operation;
            var provider = new Provider { ReplacementTimeout = true };
            var request = RequestForTest();
            var result = Replace(provider, "new", request, out operation);
            Check(Equals(result["dispatch"], "unknown") && Equals(result["errorCode"], "execution_unknown") &&
                  result["retryReason"] == null && operation.InputTerminated && request.Dispatched &&
                  provider.Value == "new" && provider.SetterCalls == 0 && provider.ReplacementCalls == 1,
                  "An uncertain edit may have executed once and cannot be retried");
            using (var cancellation = new CancellationTokenSource())
            {
                provider = new Provider { AfterReplacement = cancellation.Cancel };
                result = Replace(provider, "new", new Request(cancellation.Token, 3000), out operation);
                Check(Equals(result["dispatch"], "dispatched") && Equals(result["errorCode"], "cancelled") &&
                      result["retryReason"] == null && operation.InputTerminated && provider.ReplacementCalls == 1,
                      "Cancellation after the returned native edit must preserve its dispatch fact");
            }
        }

        static void InvalidPayloadAndMechanism()
        {
            foreach (var invalid in new[] { "first\nsecond", "text\0hidden", "\uD800", new string('x', 1024 * 1024 + 1) })
            {
                NativeWriteOperation operation;
                var provider = new Provider();
                try
                {
                    Replace(provider, invalid, RequestForTest(), out operation);
                    throw new Exception("Invalid replacement payload accepted");
                }
                catch (Failure error)
                {
                    Check(error.Code == "invalid_request" && provider.SelectionCalls == 0 &&
                          provider.ReplacementCalls == 0 && provider.SetterCalls == 0,
                          "Invalid replacement must fail before selection or dispatch");
                }
            }
            const string winForms = "WindowsForms10.EDIT.app.0.34f5582_r6_ad1";
            Check(NativeSelectionInsertMechanism.ResolveReplacement("Edit", "Edit", "Win32", true, 4, "Edit") != null &&
                  NativeSelectionInsertMechanism.ResolveReplacement(winForms, winForms, "WinForm", true, 4, "TextBox") != null,
                  "Both proven Unicode classic and WinForms multiline Edit forms must qualify");
            Check(NativeSelectionInsertMechanism.Resolve("Edit", "Edit", "Win32", true, 4, "Edit") == null &&
                  NativeSelectionInsertMechanism.ResolveReplacement("Edit", "Edit", "Win32", true, 0, "Edit") == null &&
                  NativeSelectionInsertMechanism.ResolveReplacement("Edit", "Edit", "Win32", false, 4, "Edit") == null &&
                  NativeSelectionInsertMechanism.ResolveReplacement("Edit", "Other", "Win32", true, 4, "Edit") == null &&
                  NativeSelectionInsertMechanism.ResolveReplacement("RichEdit50W", "RichEdit50W", "Win32", true, 4, "Edit") == null &&
                  NativeSelectionInsertMechanism.ResolveReplacement(winForms, winForms, "WinForm", true, 4, "MaskedTextBox") == null,
                  "Replacement must not admit single-line, ANSI, rich, different real-class or masked controls");
            foreach (int style in new[] { 8, 16, 32, 0x400, 0x800, 0x2000 })
                Check(NativeSelectionInsertMechanism.ResolveReplacement("Edit", "Edit", "Win32", true, 4 | style, "Edit") == null,
                      "Incompatible style must keep its existing route: " + style);
        }

        static int Main()
        {
            try
            {
                SuccessAndEmpty();
                RepeatAfterValuePatternChanges();
                FailuresAndCancellation();
                InvalidPayloadAndMechanism();
                Console.WriteLine("NativeMultilineReplacementTests: passed");
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
                return 1;
            }
        }
    }
}

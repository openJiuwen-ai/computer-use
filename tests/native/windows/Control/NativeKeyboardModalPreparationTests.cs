// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;

namespace ComputerUse.Windows
{
    internal static class NativeKeyboardModalPreparationTests
    {
        sealed class Provider : INativeKeyboardProvider
        {
            readonly string scenario;
            readonly CancellationTokenSource cancellation;
            readonly NativeWindowRecord original = new NativeWindowRecord();
            readonly NativeWindowRecord replacement = new NativeWindowRecord();
            internal int Activations, ReadCount, ReadsAfterActivation, Sends, ModalChecks;
            internal bool BlockAtDispatch;
            bool modalCleared;
            readonly FieldInfo interaction = typeof(NativeKeyboardState).GetField(
                "InteractionWindow", BindingFlags.Instance | BindingFlags.NonPublic);

            internal Provider(string scenario, CancellationTokenSource cancellation)
            {
                this.scenario = scenario;
                this.cancellation = cancellation;
            }

            NativeOwnedModalBlock Block()
            {
                return new NativeOwnedModalBlock(original, (r, confirmed) =>
                {
                    ModalChecks++;
                    if (scenario == "cancelled")
                        cancellation.Cancel();
                    r.Check();
                    if (scenario == "ambiguous" || confirmed && scenario == "replacement")
                        return NativeOwnedModalState.Changed;
                    if (scenario == "already_cleared" || scenario == "already_cleared_with_new_blocker")
                    {
                        modalCleared = true;
                        return NativeOwnedModalState.Cleared;
                    }
                    if (!confirmed || ModalChecks < 3 || scenario == "persistent" || scenario == "deadline")
                        return NativeOwnedModalState.Pending;
                    modalCleared = true;
                    return NativeOwnedModalState.Cleared;
                });
            }

            public NativeKeyboardState Read(Dictionary<string, object> target, string[] within, Request r,
                                            bool text = false)
            {
                r.Check();
                ReadCount++;
                if (BlockAtDispatch)
                    throw Block();
                if (scenario == "preexisting" && ReadCount > 1)
                    throw Block();
                if (Activations != 0)
                {
                    ReadsAfterActivation++;
                    Check(ModalChecks == 0 || modalCleared, "full receiver guard queried the active blocker again");
                    if (!modalCleared && ReadsAfterActivation == (scenario == "during_observation" ? 2 : 1) ||
                        modalCleared && (scenario == "replacement_after_clear" || scenario == "already_cleared_with_new_blocker"))
                        throw Block();
                    if (scenario == "stale")
                        throw new Failure("stale_target", "the original selected node ended");
                }
                bool ready = Activations != 0 || scenario == "already_ready";
                var state = new NativeKeyboardState { Role = "ControlType.Edit", Enabled = true,
                    Focusable = true, Foreground = ready, Focused = ready, WindowRoute = ready, Label = "A editor" };
                if (interaction != null)
                    interaction.SetValue(state, scenario == "rebound" && ready ? replacement : original);
                return state;
            }

            public void RequireIdle(NativeKeyEvent[] events, Request r) { r.Check(); }
            public void Activate(NativeKeyboardState state, Request r)
            {
                r.Check();
                Activations++;
                if (scenario == "inside_activate")
                {
                    var modal = Block();
                    modal.AfterActivation = true;
                    throw modal;
                }
                if (scenario == "before_activation_completed")
                    throw Block();
            }
            public void Focus(NativeKeyboardState state, Request r)
            {
                throw new Exception("modal settlement must never focus a second receiver");
            }
            public int Send(NativeKeyEvent[] events)
            {
                Check(Activations == 1 && ReadsAfterActivation >= 2, "sent before the original receiver returned");
                Check(events.Length == 2 && events[0].Code == 119 && !events[0].Up &&
                      events[1].Code == 119 && events[1].Up, "expected one F8 down/up pair");
                Sends++;
                return events.Length;
            }
        }

        static void Check(bool condition, string detail)
        {
            if (!condition)
                throw new Exception(detail);
        }

        static Dictionary<string, object> Offer(NativeKeyboard keyboard, NativeWriteOperation operation,
                                                 Dictionary<string, object> binding, Request request)
        {
            var facts = Wire.Object(keyboard.Inspect(operation, binding, new string[0], request));
            return Wire.Object(Wire.Array(facts["primitives"]).Single());
        }

        static Dictionary<string, object> Execute(NativeKeyboard keyboard, NativeWriteOperation operation,
                Dictionary<string, object> binding, Dictionary<string, object> offer, Request request, bool prepare,
                bool foreground)
        {
            return Wire.Object(keyboard.Execute(operation, Wire.Obj("kind", prepare ? "prepare_receiver" : "key_sequence",
                "target", binding, "primitiveId", offer["id"], "foreground", foreground), request));
        }

        static void Scenario(string scenario, string error = null, bool prepared = true)
        {
            using (var cancellation = new CancellationTokenSource())
            {
                var provider = new Provider(scenario, cancellation);
                var keyboard = new NativeKeyboard(provider);
                var target = Wire.Obj("session", "session", "instance", "instance", "rootWindow", "A",
                                      "surface", "A_surface", "node", "A_edit", "capture", null);
                var operation = new NativeWriteOperation(target, Wire.Obj("kind", "key", "parameters", Wire.Obj("key", "F8")));
                var request = new Request(cancellation.Token, scenario == "deadline" ? 65 : 2000, true);
                var offered = Offer(keyboard, operation, target, request);
                var result = Execute(keyboard, operation, target, offered, request, true, true);
                Check((string)result["dispatch"] == "notDispatched" && !operation.Dispatched && !request.Dispatched &&
                      provider.Sends == 0, "preparation sent requested input");
                Check(NativeWriteOperation.Same(Wire.Object(result["target"]), target), "original binding changed");
                Check((string)result["cleanup"] == "complete", "preparation cleanup changed");
                var preparation = ((IEnumerable<string>)result["preparation"]).ToArray();
                Check(preparation.SequenceEqual(prepared ? new[] { "foreground" } : new string[0]),
                      "unexpected preparation, focus or repeated foreground");
                Check(provider.Activations == (prepared ? 1 : 0), "foreground was not requested exactly as expected");
                Check((string)result["errorCode"] == error,
                      "expected " + (error ?? "confirmed preparation") + ", got " + result["errorCode"]);
                Check(result["retryReason"] == null, "terminal blocker authorized another route");
                if (error == null)
                {
                    Check((string)result["confirmation"] == "confirmed" && provider.ModalChecks >= 1,
                          "original receiver was not confirmed after an observed blocker");
                    if (scenario == "already_cleared")
                        Check(provider.ModalChecks == 1, "already closed exact blocker was sampled again");
                    var fresh = Offer(keyboard, operation, target, request);
                    Check((string)fresh["readiness"] == "yes" && !Equals(offered["id"], fresh["id"]),
                          "dispatch did not require a fresh offer of the original A");
                    int modalChecks = provider.ModalChecks;
                    provider.BlockAtDispatch = scenario == "late_dispatch_modal";
                    var sent = Execute(keyboard, operation, target, fresh, request, false, false);
                    if (provider.BlockAtDispatch)
                        Check((string)sent["errorCode"] == "blocked_by_modal" && sent["retryReason"] == null &&
                              (string)sent["dispatch"] == "notDispatched" && provider.Sends == 0 &&
                              provider.ModalChecks == modalChecks && !operation.Dispatched && !request.Dispatched,
                              "final dispatch guard did not terminate a fresh blocker before requested input");
                    else
                        Check((string)sent["dispatch"] == "dispatched" && provider.Sends == 1 && operation.Dispatched,
                              "fresh original receiver did not receive exactly one F8 pair");
                }
                else
                {
                    Check((string)result["confirmation"] == "unavailable" && provider.Sends == 0,
                          "failed preparation claimed readiness or sent requested input");
                    if (scenario == "preexisting" || scenario == "before_activation_completed")
                        Check(provider.ModalChecks == 0, "a pre-activation blocker was polled");
                    if (scenario == "ambiguous")
                        Check(provider.ModalChecks <= 1, "an ambiguous blocker was polled");
                    if (scenario == "replacement")
                        Check(provider.ModalChecks <= 2, "a replacement blocker was polled");
                    if (scenario == "replacement_after_clear")
                        Check(provider.ModalChecks <= 3, "an extra modal settle began after the original closed");
                    if (scenario == "already_cleared_with_new_blocker")
                        Check(provider.ModalChecks <= 1, "a new blocker inherited the original modal's wait");
                }
            }
        }

        static int Main()
        {
            int failures = 0;
            foreach (var test in new[] {
                new { Name = "inside_activate", Error = (string)null, Prepared = true },
                new { Name = "after_activate", Error = (string)null, Prepared = true },
                new { Name = "during_observation", Error = (string)null, Prepared = true },
                new { Name = "already_cleared", Error = (string)null, Prepared = true },
                new { Name = "already_cleared_with_new_blocker", Error = "blocked_by_modal", Prepared = true },
                new { Name = "late_dispatch_modal", Error = (string)null, Prepared = true },
                new { Name = "preexisting", Error = "blocked_by_modal", Prepared = false },
                new { Name = "before_activation_completed", Error = "blocked_by_modal", Prepared = true },
                new { Name = "persistent", Error = "blocked_by_modal", Prepared = true },
                new { Name = "ambiguous", Error = "blocked_by_modal", Prepared = true },
                new { Name = "replacement", Error = "blocked_by_modal", Prepared = true },
                new { Name = "replacement_after_clear", Error = "blocked_by_modal", Prepared = true },
                new { Name = "stale", Error = "stale_target", Prepared = true },
                new { Name = "rebound", Error = "stale_target", Prepared = true },
                new { Name = "cancelled", Error = "cancelled", Prepared = true },
                new { Name = "deadline", Error = "deadline_exceeded", Prepared = true }
            })
            {
                try
                {
                    Scenario(test.Name, test.Error, test.Prepared);
                    Console.WriteLine("PASS " + test.Name);
                }
                catch (Exception error)
                {
                    failures++;
                    Console.WriteLine("FAIL " + test.Name + ": " + error.Message);
                }
            }
            return failures == 0 ? 0 : 1;
        }
    }
}

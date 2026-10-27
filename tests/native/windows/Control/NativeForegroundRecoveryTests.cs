using System;
using System.Threading;

namespace ComputerUse.Windows
{
    internal static class NativeForegroundRecoveryTests
    {
        sealed class Probe : NativeForegroundRecovery.Host
        {
            internal double Time = Wire.Now;
            internal bool BeforeVisible, BeforeUnknown, Busy, NoShell, Preexisting, OpenFailed, ForeignAfterOpen, ChangedBeforeOpen, FocusMissing, MenuStuck, Opened, Escaped, Activated, LostAtFinalGuard, NullDuringOpen, ThrowEscape, ReleaseFails;
            internal int Opens, Escapes, Releases, Activations, DeepChecks, LoseTargetAt;
            internal uint Accepted = 2;
            int reads;
            static NativeForegroundRecovery.Surface Surface(int id)
            {
                return new NativeForegroundRecovery.Surface
                {
                    Handle = new IntPtr(id),
                    Pid = (uint)id,
                    Thread = (uint)id,
                    Generation = 1,
                    Started = 123,
                    Session = 1,
                    Image = "fixed-image-" + id
                };
            }

            readonly NativeForegroundRecovery.Surface before = Surface(1), menu = Surface(2), shell = Surface(3), foreign = Surface(4);
            internal Probe(bool unknownForegroundProcess = false)
            {
                if (unknownForegroundProcess)
                {
                    before.Session = UInt32.MaxValue;
                    before.Started = 0;
                    before.Image = null;
                }
            }

            internal override double Now
            {
                get
                {
                    return Time;
                }
            }

            internal override void Guard(bool deep)
            {
                if (deep && ++DeepChecks == LoseTargetAt)
                    throw new Failure("stale_target", "fixture target changed");
            }

            internal override NativeForegroundRecovery.Surface Foreground()
            {
                reads++;
                if (Opened)
                    return NullDuringOpen && Time < OpenTime + 60 ? null : ForeignAfterOpen ? foreign : menu;
                return ChangedBeforeOpen && reads > 1 ? foreign : before;
            }

            internal override int Visibility(NativeForegroundRecovery.Surface value)
            {
                if (value.Handle == before.Handle)
                    return BeforeUnknown ? -1 : BeforeVisible ? 1 : 0;
                return Escaped && !MenuStuck ? 0 : 1;
            }

            internal override bool Idle(NativeForegroundRecovery.Surface value, bool focus)
            {
                return !Busy && !(focus && FocusMissing);
            }

            internal override NativeForegroundRecovery.Surface Shell()
            {
                return NoShell ? null : shell;
            }

            internal override bool TrustedMenu(NativeForegroundRecovery.Surface value)
            {
                return menu.Same(value);
            }

            internal override bool ExistingMenu()
            {
                return Preexisting;
            }

            double OpenTime;
            internal override bool Open(NativeForegroundRecovery.Surface value, int timeout)
            {
                Opens++;
                Opened = true;
                OpenTime = Time;
                return !OpenFailed;
            }

            internal override uint Escape(NativeForegroundRecovery.Surface value)
            {
                Escapes++;
                Escaped = true;
                if (ThrowEscape)
                    throw new InvalidOperationException("Escape return lost");
                return Accepted;
            }

            internal override void ReleaseEscape()
            {
                Releases++;
                if (ReleaseFails)
                    throw new InvalidOperationException("Escape release failed");
            }

            internal override void Activate()
            {
                Activations++;
                Activated = true;
            }

            internal override bool TargetForeground()
            {
                return Activated && !(LostAtFinalGuard && DeepChecks >= 4);
            }

            internal override void Wait()
            {
                Time += 20;
            }
        }

        static int assertions, cases;
        static void Assert(bool value, string detail)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(detail);
        }

        static Request Request()
        {
            return new Request(CancellationToken.None, 4000);
        }

        static void Declined(Probe host, bool exclusive = true, Request request = null)
        {
            cases++;
            var r = request ?? Request();
            int opens = host.Opens, escapes = host.Escapes, activations = host.Activations;
            bool dispatched = r.Dispatched;
            Assert(!NativeForegroundRecovery.Try(exclusive, r, host), "unsafe preparation was not declined");
            Assert(host.Opens == opens && host.Escapes == escapes && host.Activations == activations, "declined preparation acted");
            Assert(r.Dispatched == dispatched, "declined preparation changed original input dispatch");
        }

        static void Failed(Probe host, string code = "input_unavailable", int escapes = 0, int activations = 0, Request request = null)
        {
            cases++;
            var r = request ?? Request();
            try
            {
                NativeForegroundRecovery.Try(true, r, host);
                throw new InvalidOperationException("unsafe preparation succeeded");
            }
            catch (Failure error)
            {
                Assert(error.Code == code, error.Code + ": " + error.Message);
            }

            Assert(host.Escapes == escapes && host.Activations == activations, "unexpected event replay or activation");
            Assert(!r.Dispatched, "preparation events became original content dispatch");
        }

        static void Main()
        {
            var diagnostics = new System.IO.StringWriter();
            Console.SetError(diagnostics);
            Assert(!NativeForegroundRecovery.PreferShell(null), "missing foreground established early recovery");
            var metadata = new NativeForegroundRecovery.Surface
            {
                Session = 1,
                Started = 123,
                Image = "known"
            };
            Assert(!NativeForegroundRecovery.PreferShell(metadata), "queryable foreground skipped normal UIA preparation");
            metadata.Session = UInt32.MaxValue;
            Assert(!NativeForegroundRecovery.PreferShell(metadata), "partial metadata was treated as complete process-query refusal");
            metadata.Started = 0;
            metadata.Image = null;
            Assert(NativeForegroundRecovery.PreferShell(metadata), "measured process-query refusal did not prefer bounded shell preparation");
            var success = new Probe();
            var r = Request();
            cases++;
            Assert(NativeForegroundRecovery.Try(true, r, success), "normal recovery failed");
            Assert(success.Opens == 1 && success.Escapes == 1 && success.Activations == 1 && success.Releases == 0, "recovery replayed");
            Assert(r.Effect && !r.Dispatched && r.ForegroundRecoveryAttempted, "preparation effect/dispatch distinction lost");
            var unknown = new Probe(true);
            cases++;
            Assert(NativeForegroundRecovery.Try(true, Request(), unknown), "unqueryable hidden foreground prevented trusted-shell recovery");
            Assert(unknown.Opens == 1 && unknown.Escapes == 1 && unknown.Activations == 1, "unknown process data changed recovery event semantics");
            var transition = new Probe
            {
                NullDuringOpen = true
            };
            cases++;
            Assert(NativeForegroundRecovery.Try(true, Request(), transition), "transient null foreground was treated as a missing desktop");
            Assert(transition.Escapes == 1 && transition.Activations == 1, "transient foreground caused replay");
            Declined(success, request: r); // At most once per request.
            Declined(new Probe(), exclusive: false); // Shared, including host foreground assistance.
            Declined(new Probe(), request: new Request(CancellationToken.None, 4000) { Dispatched = true });
            Declined(new Probe { BeforeVisible = true });
            Declined(new Probe { BeforeUnknown = true });
            Declined(new Probe { Busy = true });
            Declined(new Probe { NoShell = true });
            Declined(new Probe { Preexisting = true });
            Declined(new Probe { ChangedBeforeOpen = true });
            Failed(new Probe { OpenFailed = true });
            Failed(new Probe { ForeignAfterOpen = true });
            Failed(new Probe { FocusMissing = true });
            Failed(new Probe { LoseTargetAt = 1 }, "stale_target");
            Failed(new Probe { LoseTargetAt = 2 }, "stale_target");
            Failed(new Probe { LoseTargetAt = 3 }, "stale_target", escapes: 1);
            Failed(new Probe { LoseTargetAt = 4 }, "stale_target", escapes: 1, activations: 1);
            var zero = new Probe
            {
                Accepted = 0
            };
            Failed(zero, escapes: 1);
            Assert(zero.Releases == 0, "released a key not accepted");
            var partial = new Probe
            {
                Accepted = 1
            };
            Failed(partial, escapes: 1);
            Assert(partial.Releases == 1 && partial.Opens == 1, "partial preparation did not release its own key exactly once");
            Failed(new Probe { MenuStuck = true }, escapes: 1);
            Failed(new Probe { LostAtFinalGuard = true }, escapes: 1, activations: 1);
            var shortBudget = new Probe
            {
                FocusMissing = true
            };
            var budget = Request();
            budget.ObservationDeadline = shortBudget.Time + 25;
            Failed(shortBudget, request: budget);
            Assert(shortBudget.Time < budget.ObservationDeadline + 20, "recovery reset/exceeded observation budget");
            var cancelled = new CancellationTokenSource();
            cancelled.Cancel();
            Failed(new Probe(), "cancelled", request: new Request(cancelled.Token, 4000));
            Assert(diagnostics.ToString().Contains("\"requestedInputDispatched\":false"), "preparation diagnostics lost dispatch distinction");
            var progress = new NativeClickProgress();
            var clickRequest = Request();
            clickRequest.ClickPreparation = progress;
            cases++;
            Assert(NativeForegroundRecovery.Try(true, clickRequest, new Probe()), "negotiated click recovery failed");
            Assert(progress.Preparation.Count == 2 && progress.Preparation[0].Kind == "shellMenu" && progress.Preparation[1].Kind == "escape", "shell request and actual Escape did not retain separate preparation evidence");
            Assert(progress.Preparation[1].Dispatch == "dispatched" && progress.Preparation[1].Observation == "confirmed" && progress.Preparation[1].Accepted == 2 && progress.Dispatch == "notDispatched" && !progress.ContinuationCompatible, "content-capable preparation was mistaken for requested click or cross-route permission");
            progress = new NativeClickProgress();
            clickRequest = Request();
            clickRequest.ClickPreparation = progress;
            var unknownEscape = new Probe
            {
                ThrowEscape = true,
                ReleaseFails = true
            };
            cases++;
            try
            {
                NativeForegroundRecovery.Try(true, clickRequest, unknownEscape);
                throw new InvalidOperationException("unknown Escape unexpectedly succeeded");
            }
            catch (Exception error)
            {
                Assert(error.Message == "Escape return lost", "cleanup replaced the original preparation failure");
            }

            Assert(unknownEscape.Escapes == 1 && unknownEscape.Releases == 1 && progress.Preparation[1].Dispatch == "unknown" && progress.Preparation[1].Cleanup == "failed" && !progress.ContinuationCompatible, "uncertain Escape lost conservative release/failure evidence");
            Console.WriteLine(Wire.Json(Wire.Obj("assertions", assertions, "cases", cases, "scope", "production recovery state machine and failure boundaries; actual desktop acceptance is separate")));
        }
    }
}

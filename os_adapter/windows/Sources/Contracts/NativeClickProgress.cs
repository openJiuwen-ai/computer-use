using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Single native-operation owner for requested input and preparation. A
    // proven zero result resolves an in-flight call; it does not erase earlier
    // accepted events, an unknown call, or incompatible preparation.
    internal sealed class NativeClickProgress : INativePreparationJournal
    {
        internal bool CallInFlight, CallEntered, CallReturned, ContinuationCompatible = true;
        internal string Dispatch = "notDispatched";
        internal int AttemptedEvents, AcceptedEvents;
        internal bool AcceptanceKnown = true;
        internal string Mechanism;
        internal readonly List<NativePreparationEffect> Preparation = new List<NativePreparationEffect>();
        internal int PreparationStart;
        internal IEnumerable<NativePreparationEffect> AttemptPreparation
        {
            get
            {
                return Preparation.Skip(PreparationStart);
            }
        }

        internal bool HasRequestedInput
        {
            get
            {
                return Dispatch != "notDispatched";
            }
        }

        internal void StartAttempt()
        {
            if (CallInFlight || HasRequestedInput || !ContinuationCompatible)
                throw new Failure("execution_unknown", "the click operation cannot start another attempt");
            // Only a proven-empty attempt can reach here. Transport counters
            // describe this execute; preparation/terminal state remain owned
            // by the original operation and are never reset by a new ID.
            AttemptedEvents = AcceptedEvents = 0;
            CallEntered = CallReturned = false;
            AcceptanceKnown = true;
            Mechanism = null;
            PreparationStart = Preparation.Count;
        }

        internal void BeginCall(Request r, string mechanism, int expected)
        {
            r.Check();
            if (CallInFlight || Dispatch == "unknown")
                throw new Failure("execution_unknown", "the previous native input call is unresolved");
            Mechanism = mechanism;
            AttemptedEvents += expected;
            CallInFlight = CallEntered = true;
            CallReturned = false;
            r.Effect = true;
            r.Dispatched = true;
        }

        internal void Returned(int accepted, int expected, Request r)
        {
            CallReturned = true;
            if (accepted < 0 || accepted > expected)
            {
                Unknown();
                return;
            }

            CallInFlight = false;
            AcceptedEvents += accepted;
            Dispatch = AcceptedEvents == 0 ? "notDispatched" : "partial";
            r.Dispatched = HasRequestedInput;
            // Preparation effects remain observable even after a zero insert.
            r.Effect = HasRequestedInput || Preparation.Count != 0;
        }

        internal void Completed()
        {
            CallInFlight = false;
            CallReturned = true;
            Dispatch = "dispatched";
        }

        internal void Unknown()
        {
            CallInFlight = false;
            AcceptanceKnown = false;
            Dispatch = "unknown";
        }

        public NativePreparationEffect Preparing(string kind)
        {
            var effect = new NativePreparationEffect(kind);
            Preparation.Add(effect);
            return effect;
        }

        public void RequireObservation()
        {
            ContinuationCompatible = false;
        }

        internal Dictionary<string, object> Evidence(Dictionary<string, object> target, string family, object retryReason)
        {
            var preparation = new List<object>();
            foreach (var p in AttemptPreparation)
                preparation.Add(Wire.Obj("kind", p.Kind == "foreground" ? "activation" : p.Kind == "shellMenu" ? "windowSelection" : "keyboardPreparation", "scope", new Dictionary<string, object>(target), "dispatch", p.Dispatch, "effect", p.Kind == "foreground" ? "contextOnly" : p.Observation == "unknown" ? "unknown" : "contentPossible", "continuation", p.Kind == "foreground" && p.Observation != "unknown" ? "withinScope" : p.Observation == "confirmed" ? "sameCandidate" : "observe", "observations", new[] { p.Kind + ":" + p.Observation }, "wait", p.Observation == "confirmed" ? "satisfied" : p.Observation == "unavailable" ? "exhausted" : "unavailable"));
            return Wire.Obj("preparationEvidence", preparation, "transportEvidence", Wire.Obj("kind", Mechanism == null ? "none" : Mechanism == "UIA" ? "nativeAction" : "inserted", "callEntered", CallEntered, "callReturned", CallReturned, "submitted", Mechanism == "SendInput" && AcceptanceKnown ? (object)AcceptedEvents : null, "expected", Mechanism == "SendInput" ? (object)AttemptedEvents : null), "continuation", Wire.Obj("kind", HasRequestedInput ? "stop" : !ContinuationCompatible ? "observe" : Equals(retryReason, "preconditionChanged") ? "sameCandidateRefresh" : retryReason != null ? "reselectWithinScope" : "stop", "target", new Dictionary<string, object>(target), "routeFamily", family));
        }
    }
}

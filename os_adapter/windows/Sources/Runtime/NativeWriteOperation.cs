using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class NativeWriteOperation
    {
        internal readonly Dictionary<string, object> Original, Intent;
        internal Dictionary<string, object> Selected;
        internal string[] Within;
        bool legacyDispatched;
        internal readonly bool ClickV1;
        internal readonly NativeClickProgress Click;
        // Legacy units retain their original terminal flag. Negotiated clicks
        // distinguish an unresolved call from the system's acceptance evidence.
        internal bool Dispatched
        {
            get
            {
                return legacyDispatched || Click != null && Click.HasRequestedInput;
            }

            set
            {
                legacyDispatched = value;
            }
        }

        internal bool InputTerminated
        {
            get
            {
                return Dispatched || Click != null && (Click.CallInFlight || !Click.ContinuationCompatible) || Cleanup != "complete";
            }
        }

        internal string Cleanup = "complete";
        internal Action<NativePointerPoint, bool, int, bool> DisplayPointer;
        internal void Indicate(NativePointerPoint point, bool pulse = false, int count = 1, bool dragging = false)
        {
            try
            {
                if (DisplayPointer != null)
                    DisplayPointer(point, pulse, count, dragging);
            }
            catch
            {
            }
        }

        internal readonly Dictionary<string, NativeInputContract> Contracts = new Dictionary<string, NativeInputContract>();
        internal NativeWriteOperation(Dictionary<string, object> original, Dictionary<string, object> intent, bool clickV1 = false)
        {
            Original = new Dictionary<string, object>(original);
            Intent = Wire.Obj("kind", intent["kind"], "parameters", new Dictionary<string, object>(Wire.Object(intent["parameters"])));
            ClickV1 = clickV1;
            if (clickV1)
                Click = new NativeClickProgress();
        }

        internal static bool Same(Dictionary<string, object> a, Dictionary<string, object> b)
        {
            return a.Count == b.Count && a.All(p => b.ContainsKey(p.Key) && Equals(p.Value, b[p.Key]));
        }

        internal void Bind(Dictionary<string, object> target, string[] within)
        {
            if (InputTerminated)
                throw new Failure("execution_unknown", "the operation has unresolved input or incompatible preparation; no new contract can be acquired");
            if (!Original.All(p => p.Key == "node" || Equals(p.Value, target[p.Key])) || Original["node"] != null && !Same(Original, target))
                throw new Failure("stale_target", "the input target differs from the original request");
            if (target["node"] == null && within.Length != 0)
                throw new Failure("invalid_request", "a window input target cannot have element ancestors");
            if (within.Contains((string)target["node"]))
                throw new Failure("invalid_request", "ancestor constraints must not include the target itself");
            if (Selected != null && (!Same(Selected, target) || !Within.SequenceEqual(within)))
                throw new Failure("stale_target", "the operation already selected another control or ancestor scope");
            if (Selected == null)
            {
                Selected = new Dictionary<string, object>(target);
                Within = (string[])within.Clone();
            }
        }
    }
}

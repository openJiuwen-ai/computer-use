// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal enum NativeElementHitKind
    {
        NotSampled,
        Target,
        Descendant,
        Ancestor,
        OtherControl,
        OtherBranch,
        OutsideScope,
        NoElement,
        Incomplete,
        QueryFailed
    }

    // A UIA observation is separate from HWND routing and SendInput progress.
    // Only a proven different branch conflicts; coarse/missing provider data
    // retains the existing geometry route with its uncertainty visible.
    internal sealed class NativeElementHit
    {
        internal readonly NativeElementHitKind Kind;
        internal readonly int? Error;
        internal NativeElementHit(NativeElementHitKind kind, int? error = null)
        {
            Kind = kind;
            Error = error;
        }

        internal bool Confirmed
        {
            get
            {
                return Kind == NativeElementHitKind.Target || Kind == NativeElementHitKind.Descendant;
            }
        }

        internal bool Conflicts
        {
            get
            {
                return Kind == NativeElementHitKind.OtherControl;
            }
        }

        internal bool Ready(bool nativeReady, bool requiresConfirmed = false)
        {
            return nativeReady && !Conflicts && (!requiresConfirmed || Confirmed);
        }

        internal sealed class PointChoice
        {
            internal readonly NativePointerPoint Point;
            internal readonly NativeElementHit Hit;
            internal readonly bool RequiresConfirmed;
            internal PointChoice(NativePointerPoint point, NativeElementHit hit, bool requiresConfirmed = false)
            {
                Point = point;
                Hit = hit;
                RequiresConfirmed = requiresConfirmed;
            }
        }

        internal static PointChoice SelectPoint(NativePointerPoint primary, IEnumerable<NativePointerPoint> alternatives, Func<NativePointerPoint, NativeElementHit> query, Func<bool> canContinue)
        {
            var first = new PointChoice(primary, query(primary));
            if (!first.Hit.Conflicts)
                return first;
            foreach (var point in alternatives)
            {
                if (!canContinue())
                    break;
                var hit = query(point);
                // Never turn an unconfirmed new point into another geometry
                // fallback, and never send test clicks to search for an effect.
                if (hit.Confirmed)
                    return new PointChoice(point, hit, true);
            }

            return first;
        }

        internal static IEnumerable<NativePointerPoint> Alternatives(NativePointerPoint primary, IEnumerable<NativeCaptureGeometry> areas)
        {
            var seen = new HashSet<string>
            {
                primary.X + ":" + primary.Y
            };
            int remaining = 9;
            foreach (var b in areas)
            {
                if (b == null)
                    continue;
                int dx = Math.Max(0, Math.Min(4, (b.NativeWidth - 1) / 4)), dy = Math.Max(0, Math.Min(4, (b.NativeHeight - 1) / 4));
                int left = b.X + dx, right = b.X + b.NativeWidth - 1 - dx, top = b.Y + dy, bottom = b.Y + b.NativeHeight - 1 - dy;
                int x = b.X + b.NativeWidth / 2, y = b.Y + b.NativeHeight / 2;
                foreach (var point in new[]
                {
                    new NativePointerPoint(x, y),
                    new NativePointerPoint(left, y),
                    new NativePointerPoint(right, y),
                    new NativePointerPoint(x, top),
                    new NativePointerPoint(x, bottom),
                    new NativePointerPoint(left, top),
                    new NativePointerPoint(right, top),
                    new NativePointerPoint(left, bottom),
                    new NativePointerPoint(right, bottom)
                }

                )
                {
                    if (!seen.Add(point.X + ":" + point.Y))
                        continue;
                    yield return point;
                    if (--remaining == 0)
                        yield break;
                }
            }
        }

        internal static bool IndependentControl(string role)
        {
            switch (role)
            {
                case "ControlType.Button":
                case "ControlType.SplitButton":
                case "ControlType.CheckBox":
                case "ControlType.RadioButton":
                case "ControlType.ComboBox":
                case "ControlType.Edit":
                case "ControlType.ListItem":
                case "ControlType.TreeItem":
                case "ControlType.DataItem":
                case "ControlType.TabItem":
                case "ControlType.MenuItem":
                case "ControlType.Hyperlink":
                case "ControlType.Slider":
                case "ControlType.Spinner":
                case "ControlType.ScrollBar":
                    return true;
                default:
                    return false;
            }
        }

        static NativeElementHit OtherBranch<T>(string common, List<KeyValuePair<string, T>> path, Func<T, bool> independent, Action check)
        {
            foreach (var node in path)
            {
                if (node.Key == common)
                    break;
                check();
                if (independent(node.Value))
                    return new NativeElementHit(NativeElementHitKind.OtherControl);
            }

            // A provider reporting the underlying document/pane for its
            // same-HWND floating UI is not a proven independent receiver.
            return new NativeElementHit(NativeElementHitKind.OtherBranch);
        }

        // Query-only bounded traversal, also exercised with fault providers.
        // Both chains must actually meet before another branch is established.
        // Returning an ancestor is a coarse hit, not evidence of another control.
        internal static NativeElementHit Observe<T>(T target, string targetId, Func<T> fromPoint, Func<T, string> identity, Func<T, T> parent, Func<T, bool> within, Func<T, bool> independent, Action check)
            where T : class
        {
            check();
            var hit = fromPoint();
            check();
            if (hit == null)
                return new NativeElementHit(NativeElementHitKind.NoElement);
            string hitId = identity(hit);
            if (String.IsNullOrEmpty(targetId) || String.IsNullOrEmpty(hitId))
                return new NativeElementHit(NativeElementHitKind.Incomplete);
            if (hitId == targetId)
                return new NativeElementHit(NativeElementHitKind.Target);
            if (!within(hit))
                return new NativeElementHit(NativeElementHitKind.OutsideScope);
            var hitPath = new HashSet<string>
            {
                hitId
            };
            var targetPath = new HashSet<string>
            {
                targetId
            };
            var hitNodes = new List<KeyValuePair<string, T>>
            {
                new KeyValuePair<string, T>(hitId, hit)
            };
            var h = hit;
            var t = target;
            for (int n = 0; n < 64 && (h != null || t != null); n++)
            {
                check();
                if (h != null)
                {
                    h = parent(h);
                    check();
                    if (h != null)
                    {
                        string id = identity(h);
                        if (String.IsNullOrEmpty(id) || !hitPath.Add(id))
                            return new NativeElementHit(NativeElementHitKind.Incomplete);
                        if (id == targetId)
                            return new NativeElementHit(NativeElementHitKind.Descendant);
                        if (targetPath.Contains(id))
                            return OtherBranch(id, hitNodes, independent, check);
                        hitNodes.Add(new KeyValuePair<string, T>(id, h));
                    }
                }

                if (t != null)
                {
                    t = parent(t);
                    check();
                    if (t != null)
                    {
                        string id = identity(t);
                        if (String.IsNullOrEmpty(id) || !targetPath.Add(id))
                            return new NativeElementHit(NativeElementHitKind.Incomplete);
                        if (id == hitId)
                            return new NativeElementHit(NativeElementHitKind.Ancestor);
                        if (hitPath.Contains(id))
                            return OtherBranch(id, hitNodes, independent, check);
                    }
                }
            }

            return new NativeElementHit(NativeElementHitKind.Incomplete);
        }

        internal string SubmissionDetail()
        {
            if (Confirmed)
                return "Pointer click submitted at the original element's point; UIA confirmed the target or its descendant. Read to verify its effect.";
            string reason = Kind == NativeElementHitKind.Ancestor ? "the provider returned only an ancestor" : Kind == NativeElementHitKind.QueryFailed ? "the hit query failed" + (Error.HasValue ? " (0x" + unchecked((uint)Error.Value).ToString("X8") + ")" : "") : Kind == NativeElementHitKind.NoElement ? "the provider returned no element" : Kind == NativeElementHitKind.OutsideScope ? "the reported element's original-window ancestry was not confirmed" : Kind == NativeElementHitKind.OtherBranch ? "the provider returned a different generic branch without a confirmed control receiver" : Kind == NativeElementHitKind.Incomplete ? "the provider's hit ancestry was incomplete" : "UIA hit data was unavailable";
            return "Pointer click submitted within the original element's current visible bounds; UIA element hit unconfirmed: " + reason + ". Native window routing and original element identity/geometry were revalidated. Read to verify the actual effect.";
        }
    }
}

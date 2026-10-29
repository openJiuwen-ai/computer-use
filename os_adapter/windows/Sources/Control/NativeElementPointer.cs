// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    internal sealed partial class NativeWindowsPointer
    {
        static bool SameBox(NativeCaptureGeometry a, NativeCaptureGeometry b)
        {
            return a.X == b.X && a.Y == b.Y && a.NativeWidth == b.NativeWidth && a.NativeHeight == b.NativeHeight;
        }

        NativeCaptureGeometry ElementBounds(NativeNodeRecord node, Request r)
        {
            bool? enabled = NativeUiaBoundary.Property(node.Element, AutomationElement.IsEnabledProperty, r) as bool? ;
            if (enabled != true)
                throw new Failure("input_unavailable", enabled == false ? "the selected element is disabled" : "the selected element's enabled state could not be confirmed");
            bool? offscreen = NativeUiaBoundary.Property(node.Element, AutomationElement.IsOffscreenProperty, r) as bool? ;
            if (offscreen != false)
                throw new Failure("input_unavailable", offscreen == true ? "the selected element is off screen" : "the selected element's on-screen location could not be confirmed");
            object value = NativeUiaBoundary.Property(node.Element, AutomationElement.BoundingRectangleProperty, r);
            if (!(value is Rect))
                throw new Failure("input_unavailable", "the selected element has no current visible bounds");
            var box = (Rect)value;
            if (box.IsEmpty || Double.IsNaN(box.X) || Double.IsNaN(box.Y) || Double.IsInfinity(box.X) || Double.IsInfinity(box.Y) || Double.IsNaN(box.Width) || Double.IsNaN(box.Height) || Double.IsInfinity(box.Width) || Double.IsInfinity(box.Height) || box.Width < 1 || box.Height < 1 || box.Width > 32768 || box.Height > 32768 || Math.Abs(box.X) > 100000 || Math.Abs(box.Y) > 100000)
                throw new Failure("input_unavailable", "the selected element has no finite visible bounds");
            return new NativeCaptureGeometry((int)Math.Ceiling(box.X), (int)Math.Ceiling(box.Y), (int)Math.Floor(box.Width), (int)Math.Floor(box.Height), 1);
        }

        public NativeElementPointerTarget Element(Dictionary<string, object> target, string[] within, Request r)
        {
            var window = new NativeUiaBoundary(windows).Validate(target, within, r);
            var node = windows.Node((string)target["node"], (string)target["surface"], r);
            var bounds = ElementBounds(node, r);
            var rectangle = Native.Bounds(window.Native.Handle);
            var windowBounds = new NativeCaptureGeometry(rectangle.X, rectangle.Y, rectangle.Width, rectangle.Height, 1);
            var layout = new NativePointerLayout();
            // UIA clickable points are physical screen pixels. A provider may
            // decline one; the visible intersection's center remains a candidate
            // only after native routing and the live element boundary checks.
            NativePointerPoint point;
            try
            {
                r.Check();
                if (!node.Element.TryClickablePoint(out point))
                    point = new NativePointerPoint(-100001, -100001);
            }
            catch (COMException)
            {
                r.Check();
                point = new NativePointerPoint(-100001, -100001);
            }

            if (!NativePointerCapture.Contains(bounds, point) || !NativePointerCapture.Contains(windowBounds, point) || !layout.Displays.Any(d => NativePointerCapture.Contains(d, point)))
            {
                var visible = layout.Displays.Select(d => RectangleIntersection(bounds, windowBounds, d)).FirstOrDefault(b => b != null);
                if (visible == null)
                    throw new Failure("input_unavailable", "the selected element has no visible point inside its working window and displays");
                point = new NativePointerPoint(visible.X + visible.NativeWidth / 2, visible.Y + visible.NativeHeight / 2);
            }

            // Resolve a covered provider point before the gesture freezes it.
            // Unknown/coarse hits retain the original candidate; only a known
            // conflict triggers bounded read-only alternatives with exact hits.
            double until = Math.Min(r.Deadline, Wire.Now + 150);
            var choice = NativeElementHit.SelectPoint(point, NativeElementHit.Alternatives(point, layout.Displays.Select(d => RectangleIntersection(bounds, windowBounds, d))), candidate => ElementHit(node, candidate, r), () =>
            {
                r.Check();
                return Wire.Now < until;
            });
            NativeUiaBoundary.SameWindow(window, new NativeUiaBoundary(windows).Validate(target, within, r), r);
            return new NativeElementPointerTarget
            {
                Native = window,
                Point = choice.Point,
                Hit = choice.Hit,
                RequiresConfirmedHit = choice.RequiresConfirmed,
                Bounds = bounds,
                WindowBounds = windowBounds,
                Canvas = layout.Canvas,
                Layout = layout.Signature
            };
        }

        static NativeCaptureGeometry RectangleIntersection(NativeCaptureGeometry a, NativeCaptureGeometry b, NativeCaptureGeometry c)
        {
            int left = Math.Max(a.X, Math.Max(b.X, c.X)), top = Math.Max(a.Y, Math.Max(b.Y, c.Y));
            int right = Math.Min(a.X + a.NativeWidth, Math.Min(b.X + b.NativeWidth, c.X + c.NativeWidth));
            int bottom = Math.Min(a.Y + a.NativeHeight, Math.Min(b.Y + b.NativeHeight, c.Y + c.NativeHeight));
            return right <= left || bottom <= top ? null : new NativeCaptureGeometry(left, top, right - left, bottom - top, 1);
        }

        NativeElementHit ElementHit(NativeNodeRecord node, NativePointerPoint point, Request r)
        {
            try
            {
                return NativeElementHit.Observe(node.Element, node.Runtime, () => UiaElement.FromPoint(point), NativeReadStore.Runtime, UiaClient.Parent, value => windows.Within(value, node.Window, r), value =>
                {
                    var role = NativeUiaBoundary.Property(value, AutomationElement.ControlTypeProperty, r) as ControlType;
                    return role != null && NativeElementHit.IndependentControl(role.ProgrammaticName);
                }, r.Check);
            }
            catch (ElementNotAvailableException error)
            {
                // This is the hit-query object, not proof that the retained
                // target died. Its independent boundary checks still apply.
                r.Check();
                return new NativeElementHit(NativeElementHitKind.QueryFailed, error.HResult);
            }
            catch (COMException error)
            {
                r.Check();
                return new NativeElementHit(NativeElementHitKind.QueryFailed, error.HResult);
            }
        }

        IntPtr PointerWindow(NativeNodeRecord node, NativeWindowRecord interaction, IntPtr hit, NativePointerPoint point, Request r)
        {
            IntPtr root = interaction.Native.Handle;
            if (Native.Inside(root, hit))
                return root;
            // Native dropdowns may have their own HWND and mouse capture. They
            // are usable here only for an observed element whose live UIA
            // ancestry remains in the original scope and whose hit window has
            // a proven owner chain to that scope. A provider can omit the popup
            // HWND from its parent chain; the exact live UIA hit still has to
            // match the selected element. Same PID is not enough.
            IntPtr recipient = Native.GetAncestor(hit, 2);
            if (recipient == IntPtr.Zero || !Native.Inside(recipient, hit))
                return IntPtr.Zero;
            if (Native.GetWindow(recipient, 4) == IntPtr.Zero)
            {
                // Some providers expose the popup items as logical descendants
                // of the original control but omit a Win32 owner. Require the
                // exact UIA hit, its independently checked original ancestry,
                // and the native host of that UIA provider together. PID alone
                // or a popup title cannot establish this relation.
                object process = NativeUiaBoundary.Property(node.Element, AutomationElement.ProcessIdProperty, r);
                if (process is int && (uint)(int)process == Native.PID(recipient) && ElementHit(node, point, r).Confirmed)
                    return recipient;
            }

            var seen = new HashSet<IntPtr>();
            var owner = recipient;
            for (int n = 0; n < 64 && owner != IntPtr.Zero && seen.Add(owner); n++)
            {
                r.Check();
                owner = Native.GetWindow(owner, 4);
                if (owner == root)
                    return ElementHit(node, point, r).Confirmed ? recipient : IntPtr.Zero;
            }

            return IntPtr.Zero;
        }

        bool NativeRoute(NativeNodeRecord node, NativeWindowRecord interaction, NativePointerPoint point, Request r, out bool active, out bool foregroundReady)
        {
            IntPtr main = interaction.Native.Handle;
            IntPtr recipient = PointerWindow(node, interaction, Native.WindowFromPoint(new Native.POINT { X = point.X, Y = point.Y }), point, r);
            IntPtr foreground = Native.GetForegroundWindow();
            active = foreground == main || recipient != IntPtr.Zero && foreground == recipient;
            foregroundReady = active || Native.PointerForeground(main, foreground);
            var gui = new Native.GUI
            {
                Size = Marshal.SizeOf(typeof(Native.GUI))
            };
            bool capture = Native.GetGUIThreadInfo(0, ref gui) && (gui.Capture == IntPtr.Zero || recipient != IntPtr.Zero && (Native.Inside(recipient, gui.Capture) || Native.Inside(main, gui.Capture)));
            return recipient != IntPtr.Zero && foregroundReady && capture;
        }

        public NativePointerState ReadElement(Dictionary<string, object> target, string[] within, NativeElementPointerTarget selected, Request r)
        {
            var boundary = new NativeUiaBoundary(windows);
            var window = boundary.Validate(target, within, r);
            NativeUiaBoundary.SameWindow((NativeWindowRecord)selected.Native, window, r);
            var node = windows.Node((string)target["node"], (string)target["surface"], r);
            var rectangle = Native.Bounds(window.Native.Handle);
            if (!SameBox(ElementBounds(node, r), selected.Bounds) || !SameBox(new NativeCaptureGeometry(rectangle.X, rectangle.Y, rectangle.Width, rectangle.Height, 1), selected.WindowBounds) || new NativePointerLayout().Signature != selected.Layout)
                throw new Failure("input_unavailable", "the selected element or window moved during pointer preparation; no remaining click was sent");
            IntPtr handle = window.Native.Handle;
            bool active, foregroundReady;
            bool nativeReady = NativeRoute(node, window, selected.Point, r, out active, out foregroundReady);
            selected.Hit = nativeReady ? ElementHit(node, selected.Point, r) : new NativeElementHit(NativeElementHitKind.NotSampled);
            // Some providers hit-test the underlying page instead of their
            // same-HWND floating controls. Physical clicks use the live visible
            // geometry and native window route; an incomplete UIA hit is kept
            // as uncertainty in the receipt. A foreign HWND still requires the
            // explicit popup relation checks in PointerWindow, never PID alone.
            // A confirmed same-window conflict is not missing provider data.
            bool ready = selected.Hit.Ready(nativeReady, selected.RequiresConfirmedHit);
            NativeUiaBoundary.SameWindow(window, boundary.Validate(target, within, r), r);
            windows.Node(node.Id, node.Surface, r);
            rectangle = Native.Bounds(handle);
            if (!SameBox(ElementBounds(node, r), selected.Bounds) || !SameBox(new NativeCaptureGeometry(rectangle.X, rectangle.Y, rectangle.Width, rectangle.Height, 1), selected.WindowBounds))
                throw new Failure("input_unavailable", "the selected element or window moved during final pointer validation; no remaining click was sent");
            // Provider callbacks can change the foreground/capture after the
            // UIA hit query. Verify native routing last, immediately before SendInput.
            // Refresh foreground readiness independently even when an earlier
            // hit was blocked; it decides whether activation could help.
            bool finalRoute = NativeRoute(node, window, selected.Point, r, out active, out foregroundReady);
            ready = ready && finalRoute && Native.IsWindowVisible(handle) && !Native.IsIconic(handle);
            if (ready)
            {
                NativeInputAccess.RequireForeground();
                NativeInputAccess.RequireWindow(handle);
            }

            r.Check();
            return new NativePointerState
            {
                Ready = ready,
                Foreground = active,
                ForegroundReady = foregroundReady,
                Native = new Target
                {
                    Binding = new Dictionary<string, object>(target),
                    Within = (string[])within.Clone(),
                    Interaction = window,
                    Point = selected.Point,
                    Kind = "click"
                }
            };
        }
    }
}

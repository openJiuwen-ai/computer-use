// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // The real objects, native samples, and final scope check remain together
    // on the caller's native execution thread. This adapter never dispatches.
    internal sealed class NativeReadDiscoveryHost : INativeReadDiscoveryHost
    {
        readonly NativeReadStore store;
        readonly NativeNodeRecord source;
        internal NativeReadDiscoveryHost(NativeReadStore store, NativeNodeRecord source)
        {
            this.store = store;
            this.source = source;
        }
        public IntPtr SelectedWindow
        {
            get {
                return source.Window.Native.Handle;
            }
        }
        public bool WindowRoot
        {
            get {
                return source.Id == source.Window.RootNode;
            }
        }
        public bool Outside
        {
            get {
                return source.Outside;
            }
        }
        public void Validate(Request request)
        {
            request.Check();
            source.Window.Native.Validate();
        }
        public IntPtr Foreground(Request request)
        {
            Validate(request);
            return Native.GetForegroundWindow();
        }
        public NativeReadDiscoveryStamp Pointer(Request request)
        {
            Validate(request);
            Native.POINT point;
            if (!Native.GetCursorPos(out point))
                return null;
            IntPtr hit = Native.WindowFromPoint(point);
            if (!Native.Inside(SelectedWindow, hit) || !Native.IsWindow(hit))
                return null;
            var stamp = new NativeReadDiscoveryStamp { X = point.X, Y = point.Y, Hit = hit, HitPid = Native.PID(hit),
                                                       HitGeneration = Native.Generation(hit) };
            if (stamp.HitPid == 0)
                return null;
            Validate(request);
            return stamp;
        }
        public object Query(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp, int milliseconds,
                            Request request)
        {
            Validate(request);
            using (UiaClient.TransactionBudget(milliseconds))
            {
                object candidate = kind == NativeReadDiscoveryKind.Focus
                                       ? UiaElement.FocusedElement
                                       : UiaElement.FromPoint(new NativePointerPoint(stamp.X, stamp.Y));
                Validate(request);
                return candidate;
            }
        }
        public NativeReadDiscoveryRetention Retain(object element, Action validateStable, Request request)
        {
            Validate(request);
            string id = store.KeepObservedCandidate((UiaElement)element, source, request, validateStable);
            var node = store.ReadNode(id, source.Surface, request);
            if (node.Outside)
                return new NativeReadDiscoveryRetention(
                    null, "unsupported",
                    "UIA returned an outside-window object; it was not added to the selected window's discovery relations.");
            return new NativeReadDiscoveryRetention(
                id, "complete",
                "Passive discovery observed this object; " +
                    (node.ObservationOnly
                         ? "its uncertain ownership is read-only and cannot authorize input."
                         : "any input still requires independent live identity and ownership validation."));
        }
    }
}

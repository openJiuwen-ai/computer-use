// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal sealed class NativeReadDiscoveryProvider : INativeReadDiscoveryProvider
    {
        readonly INativeReadDiscoveryHost host;
        readonly Action check;
        readonly Func<double> remaining;
        readonly Action<string> trace;
        internal NativeReadDiscoveryProvider(NativeReadStore store, NativeNodeRecord source, Action check,
                                             Func<double> remaining, Action<string> trace = null)
            : this(new NativeReadDiscoveryHost(store, source), check, remaining, trace)
        {
        }
        internal NativeReadDiscoveryProvider(INativeReadDiscoveryHost host, Action check, Func<double> remaining,
                                             Action<string> trace = null)
        {
            this.host = host;
            this.check = check;
            this.remaining = remaining;
            this.trace = trace;
        }
        void Check(Request request)
        {
            request.Check();
            check();
            host.Validate(request);
        }
        static bool ValidPoint(NativeReadDiscoveryStamp point)
        {
            return point != null && point.Hit != IntPtr.Zero && point.HitPid != 0;
        }
        public NativeReadDiscoveryGate Capture(NativeReadDiscoveryKind kind, Request request)
        {
            Check(request);
            if (!host.WindowRoot)
                return new NativeReadDiscoveryGate(
                    null, "unsupported", "Window-wide passive discovery does not expand an element subtree read.");
            if (host.Outside)
                return new NativeReadDiscoveryGate(null, "unsupported",
                                                   "Discovery does not expand an outside-window observation.");
            if (kind == NativeReadDiscoveryKind.Focus)
            {
                IntPtr front = host.Foreground(request);
                if (front != host.SelectedWindow)
                    return new NativeReadDiscoveryGate(
                        null, "unsupported", "The selected window is not foreground; global focus was not queried.");
                return new NativeReadDiscoveryGate(new NativeReadDiscoveryStamp { Foreground = front });
            }
            var point = host.Pointer(request);
            return !ValidPoint(point)
                       ? new NativeReadDiscoveryGate(
                             null, "unsupported",
                             "The current pointer does not hit the selected native window; no point query was made.")
                       : new NativeReadDiscoveryGate(point);
        }
        public object Query(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp, Request request)
        {
            Check(request);
            if (!Stable(kind, stamp, request))
                throw new NativeFieldUnavailable("error", "The discovery scope changed before the UIA query.");
            int milliseconds = (int)Math.Min(150, remaining());
            if (milliseconds < UiaClient.MinimumTransactionMs)
                throw new NativeFieldUnavailable(
                    "timeout", "The remaining observation slice cannot start another supported UIA transaction.");
            if (trace != null)
                trace(kind == NativeReadDiscoveryKind.Focus ? "GetFocusedElement before window collection"
                                                            : "ElementFromPoint at the unchanged current pointer");
            object candidate = host.Query(kind, stamp, milliseconds, request);
            Check(request);
            return candidate;
        }
        public bool Stable(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp, Request request)
        {
            Check(request);
            if (kind == NativeReadDiscoveryKind.Focus)
                return stamp.Foreground == host.SelectedWindow && host.Foreground(request) == stamp.Foreground;
            var current = host.Pointer(request);
            return ValidPoint(current) && current.X == stamp.X && current.Y == stamp.Y && current.Hit == stamp.Hit &&
                   current.HitPid == stamp.HitPid && current.HitGeneration == stamp.HitGeneration;
        }
        public NativeReadDiscoveryRetention Retain(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp,
                                                   object element, Request request)
        {
            Check(request);
            if (!Stable(kind, stamp, request))
                return new NativeReadDiscoveryRetention(
                    null, "error", "The discovery scope changed before retention; no candidate was retained.");
            var retained = host.Retain(
                element,
                () =>
                {
                    if (!Stable(kind, stamp, request))
                        throw new NativeFieldUnavailable(
                            "error",
                            "The discovery scope changed during ownership queries; no candidate was retained.");
                },
                request);
            if (!Stable(kind, stamp, request))
                return new NativeReadDiscoveryRetention(
                    null, "error",
                    "The discovery scope changed during retention; no candidate reference was delivered.");
            return retained;
        }
    }
}
